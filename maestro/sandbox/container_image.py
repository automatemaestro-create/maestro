"""The image of a project, built by the container broker from a Dockerfile of its workspace (#1726).

An agent names a Dockerfile of its session's workspace instead of an image
(`maestro-container --dockerfile PATH COMMAND...`). The Dockerfile is the agent's: a
datum the broker judges, never a permission. Maestro builds the image itself, at the
person's level, and passes BuildKit no entitlement (`network.host`,
`security.insecure`), no secret and no ssh agent. It does so in two steps
(`maestro.sandbox.container_broker` plays them):

1. **The context.** Docker exports, as a tar, what the agent's ignore file keeps of the
   workspace, the Dockerfile included, and nothing of what the scope excludes. The
   ignore file docker reads for it is Maestro's (`context_ignore`): the agent's
   patterns, then the Dockerfile, then the masks, last so that they win. This step
   builds Maestro's own Dockerfile (`CONTEXT_DOCKERFILE`) on the engine's built-in
   frontend, and docker's client sends a link or a junction as a link, never what it
   points to. The tar is then read (`inspect_context`): a masked path found in it
   refuses the build.
2. **The image**, from that tar on stdin. A tar context is the context, verbatim: no
   ignore file applies to it, so a frontend that a `# syntax=` directive pulls reads
   that tar, and nothing else of the workspace. The Dockerfile is in it, at its path,
   even where the agent's ignore file leaves it out: a tar context carries the
   Dockerfile it builds.

**The tag is the fingerprint of that tar**: every entry's path, type, mode and content,
the Dockerfile's among them. A second request on the same content finds its image and
builds nothing. **Each project has its repository** (`repository`), and building a new
image removes the other tags of that repository, compared exactly with the current one
(`stale`), the parti pris of #463: the engine's disk never shrinks (docs/10 §8.4), so
what protects it is not letting images pile up.

**The cache mounts** of a build are namespaced by its repository
(`BUILDKIT_CACHE_MOUNT_NS`): a `RUN --mount=type=cache` of the agent shares no cache
with a build of the person, nor with another project's. A third-party frontend is not
bound by that namespace (docs/17 §7).
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from maestro.sandbox import container

#: The prefix of a project's image repository: `maestro-broker/<project>`.
REPOSITORY_PREFIX = "maestro-broker"

#: Maestro's Dockerfile for the first step: the context, copied as it is sent.
CONTEXT_DOCKERFILE = "FROM scratch\nCOPY . /\n"

#: The names of the first step's files, in the broker's own folder.
CONTEXT_DOCKERFILE_NAME = "context.Dockerfile"
CONTEXT_TAR_NAME = "context.tar"

#: The suffix docker gives the ignore file of a Dockerfile, and the name of a context's.
IGNORE_SUFFIX = ".dockerignore"

#: The largest Dockerfile, and the largest ignore file, the broker reads.
DOCKERFILE_MAX_BYTES = 1 << 20
IGNORE_MAX_BYTES = 1 << 20

#: How many hex digits of the fingerprint the tag keeps, as the image of the CI's filet.
TAG_LENGTH = 12

#: The longest Dockerfile path a request may name.
_PATH_MAX = 4096

#: The build argument that namespaces cache mounts, read by docker's Dockerfile frontend.
CACHE_NAMESPACE_ARG = "BUILDKIT_CACHE_MOUNT_NS"

#: What the fingerprint starts with: a change of what it covers changes every tag.
_SCHEME = b"maestro-broker-context/1\n"

#: A path component of an image repository, on docker's grammar.
_COMPONENT = re.compile(r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*")
_COMPONENT_MAX = 64

#: What an ignore file reads as a pattern, and what it trims from a line.
_PATTERN_CHARACTERS = frozenset("*?[]\\")


class ImageRefusal(Exception):
    """A Dockerfile the broker does not build; its text says why, to the agent."""


def repository(key: str) -> str:
    """The image repository of a project (or of a session without one), named by `key`.

    A key that is already a component of docker's grammar is kept, so the person reads
    the project in `docker images`; another is reduced to one, with a digest of the key
    so that two keys never share a repository.
    """
    if len(key) <= _COMPONENT_MAX and _COMPONENT.fullmatch(key):
        return f"{REPOSITORY_PREFIX}/{key}"
    slug = "-".join(re.findall(r"[a-z0-9]+", key.lower()))[:40] or "project"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return f"{REPOSITORY_PREFIX}/{slug}-{digest}"


def dockerfile_path(workspace: Path, requested: object) -> str:
    """The Dockerfile's path relative to `workspace`, in POSIX form, or the refusal it gets.

    The program sends an absolute path, resolved against the agent's working
    directory. The comparison is lexical: a link the path crosses is followed neither
    here nor by docker's client, so a Dockerfile behind a link is not in the context,
    and is refused there (`inspect_context`).
    """
    if (
        not isinstance(requested, str)
        or not requested
        or "\0" in requested
        or len(requested) > _PATH_MAX
        or not os.path.isabs(requested)
    ):
        raise ImageRefusal(
            f"{requested!r} ne nomme pas un Dockerfile : nomme-le par son chemin, dans ton "
            f"espace de travail ({workspace})."
        )
    base = os.path.normpath(os.path.abspath(workspace))
    target = os.path.normpath(requested)
    inside = os.path.normcase(target).startswith(os.path.normcase(base).rstrip(os.sep) + os.sep)
    if not inside:
        raise ImageRefusal(
            f"{requested} n'est pas dans ton espace de travail ({workspace}) : le courtier ne "
            "construit qu'un Dockerfile de ton espace."
        )
    relative = target[len(base.rstrip(os.sep)) + 1 :].replace(os.sep, "/")
    if not _literal(relative):
        raise ImageRefusal(
            f"`{relative}` porte un caractère que docker lit comme un motif (`*`, `?`, `[`, "
            "`]`, `\\`, un `!` ou un `#` en tête, une espace au bord) : renomme le Dockerfile."
        )
    return relative


def masks(environ: Mapping[str, str]) -> list[str]:
    """What stays out of the context: the scope's exclusions, as the container's masks.

    `environ` is the protocol `protocole_sandbox` composed for the session, the same
    the container is played on. For a versioned project, the workspace's `.git` is out
    too: it points into the project's root, that no container sees.
    """
    entries = (environ.get(container.ENV_MASQUES) or "").split("\n")
    paths = [entry.partition(":")[2] for entry in entries if entry.strip()]
    paths = [path for path in paths if path]
    if (environ.get(container.ENV_DEPOT) or "").strip() and ".git" not in paths:
        paths.append(".git")
    return paths


def refuse_masked(dockerfile: str, excluded: Sequence[str]) -> None:
    """Refuses a Dockerfile the scope excludes: it would enter the context it is kept from."""
    if _masked(dockerfile, excluded):
        raise ImageRefusal(
            f"`{dockerfile}` est exclu par le périmètre du projet : il n'entre pas dans le "
            "contexte d'une construction."
        )


def agent_ignore(workspace: Path, dockerfile: str) -> bytes:
    """The ignore file docker would read for `dockerfile`, as its bytes, or nothing.

    Docker's choice, read without leaving the workspace (`read_inside`): the file
    named after the Dockerfile, next to it (`<Dockerfile>.dockerignore`), else the
    workspace's `.dockerignore`.
    """
    for relative in (dockerfile + IGNORE_SUFFIX, IGNORE_SUFFIX):
        data = read_inside(workspace, relative, IGNORE_MAX_BYTES)
        if data is not None:
            return data
    return b""


def context_ignore(agent: bytes, dockerfile: str, excluded: Sequence[str]) -> bytes:
    """The ignore file of the first step: the agent's patterns, the Dockerfile, the masks.

    The last pattern that matches a path decides, so the masks come last: an exception
    of the agent (`!.env`) does not bring back what the scope excludes. A mask docker
    would read as a pattern is refused rather than written approximately.
    """
    for path in excluded:
        if not _literal(path):
            raise ImageRefusal(
                f"`{path}`, que le périmètre exclut, porte un caractère que docker lit comme "
                "un motif : Maestro ne sait pas l'écarter du contexte, et ne construit pas."
            )
    head = agent if not agent or agent.endswith(b"\n") else agent + b"\n"
    tail = [
        "# Maestro (#1726) : le Dockerfile entre au contexte, ce que le périmètre exclut non.",
        f"!{dockerfile}",
        *excluded,
    ]
    return head + "".join(f"{line}\n" for line in tail).encode("utf-8")


def context_argv(folder: Path, workspace: Path) -> list[str]:
    """The first step: the context of `workspace`, exported as a tar into `folder`."""
    tar = folder / CONTEXT_TAR_NAME
    return [
        "docker",
        "buildx",
        "build",
        "--progress",
        "plain",
        "--file",
        str(folder / CONTEXT_DOCKERFILE_NAME),
        "--output",
        ",".join(_csv(field) for field in ("type=tar", f"dest={tar}")),
        str(workspace),
    ]


def inspect_context(tar: Path, dockerfile: str, excluded: Sequence[str]) -> str:
    """The fingerprint of the exported context, or the refusal it gets.

    Refused: a masked path in the tar (a leak the first step should never let through),
    and a Dockerfile that is not a file of the tar — absent, behind a link, or too large.
    """
    digest = hashlib.sha256(_SCHEME)
    size: int | None = None
    with tarfile.open(tar) as archive:
        members = sorted(archive.getmembers(), key=lambda member: _name(member.name))
        for member in members:
            name = _name(member.name)
            if not name:
                continue
            if _masked(name, excluded):
                raise ImageRefusal(
                    f"le contexte porterait `{name}`, que le périmètre exclut : la "
                    "construction est refusée."
                )
            digest.update(_entry(archive, member, name))
            if name == dockerfile and member.isfile():
                size = member.size
    if size is None:
        raise ImageRefusal(
            f"`{dockerfile}` n'est pas un fichier que docker voit dans ton espace : il est "
            "absent, ou atteint par un lien, que docker ne suit pas."
        )
    if size > DOCKERFILE_MAX_BYTES:
        raise ImageRefusal(f"`{dockerfile}` dépasse {DOCKERFILE_MAX_BYTES} octets.")
    return digest.hexdigest()


def tag(repository_name: str, fingerprint: str) -> str:
    """The tag of the image whose context has `fingerprint`."""
    return f"{repository_name}:{fingerprint[:TAG_LENGTH]}"


def build_argv(image: str, dockerfile: str, namespace: str) -> list[str]:
    """The second step: the image, built from the context tar that stdin carries.

    Nothing more is passed: no `--allow` (`network.host`, `security.insecure`), no
    `--secret`, no `--ssh`, no `--network`, no build argument of the agent's.
    """
    return [
        "docker",
        "buildx",
        "build",
        "--progress",
        "plain",
        "--load",
        "--tag",
        image,
        "--file",
        dockerfile,
        "--build-arg",
        f"{CACHE_NAMESPACE_ARG}={namespace}",
        "-",
    ]


def listing_argv(repository_name: str) -> list[str]:
    """The tags of a repository, one per line: `docker images` lists nothing else."""
    return ["docker", "images", repository_name, "--format", "{{.Repository}}:{{.Tag}}"]


def stale(listing: str, repository_name: str, current: str | None) -> list[str]:
    """The tags of `listing` to remove: those of `repository_name`, but `current`.

    The current image is kept by exact comparison, never by date: the most recent and
    the one whose content is current part ways as soon as a branch goes back. A line
    of another repository is never kept for removal, whatever docker listed.
    """
    found: list[str] = []
    for line in listing.splitlines():
        reference = line.strip()
        if not reference.startswith(repository_name + ":") or reference == current:
            continue
        if reference.endswith(":<none>"):
            continue
        found.append(reference)
    return found


def read_inside(workspace: Path, relative: str, limit: int) -> bytes | None:
    """The bytes of `relative` in `workspace`, read without leaving it; `None` when absent.

    The broker reads at the person's level, in a folder the agent writes: a link or a
    junction there would have it read, for the agent, what the agent cannot read. The
    file is therefore opened first, and the final path of the open handle must be
    inside the workspace; nothing can change between that check and the read.
    """
    path = Path(workspace).joinpath(*relative.split("/"))
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise ImageRefusal(f"`{relative}` n'a pas pu être lu ({exc.strerror}).") from None
    with os.fdopen(descriptor, "rb") as handle:
        final = _final_path(handle.fileno(), path)
        if not _within(final, Path(os.path.realpath(workspace))):
            raise ImageRefusal(
                f"`{relative}` mène hors de ton espace de travail : le courtier ne le lit pas."
            )
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ImageRefusal(f"`{relative}` n'est pas un fichier.")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ImageRefusal(f"`{relative}` dépasse {limit} octets.")
    return data


def _literal(path: str) -> bool:
    """Is `path` read by docker's ignore file as itself, and not as a pattern?"""
    return (
        bool(path)
        and path == path.strip()
        and not path.startswith(("!", "#"))
        and not _PATTERN_CHARACTERS & set(path)
    )


