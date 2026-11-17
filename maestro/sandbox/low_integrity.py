"""A process born at low integrity, and the folders it may write (#1515).

The agent runs under the person's account. The scope (`maestro.portee`) reads the
text of a command, and a script that the command launches says nothing in that
text: what bounds it is the operating system. On Windows, the mechanism chosen on
evidence by #1514 is the **low integrity level** (docs/48 §2.1.1):

- the agent's CLI is born under a duplicate of the person's token, lowered to low
  integrity (`spawn_suspended`). A low process cannot open a process of the person
  to stop it — `taskkill /IM` does not even see them —, nor write a file, a folder
  or a registry key that does not carry a low label: every object refuses writes
  from below by default;
- what the agent must write receives an inheritable low label (`label_low`): its
  workspace, the shared Git directory of a versioned project, its workbench;
- what belongs to Maestro and that the agent must not read receives a medium label
  that also refuses reads from below (`refuse_read_up`): the API token, the
  secrets vault.

Setting a label needs `WRITE_OWNER` on the object, and "Modify" does not grant it.
On a disk where the person only has "Modify" (measured on `E:`, docs/48 §2.1.1),
she first grants herself full control on the folder (`_grant_full_control`), which
needs no elevation: she owns what she created.

What it does not bound, and the caller says so: reading (everything the person
reads stays readable, except what `refuse_read_up` closes), what carries a low
label (writable by any low process of the machine), the network, loopback
included, and the environment the process receives.

Windows only. Elsewhere `available()` is false and nothing here may be called.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

#: The integrity level of the agent's tree, as a well-known SID.
LOW_INTEGRITY_SID = "S-1-16-4096"

#: The labels, in SDDL. `NW`: no write up. `NR`, `NX`: no read up, no execute up.
#: `OICI`: inherited by the files and folders created beneath, whoever creates them.
_LABEL_LOW_FOLDER = "S:(ML;OICI;NW;;;LW)"
_LABEL_LOW_FILE = "S:(ML;;NW;;;LW)"
_LABEL_NO_READ_UP_FOLDER = "S:(ML;OICI;NRNWNX;;;ME)"
_LABEL_NO_READ_UP_FILE = "S:(ML;;NRNWNX;;;ME)"

#: The integrity levels by RID, as `current_integrity` names them.
_LEVELS = {0x0000: "untrusted", 0x1000: "low", 0x2000: "medium", 0x3000: "high", 0x4000: "system"}


class LowIntegrityError(OSError):
    """The system refused a step of the low-integrity mechanism — named, never guessed."""


def available() -> bool:
    """Whether this machine has the mechanism: Windows only."""
    return sys.platform == "win32"


class LowIntegrityProcess:
    """The part of `subprocess.Popen` that `maestro.sandbox.arbre.Arbre` uses.

    `pid`, `returncode`, `stdin`/`stdout`/`stderr` (the parent's end of a pipe, or
    `None`), `poll`, `wait` and `kill`. The process handle is held until `close`:
    as long as it is held, the pid cannot be recycled.
    """

    def __init__(
        self,
        args: Sequence[str],
        handle: int,
        pid: int,
        *,
        stdin: IO[bytes] | None = None,
        stdout: IO[bytes] | None = None,
        stderr: IO[bytes] | None = None,
    ) -> None:
        self.args = list(args)
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._handle: int | None = handle

    def poll(self) -> int | None:
        """The exit code if the process has ended, `None` otherwise."""
        if sys.platform == "win32":
            if self.returncode is None and self._handle is not None:
                if _kernel32.WaitForSingleObject(self._handle, 0) == _WAIT_OBJECT_0:
                    self.returncode = _exit_code(self._handle)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        """Waits for the end of the process; `subprocess.TimeoutExpired` past `timeout`."""
        if self.returncode is not None:
            return self.returncode
        if sys.platform != "win32" or self._handle is None:  # pragma: no cover - closed
            raise LowIntegrityError("the process handle is closed")
        else:
            millis = _INFINITE if timeout is None else max(0, int(timeout * 1000))
            result = _kernel32.WaitForSingleObject(self._handle, millis)
            if result == _WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(self.args, timeout or 0.0)
            if result != _WAIT_OBJECT_0:
                raise LowIntegrityError(ctypes.get_last_error(), "waiting for the process failed")
            self.returncode = _exit_code(self._handle)
            return self.returncode

    def kill(self) -> None:
        """Ends the process; nothing if it has already ended."""
        if sys.platform == "win32":
            if self.poll() is None and self._handle is not None:
                _kernel32.TerminateProcess(self._handle, 1)

    terminate = kill

    def close(self) -> None:
        """Releases the process handle — the process itself is not touched."""
        if sys.platform == "win32":
            if self._handle is not None:
                _kernel32.CloseHandle(self._handle)
                self._handle = None

    def __del__(self) -> None:
        self.close()


def spawn_suspended(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: int | IO[Any] | None = subprocess.DEVNULL,
    stdout: int | IO[Any] | None = subprocess.PIPE,
    stderr: int | IO[Any] | None = subprocess.STDOUT,
    creationflags: int = 0,
) -> LowIntegrityProcess:
    """Creates `argv` suspended, under the person's token lowered to low integrity.

    The streams follow `subprocess.Popen`: `None` inherits the caller's, an `int`
    is a file descriptor, `DEVNULL`, `PIPE` (the parent keeps the other end) and,
    for `stderr`, `STDOUT`. Only those three handles are inherited
    (`PROC_THREAD_ATTRIBUTE_HANDLE_LIST`): nothing else the caller holds open
    reaches the low process. The caller resumes it once it is placed in its job
    (`maestro.sandbox.arbre`).
    """
    if sys.platform != "win32":  # pragma: no cover - Windows only
        raise LowIntegrityError("the low integrity level exists only on Windows")
    else:
        streams = _Streams(stdin, stdout, stderr)
        token = _low_token()
        try:
            return streams.spawn(argv, token, cwd=cwd, env=env, creationflags=creationflags)
        finally:
            _kernel32.CloseHandle(token)
            streams.close_child_ends()


def label_low(path: Path) -> None:
    """Gives `path` an inheritable low label: a low process may write in it.

    A folder transmits the label to what is created beneath it, and the system
    propagates it to what is already there. Nothing is done when the label is
    already set: a folder labelled once is not walked again.
    """
    if sys.platform != "win32":  # pragma: no cover - Windows only
        raise LowIntegrityError("integrity labels exist only on Windows")
    else:
        _label(path, _LABEL_LOW_FOLDER if path.is_dir() else _LABEL_LOW_FILE)


def refuse_read_up(path: Path) -> None:
    """Gives `path` a medium label that refuses reads from below: a low process cannot read it.

    The person, at medium integrity, reads it as before. A folder transmits the
    label to what is created beneath it.
    """
    if sys.platform != "win32":  # pragma: no cover - Windows only
        raise LowIntegrityError("integrity labels exist only on Windows")
    else:
        _label(path, _LABEL_NO_READ_UP_FOLDER if path.is_dir() else _LABEL_NO_READ_UP_FILE)


def label_of(path: Path) -> str:
    """The integrity label of `path`, in SDDL (`S:(ML;OICI;NW;;;LW)`) — `""` without one."""
    if sys.platform != "win32":  # pragma: no cover - Windows only
        return ""
    else:
        return _label_text(path)


def current_integrity() -> str:
    """The integrity level of this process: `low`, `medium`, `high`, `system`…"""
    if sys.platform != "win32":  # pragma: no cover - Windows only
        return ""
    else:
        return _integrity_of_this_process()


# ------------------------------------------------------------------- Windows

_INFINITE = 0xFFFFFFFF
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    def _label_text(path: Path) -> str:
        descriptor = ctypes.c_void_p()
        error = _advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _LABEL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
            ctypes.byref(descriptor),
        )
        if error:
            raise LowIntegrityError(error, f"the label of {path} could not be read")
        try:
            text = ctypes.c_wchar_p()
            if not _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                descriptor, _SDDL_REVISION_1, _LABEL_SECURITY_INFORMATION, ctypes.byref(text), None
            ):
                raise LowIntegrityError(
                    ctypes.get_last_error(), "the label could not be written out"
                )
            try:
                return text.value or ""
            finally:
                _kernel32.LocalFree(text)
        finally:
            _kernel32.LocalFree(descriptor)

    def _integrity_of_this_process() -> str:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise LowIntegrityError(
                ctypes.get_last_error(), "the token of this process is unreadable"
            )
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(
                token, _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
            )
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                token, _TOKEN_INTEGRITY_LEVEL, buffer, size, ctypes.byref(size)
            ):
                raise LowIntegrityError(
                    ctypes.get_last_error(), "the integrity level is unreadable"
                )
            label = ctypes.cast(buffer, ctypes.POINTER(_TokenMandatoryLabel)).contents
            count = _advapi32.GetSidSubAuthorityCount(label.Label.Sid)[0]
            rid = _advapi32.GetSidSubAuthority(label.Label.Sid, count - 1)[0]
            return _LEVELS.get(rid, f"rid-{rid:#x}")
        finally:
            _kernel32.CloseHandle(token)


if sys.platform == "win32":
    _TOKEN_ASSIGN_PRIMARY = 0x0001
    _TOKEN_DUPLICATE = 0x0002
    _TOKEN_QUERY = 0x0008
    _TOKEN_ADJUST_DEFAULT = 0x0080
    _TOKEN_RIGHTS = _TOKEN_ASSIGN_PRIMARY | _TOKEN_DUPLICATE | _TOKEN_QUERY | _TOKEN_ADJUST_DEFAULT
    _SECURITY_IMPERSONATION = 2
    _TOKEN_PRIMARY = 1
    _TOKEN_USER = 1
    _TOKEN_INTEGRITY_LEVEL = 25
    _SE_GROUP_INTEGRITY = 0x00000020

    _SE_FILE_OBJECT = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _LABEL_SECURITY_INFORMATION = 0x00000010
    _SDDL_REVISION_1 = 1
    _ERROR_ACCESS_DENIED = 5

    _GRANT_ACCESS = 1
    _NO_INHERITANCE = 0
    _SUB_CONTAINERS_AND_OBJECTS_INHERIT = 3
    _TRUSTEE_IS_SID = 0
    _TRUSTEE_IS_USER = 1
    _FILE_ALL_ACCESS = 0x001F01FF

    _STARTF_USESTDHANDLES = 0x00000100
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    _DUPLICATE_SAME_ACCESS = 0x00000002
    _STD_HANDLES = (-10, -11, -12)
    _INVALID_HANDLE = ctypes.c_void_p(-1).value

    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TokenMandatoryLabel(ctypes.Structure):
        _fields_ = [("Label", _SidAndAttributes)]

    class _TokenUser(ctypes.Structure):
        _fields_ = [("User", _SidAndAttributes)]

    class _Trustee(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", ctypes.c_void_p),
        ]

    class _ExplicitAccess(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", wintypes.DWORD),
            ("grfAccessMode", ctypes.c_int),
            ("grfInheritance", wintypes.DWORD),
            ("Trustee", _Trustee),
        ]

    class _StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

    class _ProcessInformation(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.GetCurrentProcess.argtypes = ()
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.GetStdHandle.argtypes = (wintypes.DWORD,)
    _kernel32.GetStdHandle.restype = wintypes.HANDLE
    _kernel32.DuplicateHandle.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    )
    _kernel32.DuplicateHandle.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _kernel32.InitializeProcThreadAttributeList.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    )
    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
    _kernel32.DeleteProcThreadAttributeList.restype = None

    _advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.DuplicateTokenEx.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.GetLengthSid.argtypes = (ctypes.c_void_p,)
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.SetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    _advapi32.SetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
    _advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    _advapi32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
    _advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    _advapi32.CreateProcessAsUserW.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessInformation),
    )
    _advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    )
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.c_void_p,
    )
    _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL
    _advapi32.GetSecurityDescriptorSacl.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    )
    _advapi32.GetSecurityDescriptorSacl.restype = wintypes.BOOL
    _advapi32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetEntriesInAclW.argtypes = (
        wintypes.ULONG,
        ctypes.POINTER(_ExplicitAccess),
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.SetEntriesInAclW.restype = wintypes.DWORD

    def _exit_code(handle: int) -> int:
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise LowIntegrityError(ctypes.get_last_error(), "the exit code is unreadable")
        return int(code.value)

    def _low_token() -> int:
        """A primary token: a duplicate of this process's token, lowered to low integrity."""
        own = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_RIGHTS, ctypes.byref(own)
        ):
            raise LowIntegrityError(ctypes.get_last_error(), "the token of this process is closed")
        lowered = wintypes.HANDLE()
        try:
            if not _advapi32.DuplicateTokenEx(
                own,
                _TOKEN_RIGHTS,
                None,
                _SECURITY_IMPERSONATION,
                _TOKEN_PRIMARY,
                ctypes.byref(lowered),
            ):
                raise LowIntegrityError(ctypes.get_last_error(), "the token cannot be duplicated")
        finally:
            _kernel32.CloseHandle(own)
        sid = ctypes.c_void_p()
        if not _advapi32.ConvertStringSidToSidW(LOW_INTEGRITY_SID, ctypes.byref(sid)):
            _kernel32.CloseHandle(lowered)
            raise LowIntegrityError(ctypes.get_last_error(), "the low integrity SID is unknown")
        try:
            label = _TokenMandatoryLabel()
            label.Label.Sid = sid
            label.Label.Attributes = _SE_GROUP_INTEGRITY
            size = ctypes.sizeof(label) + _advapi32.GetLengthSid(sid)
            if not _advapi32.SetTokenInformation(
                lowered, _TOKEN_INTEGRITY_LEVEL, ctypes.byref(label), size
            ):
                _kernel32.CloseHandle(lowered)
                raise LowIntegrityError(ctypes.get_last_error(), "the token cannot be lowered")
        finally:
            _kernel32.LocalFree(sid)
        return int(lowered.value or 0)

    def _environment_block(env: Mapping[str, str]) -> Any:
        """The environment of the process, as `CreateProcess` reads it: sorted, double null."""
        entries = sorted(env.items(), key=lambda entry: entry[0].upper())
        text = "".join(f"{name}={value}\0" for name, value in entries) + "\0"
        return ctypes.create_unicode_buffer(text, len(text))

    class _Streams:
        """The three standard handles of the child, and the parent's ends of its pipes."""

        def __init__(
            self,
            stdin: int | IO[Any] | None,
            stdout: int | IO[Any] | None,
            stderr: int | IO[Any] | None,
        ) -> None:
            self._to_close_fds: list[int] = []
            self._to_close_files: list[IO[Any]] = []
            self.parent_stdin: IO[bytes] | None = None
            self.parent_stdout: IO[bytes] | None = None
            self.parent_stderr: IO[bytes] | None = None
            # One at a time: `stderr=STDOUT` reads the handle `stdout` has just received.
            self.handles: list[int] = []
            for index, value in enumerate((stdin, stdout, stderr)):
                self.handles.append(self._resolve(value, index))

        def _resolve(self, value: int | IO[Any] | None, index: int) -> int:
            if value is None:
                return int(_kernel32.GetStdHandle(_STD_HANDLES[index]) or 0)
            if value == subprocess.STDOUT:
                return self.handles[1]
            if value == subprocess.DEVNULL:
                null = open(os.devnull, "r+b")  # noqa: SIM115 - closed with the child's ends
                self._to_close_files.append(null)
                return int(msvcrt.get_osfhandle(null.fileno()))
            if value == subprocess.PIPE:
                read, write = os.pipe()
                child, parent = (read, write) if index == 0 else (write, read)
                self._to_close_fds.append(child)
                stream = os.fdopen(parent, "wb" if index == 0 else "rb")
                if index == 0:
                    self.parent_stdin = stream
                elif index == 1:
                    self.parent_stdout = stream
                else:
                    self.parent_stderr = stream
                return int(msvcrt.get_osfhandle(child))
            if isinstance(value, int):
                return int(msvcrt.get_osfhandle(value))
            return int(msvcrt.get_osfhandle(value.fileno()))

        def spawn(
            self,
            argv: Sequence[str],
            token: int,
            *,
            cwd: Path | None,
            env: Mapping[str, str] | None,
            creationflags: int,
        ) -> LowIntegrityProcess:
            current = _kernel32.GetCurrentProcess()
            inheritable: list[int] = []
            mapping: dict[int, int] = {}
            try:
                for handle in self.handles:
                    if not handle or handle == _INVALID_HANDLE or handle in mapping:
                        continue
                    copy = wintypes.HANDLE()
                    if not _kernel32.DuplicateHandle(
                        current,
                        handle,
                        current,
                        ctypes.byref(copy),
                        0,
                        True,
                        _DUPLICATE_SAME_ACCESS,
                    ):
                        raise LowIntegrityError(
                            ctypes.get_last_error(), "a standard handle cannot be passed on"
                        )
                    mapping[handle] = int(copy.value or 0)
                    inheritable.append(int(copy.value or 0))
                return self._create(argv, token, cwd, env, creationflags, mapping, inheritable)
            finally:
                for copy_handle in inheritable:
                    _kernel32.CloseHandle(copy_handle)

        def _create(
            self,
            argv: Sequence[str],
            token: int,
            cwd: Path | None,
            env: Mapping[str, str] | None,
            creationflags: int,
            mapping: Mapping[int, int],
            inheritable: Sequence[int],
        ) -> LowIntegrityProcess:
            info = _StartupInfoEx()
            info.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
            info.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            info.StartupInfo.hStdInput = mapping.get(self.handles[0], self.handles[0])
            info.StartupInfo.hStdOutput = mapping.get(self.handles[1], self.handles[1])
            info.StartupInfo.hStdError = mapping.get(self.handles[2], self.handles[2])
            flags = creationflags | _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT
            attributes: Any = None
            handle_list: Any = None
            if inheritable:
                size = ctypes.c_size_t()
                _kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
                attributes = ctypes.create_string_buffer(size.value)
                if not _kernel32.InitializeProcThreadAttributeList(
                    attributes, 1, 0, ctypes.byref(size)
                ):
                    raise LowIntegrityError(ctypes.get_last_error(), "no attribute list")
                handle_list = (wintypes.HANDLE * len(inheritable))(*inheritable)
                if not _kernel32.UpdateProcThreadAttribute(
                    attributes,
                    0,
                    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    handle_list,
                    ctypes.sizeof(handle_list),
                    None,
                    None,
                ):
                    _kernel32.DeleteProcThreadAttributeList(attributes)
                    raise LowIntegrityError(ctypes.get_last_error(), "the handle list is refused")
                info.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
                flags |= _EXTENDED_STARTUPINFO_PRESENT
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(list(argv)))
            block = None if env is None else _environment_block(env)
            process = _ProcessInformation()
            try:
                created = _advapi32.CreateProcessAsUserW(
                    token,
                    None,
                    command,
                    None,
                    None,
                    bool(inheritable),
                    flags,
                    block,
                    None if cwd is None else str(cwd),
                    ctypes.byref(info),
                    ctypes.byref(process),
                )
            finally:
                if attributes is not None:
                    _kernel32.DeleteProcThreadAttributeList(attributes)
            if not created:
                error = ctypes.get_last_error()
                self.close_parent_ends()
                raise LowIntegrityError(error, f"{argv[0]} could not be created at low integrity")
            _kernel32.CloseHandle(process.hThread)
            return LowIntegrityProcess(
                argv,
                int(process.hProcess or 0),
                int(process.dwProcessId),
                stdin=self.parent_stdin,
                stdout=self.parent_stdout,
                stderr=self.parent_stderr,
            )

        def close_child_ends(self) -> None:
            """Closes what only the child needed: the child's ends of the pipes, the null device."""
            for descriptor in self._to_close_fds:
                try:
                    os.close(descriptor)
                except OSError:
                    continue
            for stream in self._to_close_files:
                stream.close()
            self._to_close_fds = []
            self._to_close_files = []

        def close_parent_ends(self) -> None:
            for stream in (self.parent_stdin, self.parent_stdout, self.parent_stderr):
                if stream is not None:
                    stream.close()

    def _label(path: Path, sddl: str) -> None:
        """Sets the label `sddl` on `path`, granting full control first if the system asks.

        A label already set explicitly is left alone: the system writes it back with
        flags of its own (`S:AI(ML;OICI;NW;;;LW)`), so the entry is looked for, not
        the whole text — setting it again would walk the whole tree once more.
        """
        if sddl[2:] in label_of(path):
            return
        error = _set_label(path, sddl)
        if error == _ERROR_ACCESS_DENIED:
            _grant_full_control(path)
            error = _set_label(path, sddl)
        if error:
            raise LowIntegrityError(error, f"the label of {path} could not be set")

    def _set_label(path: Path, sddl: str) -> int:
        descriptor = ctypes.c_void_p()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None
        ):
            raise LowIntegrityError(ctypes.get_last_error(), f"the label {sddl} is malformed")
        try:
            present = wintypes.BOOL()
            defaulted = wintypes.BOOL()
            sacl = ctypes.c_void_p()
            if not _advapi32.GetSecurityDescriptorSacl(
                descriptor, ctypes.byref(present), ctypes.byref(sacl), ctypes.byref(defaulted)
            ):
                raise LowIntegrityError(ctypes.get_last_error(), "the label carries no list")
            name = ctypes.create_unicode_buffer(str(path))
            return int(
                _advapi32.SetNamedSecurityInfoW(
                    name, _SE_FILE_OBJECT, _LABEL_SECURITY_INFORMATION, None, None, None, sacl
                )
            )
        finally:
            _kernel32.LocalFree(descriptor)

    def _user_sid_buffer() -> Any:
        """The SID of the person this process runs as, in a buffer that holds it."""
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise LowIntegrityError(ctypes.get_last_error(), "the token of this process is closed")
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                token, _TOKEN_USER, buffer, size, ctypes.byref(size)
            ):
                raise LowIntegrityError(ctypes.get_last_error(), "the person's SID is unreadable")
            return buffer
        finally:
            _kernel32.CloseHandle(token)

    def _grant_full_control(path: Path) -> None:
        """Grants the person full control on `path`, inherited beneath a folder.

        "Modify" does not include `WRITE_OWNER`, which a label needs. The owner may
        always change the access list of what she owns: no elevation is asked.
        """
        descriptor = ctypes.c_void_p()
        old = ctypes.c_void_p()
        error = _advapi32.GetNamedSecurityInfoW(
            str(path),
            _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION,
            None,
            None,
            ctypes.byref(old),
            None,
            ctypes.byref(descriptor),
        )
        if error:
            raise LowIntegrityError(error, f"the access list of {path} could not be read")
        new = ctypes.c_void_p()
        try:
            user = _user_sid_buffer()
            sid = ctypes.cast(user, ctypes.POINTER(_TokenUser)).contents.User.Sid
            access = _ExplicitAccess()
            access.grfAccessPermissions = _FILE_ALL_ACCESS
            access.grfAccessMode = _GRANT_ACCESS
            access.grfInheritance = (
                _SUB_CONTAINERS_AND_OBJECTS_INHERIT if path.is_dir() else _NO_INHERITANCE
            )
            access.Trustee.TrusteeForm = _TRUSTEE_IS_SID
            access.Trustee.TrusteeType = _TRUSTEE_IS_USER
            access.Trustee.ptstrName = sid
            error = _advapi32.SetEntriesInAclW(1, ctypes.byref(access), old, ctypes.byref(new))
            if error:
                raise LowIntegrityError(error, "the access list could not be extended")
            name = ctypes.create_unicode_buffer(str(path))
            error = _advapi32.SetNamedSecurityInfoW(
                name, _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION, None, None, new, None
            )
            if error:
                raise LowIntegrityError(error, f"full control on {path} could not be granted")
        finally:
            if new:
                _kernel32.LocalFree(new)
            _kernel32.LocalFree(descriptor)


__all__ = [
    "LOW_INTEGRITY_SID",
    "LowIntegrityError",
    "LowIntegrityProcess",
    "available",
    "current_integrity",
    "label_low",
    "label_of",
    "refuse_read_up",
    "spawn_suspended",
]
