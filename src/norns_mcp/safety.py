"""Every guard in one place: names, remote commands, rsync arguments, Lua quoting.

Rules (Thomas, 2026-10-20): no sudo, no firmware or system updates, never delete files on
the norns. Remote commands are argv lists checked here before they are joined with
``shlex.quote``; nothing outside the allowlist reaches ssh.
"""

from __future__ import annotations

import math
import re
import shlex

from norns_mcp.config import REMOTE_BACKUPS, REMOTE_HOME, REMOTE_SCREENSHOT


class UnsafeCommand(ValueError):
    """A remote command or rsync call that breaks a safety rule. Never sent."""


# A script folder or file name: letters, digits, dot, dash, underscore. No slashes, no "..".
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
# A param id as norns scripts use them ("cutoff", "1_level", "output_level", "grid/rows").
_PARAM_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]{0,127}$")


def valid_name(name: str) -> bool:
    return bool(_NAME.match(name)) and ".." not in name


def check_name(name: str, what: str = "name") -> str:
    if not isinstance(name, str) or not valid_name(name):
        raise ValueError(
            f"invalid {what} {name!r}: use letters, digits, '.', '-', '_' (no slashes, no '..')"
        )
    return name


def check_param_id(param_id: str) -> str:
    if not isinstance(param_id, str) or not _PARAM_ID.match(param_id) or ".." in param_id:
        raise ValueError(f"invalid param id {param_id!r}")
    return param_id


def split_script(script: str) -> tuple[str, str]:
    """``awake`` -> (awake, awake); ``awake/other`` -> (awake, other). ``.lua`` is optional."""
    if not isinstance(script, str):
        raise ValueError("script must be a string")
    parts = script.strip().removesuffix(".lua").split("/")
    if len(parts) == 1:
        folder = file = parts[0]
    elif len(parts) == 2:
        folder, file = parts
    else:
        raise ValueError(f"invalid script {script!r}: use 'folder' or 'folder/file'")
    return check_name(folder, "script folder"), check_name(file, "script file")


# ── Remote commands ──────────────────────────────────────────────────────────────

READ_PROGRAMS = frozenset(
    {"cat", "find", "journalctl", "du", "stat", "test", "ls", "sha256sum", "uname", "df"}
)
# Writes are limited to exactly these shapes; see check_remote_argv.
WRITE_PROGRAMS = frozenset({"mkdir", "cp"})
FORBIDDEN_TOKENS = frozenset(
    {
        "sudo",
        "su",
        "doas",
        "rm",
        "rmdir",
        "unlink",
        "shred",
        "mv",
        "dd",
        "truncate",
        "apt",
        "apt-get",
        "dpkg",
        "pip",
        "pip3",
        "reboot",
        "shutdown",
        "poweroff",
        "halt",
        "chmod",
        "chown",
        "ln",
    }
)


def _under(path: str, root: str) -> bool:
    if ".." in path.split("/"):
        return False
    return path == root or path.startswith(root.rstrip("/") + "/")