def _masked(name: str, excluded: Iterable[str]) -> bool:
    return any(name == path or name.startswith(path + "/") for path in excluded)


def _name(raw: str) -> str:
    """A tar entry's path, as the context names it: no leading `./` or `/`, no trailing `/`."""
    name = raw.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.strip("/")


def _entry(archive: tarfile.TarFile, member: tarfile.TarInfo, name: str) -> bytes:
    """What the fingerprint covers of an entry: its path, type, mode, link and content."""
    content = hashlib.sha256()
    if member.isfile():
        stream = archive.extractfile(member)
        if stream is not None:
            with stream:
                for chunk in iter(lambda: stream.read(1 << 16), b""):
                    content.update(chunk)
    fields = (
        name,
        member.type.decode("ascii", "replace"),
        oct(member.mode & 0o7777),
        member.linkname,
        content.hexdigest(),
    )
    return "\0".join(fields).encode("utf-8") + b"\n"


def _csv(field: str) -> str:
    """A field of docker's `--output`, which it reads as CSV."""
    if any(character in field for character in ',"\n'):
        return '"' + field.replace('"', '""') + '"'
    return field


def _within(path: Path, base: Path) -> bool:
    target = os.path.normcase(str(path))
    root = os.path.normcase(str(base)).rstrip(os.sep)
    return target == root or target.startswith(root + os.sep)


