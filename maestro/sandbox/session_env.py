"""The environment an agent's session receives: named variables only (#1312).

An agent's session — the CLI and everything it runs — starts from the workstation's
environment. Left as it is, that environment reaches the agent whole: the person's
tokens and keys, Maestro's own settings, and the variables that reconfigure the
provider's CLI (`CLAUDE_CODE_*`, `ANTHROPIC_BASE_URL`…). Such a variable changes
what the session does **without a word**: it is in nobody's settings, and nothing
in the run says it was there.

**The rule: an ambient variable enters a session only when it has been named.**
Three sources name a variable:

- the **base** (`BASE_NAMES`): what a process needs to start, find its programs,
  write its temporary files, speak the person's language and reach the network
  through the workstation's proxy and certificates. Nothing in it configures a
  provider or carries a secret of Maestro's;
- the **provider**, which names what it sets itself — its connection, its CLI's
  folder, the identity of the agent's commits — and the variables of its own
  namespace that Maestro's `.env` files declare (`maestro.providers.claude`);
- the **person**, who names the others in `MAESTRO_AGENT_ENV` (`parse_names`): a
  `JAVA_HOME` the agent's build needs, a `GRADLE_*` family.

Everything else is **withheld**. That is a guard, not a restraint (docs/41): it
closes a channel nobody opened on purpose, and opening it takes one name.

## How it is applied

The SDK builds the CLI's environment itself, from this process's environment, its
own variables and the provider's `env` (`ClaudeAgentOptions.env`), and offers no way
to leave a variable out. The seam is the confinement launcher (`cli_path`,
`maestro.sandbox.confinement`), which starts the CLI with an environment it
rebuilds. But the launcher only sees the merge: a variable that came from the
workstation and one the SDK set under the same name look alike.

They are told apart by their **value**. The provider hands the launcher, for each
ambient variable it withholds, a fingerprint of its value (`withheld`) — never the
value. The launcher removes a variable whose value still has that fingerprint: it
arrived unchanged from the workstation. A variable whose value changed on the way
was set by someone in between, the SDK or the provider, and stays. This is how a
session keeps what its protocol needs without Maestro ever naming the SDK's
internal variables (#1315, docs/44).

What this module does not cover, and says: the text paths of a provider
(`generate`) run no launcher and receive the workstation's environment; they expose
no tool, so no agent's gesture can read it. A session the launcher does not confine
(`maestro.sandbox.confinement.ReleveConfinement.non_confinee`) is not filtered
either, and its journal says it was not confined. The container (docs/17) receives
only the variables `maestro.sandbox.container.ENV_TRANSMISES` names: it was a list
of names from the start.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping

#: The variables every session receives, by name. Grouped by what they are for:
#: a process that lacks one of them fails in ways that say nothing of the cause.
BASE_NAMES: frozenset[str] = frozenset(
    {
        # Finding programs, the system and the person's folders — Windows.
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PSMODULEPATH",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "ALLUSERSPROFILE",
        "PUBLIC",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "COMMONPROGRAMFILES",
        "COMMONPROGRAMFILES(X86)",
        "COMMONPROGRAMW6432",
        "DRIVERDATA",
        # Who and where — Windows.
        "USERNAME",
        "USERDOMAIN",
        "USERDOMAIN_ROAMINGPROFILE",
        "COMPUTERNAME",
        "LOGONSERVER",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "PROCESSOR_LEVEL",
        "PROCESSOR_REVISION",
        "NUMBER_OF_PROCESSORS",
        # Finding programs and the person's folders — POSIX, and Git Bash's `HOME`.
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        # Temporary files.
        "TEMP",
        "TMP",
        "TMPDIR",
        # The person's language and time.
        "LANG",
        "LANGUAGE",
        "LC_*",
        "TZ",
        # Reaching the network the way the workstation does: behind a proxy, a
        # session that loses it cannot reach its model at all.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "NODE_EXTRA_CA_CERTS",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    }
)

#: The length of a fingerprint, in hexadecimal digits: enough that two values do not
#: collide by chance, too short to be worth anything to whoever reads it.
FINGERPRINT_LENGTH = 16


def parse_names(raw: str | None) -> tuple[str, ...]:
    """The names the person lists in `MAESTRO_AGENT_ENV`: comma-separated, `*` for a family.

    Blanks around a name are dropped, and so are empty entries. A name is taken as
    written: `GRADLE_*` names every variable that starts with `GRADLE_`.
    """
    if not raw:
        return ()
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def _key(name: str) -> str:
    """The name as the system compares it: Windows ignores case, POSIX does not."""
    return name.upper() if sys.platform == "win32" else name


def is_named(name: str, patterns: Iterable[str]) -> bool:
    """Does one of `patterns` name `name`? A pattern is a name, or a prefix ending in `*`."""
    key = _key(name)
    return any(fnmatch.fnmatchcase(key, _key(pattern)) for pattern in patterns)


def fingerprint(value: str) -> str:
    """What identifies a value without revealing it."""
    digest = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


def withheld(environ: Mapping[str, str], named: Iterable[str]) -> dict[str, str]:
    """The ambient variables no pattern of `named` names, each with its fingerprint.

    `environ` is the environment the session starts from — this process's. `named`
    are names and families (`BASE_NAMES`, the provider's, the person's).
    """
    patterns = tuple(named)
    return {
        name: fingerprint(value)
        for name, value in environ.items()
        if not is_named(name, patterns)
    }


def encode(fingerprints: Mapping[str, str]) -> str:
    """`withheld` as the launcher receives it: one JSON object."""
    return json.dumps(dict(sorted(fingerprints.items())), ensure_ascii=True)


def decode(raw: str) -> dict[str, str]:
    """What `encode` wrote.

    Raises `ValueError` on anything else: a filter half read is no filter.
    """
    try:
        decoded = json.loads(raw)
    except ValueError as exc:
        raise ValueError(
            f"unreadable withheld variables (a JSON object is expected): {exc}"
        ) from exc
    if not isinstance(decoded, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in decoded.items()
    ):
        raise ValueError("withheld variables must be a JSON object of strings")
    return decoded


def without_withheld(environ: Mapping[str, str], fingerprints: Mapping[str, str]) -> dict[str, str]:
    """`environ` without the variables that arrived unchanged from the workstation.

    A variable is removed when `fingerprints` names it **and** its value still has
    that fingerprint. One whose value changed was set on the way, by the SDK or the
    provider, and is kept.
    """
    by_key = {_key(name): print_ for name, print_ in fingerprints.items()}
    return {
        name: value
        for name, value in environ.items()
        if by_key.get(_key(name)) != fingerprint(value)
    }