def check_remote_argv(argv: list[str]) -> list[str]:
    """Raise ``UnsafeCommand`` unless ``argv`` is an allowed read or one of the backup writes."""
    if not argv or not all(isinstance(a, str) for a in argv):
        raise UnsafeCommand("empty or non-string argv")
    prog = argv[0]
    for token in argv:
        if token in FORBIDDEN_TOKENS or token.split("/")[-1] in FORBIDDEN_TOKENS:
            raise UnsafeCommand(f"forbidden token {token!r} in {argv!r}")
        if token.startswith("--delete") or token == "-delete":
            raise UnsafeCommand(f"delete flag {token!r} in {argv!r}")
    if prog == "find" and any(
        t in {"-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
        for t in argv
    ):
        raise UnsafeCommand(f"find action not allowed: {argv!r}")
    if prog == "journalctl" and any(
        t.startswith(("--vacuum", "--rotate", "--flush", "--setup-keys", "--relinquish"))
        for t in argv
    ):
        raise UnsafeCommand(f"journalctl maintenance flag not allowed: {argv!r}")
    if prog == "systemctl":
        if len(argv) < 2 or argv[1] not in {"is-active", "show"}:
            raise UnsafeCommand(f"only 'systemctl is-active|show' is allowed: {argv!r}")
        return argv
    if prog in READ_PROGRAMS:
        return argv
    if prog == "mkdir":
        # mkdir -p <dir under the backup root>
        if len(argv) == 3 and argv[1] == "-p" and _under(argv[2], REMOTE_BACKUPS):
            return argv
        raise UnsafeCommand(f"mkdir only as 'mkdir -p {REMOTE_BACKUPS}/...': {argv!r}")
    if prog == "cp":
        # cp -a <dust path> <new dir under the backup root>   (copy only, never a move)
        if (
            len(argv) == 4
            and argv[1] == "-a"
            and _under(argv[2], f"{REMOTE_HOME}/dust")
            and _under(argv[3], REMOTE_BACKUPS)
            and argv[3] != REMOTE_BACKUPS
        ):
            return argv
        raise UnsafeCommand(f"cp only as 'cp -a <dust path> {REMOTE_BACKUPS}/<new>': {argv!r}")
    raise UnsafeCommand(f"program {prog!r} is not on the allowlist")


def remote_command(argv: list[str]) -> str:
    """Check ``argv`` and join it into one shell-safe string for ``ssh host -- <string>``."""
    return shlex.join(check_remote_argv(argv))


# ── rsync ────────────────────────────────────────────────────────────────────────

RSYNC_FORBIDDEN_PREFIXES = (
    "--delete",
    "--del",
    "--remove-source-files",
    "--prune-empty-dirs",
    "--force",
    "--inplace",
    "--rsync-path",
    "--existing",
    "--ignore-existing",
)


def check_rsync_argv(argv: list[str], remote_root: str) -> list[str]:
    """Raise unless this is a plain push into ``remote_root`` with no delete-type flag."""
    if not argv or argv[0] != "rsync":
        raise UnsafeCommand(f"not an rsync call: {argv!r}")
    for token in argv[1:]:
        if token.startswith(RSYNC_FORBIDDEN_PREFIXES):
            raise UnsafeCommand(f"rsync flag {token!r} is not allowed")
        # Short flag clusters: refuse anything not in our fixed set.
        if token.startswith("-") and not token.startswith("--") and set(token[1:]) - set("rltvnie"):
            raise UnsafeCommand(f"rsync short flags {token!r} are not allowed")
    dest = argv[-1]
    _, _, remote_path = dest.partition(":")
    if not remote_path or not _under(remote_path.rstrip("/"), remote_root):
        raise UnsafeCommand(f"rsync destination {dest!r} is outside {remote_root}")
    if "sudo" in " ".join(argv):
        raise UnsafeCommand("sudo in rsync call")
    return argv


# ── Lua ──────────────────────────────────────────────────────────────────────────


def lua_str(value: str) -> str:
    """A Lua string literal for ``value``, safe to put on one REPL line."""
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif code < 32 or code == 127:
            out.append(f"\\{code:03d}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def lua_value(value: object) -> str:
    """A Lua literal for a param value: number or string. Booleans are refused."""
    if isinstance(value, bool):
        raise ValueError("param value must be a number or a string, not a boolean")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("param value must be finite")
        return repr(value)
    if isinstance(value, str):
        return lua_str(value)
    raise ValueError(f"param value must be a number or a string, got {type(value).__name__}")


def check_lua_line(code: str) -> str:
    """matron reads the REPL line by line: a newline would leave half a chunk in its buffer
    and corrupt every later command (found on the iMac norns-mcp-server, 2025)."""
    if "\n" in code or "\r" in code:
        raise ValueError("Lua sent to the REPL must be a single line")
    return code


def screenshot_path_ok(path: str) -> bool:
    return path == REMOTE_SCREENSHOT


__all__ = [
    "UnsafeCommand",
    "check_lua_line",
    "check_name",
    "check_param_id",
    "check_remote_argv",
    "check_rsync_argv",
    "lua_str",
    "lua_value",
    "remote_command",
    "screenshot_path_ok",
    "split_script",
    "valid_name",
]