def _final_path(descriptor: int, path: Path) -> Path:
    """Where the open `descriptor` really is: read on the handle, not on its name."""
    if sys.platform == "win32":
        return _windows_final_path(descriptor)
    proc = Path(f"/proc/self/fd/{descriptor}")
    if proc.exists():
        return Path(os.readlink(proc))
    # Elsewhere the broker is not offered (docs/17 §7): the name, resolved, is the answer.
    return Path(os.path.realpath(path))


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _kernel32.GetFinalPathNameByHandleW.restype = wintypes.DWORD

    def _windows_final_path(descriptor: int) -> Path:
        handle = msvcrt.get_osfhandle(descriptor)
        size = 512
        while True:
            buffer = ctypes.create_unicode_buffer(size)
            length = _kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0)
            if length == 0:
                raise ctypes.WinError(ctypes.get_last_error())
            if length < size:
                break
            size = length + 1
        text = buffer.value
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[len("\\\\?\\UNC\\") :]
        elif text.startswith("\\\\?\\"):
            text = text[len("\\\\?\\") :]
        return Path(text)


__all__ = [
    "CACHE_NAMESPACE_ARG",
    "CONTEXT_DOCKERFILE",
    "CONTEXT_DOCKERFILE_NAME",
    "CONTEXT_TAR_NAME",
    "DOCKERFILE_MAX_BYTES",
    "IGNORE_SUFFIX",
    "REPOSITORY_PREFIX",
    "TAG_LENGTH",
    "ImageRefusal",
    "agent_ignore",
    "build_argv",
    "context_argv",
    "context_ignore",
    "dockerfile_path",
    "inspect_context",
    "listing_argv",
    "masks",
    "read_inside",
    "refuse_masked",
    "repository",
    "stale",
    "tag",
]
