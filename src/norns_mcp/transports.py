"""The four ways this server touches the norns, each behind a small Protocol.

``controller.NornsController`` depends only on these Protocols, so tests use fakes
(tests/fakes.py) and never need the device.

- ``Shell``: one remote command over ``ssh norns`` (BatchMode). Commands are argv lists,
  checked by ``safety.remote_command`` before they are sent.
- ``Repl``: one line of Lua to matron's websocket REPL (ws://norns.local:5555/).
- ``Osc``: one OSC message to matron (UDP 10111).
- ``Runner``: a local process (rsync, scp) on the Mac.
"""

from __future__ import annotations

import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol

from norns_mcp.config import Settings
from norns_mcp.safety import check_lua_line, remote_command


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Shell(Protocol):
    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult:
        """Run ``argv`` on the norns. Raises ``safety.UnsafeCommand`` before sending if the
        command is not allowed; never raises for a non-zero exit."""


class Repl(Protocol):
    def send(self, code: str, timeout: float | None = None, marker_end: str | None = None) -> str:
        """Send one line of Lua, return everything matron printed back (frames joined).
        Reading stops at ``<ok>``, a traceback, or ``marker_end``. Raises
        ``ConnectionError`` if the REPL cannot be reached."""


class Osc(Protocol):
    def send(self, address: str, *args: float | str) -> bytes:
        """Send one OSC message; return the encoded packet."""


class Runner(Protocol):
    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult:
        """Run a local command (no shell)."""


# ── SSH ──────────────────────────────────────────────────────────────────────────


def ssh_base(settings: Settings) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        settings.ssh_alias,
        "--",
    ]


class SshShell:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult:
        command = remote_command(argv)  # raises UnsafeCommand before anything is sent
        try:
            proc = subprocess.run(
                [*ssh_base(self._settings), command],
                capture_output=True,
                text=True,
                timeout=timeout or self._settings.ssh_timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", f"ssh timed out after {timeout}s")
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)


class LocalRunner:
    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult:
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout or 600, check=False
            )
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", f"timed out after {timeout}s")
        except FileNotFoundError as exc:
            return CommandResult(127, "", str(exc))
        return CommandResult(proc.returncode, proc.stdout, proc.stderr)


# ── matron REPL ──────────────────────────────────────────────────────────────────

# matron ends a statement with "<ok>" and an error with a Lua traceback. Output can arrive
# in several frames, so we read until one of these appears, then drain briefly.
_TERMINALS = ("<ok>", "stack traceback:")


def _done(text: str, marker_end: str | None) -> bool:
    if marker_end and marker_end in text:
        return True
    return any(t in text for t in _TERMINALS)


class WsRepl:
    """A fresh websocket per command: matron's continuation buffer is per line, and a fresh
    connection keeps one call's noise out of the next (iMac norns-mcp-server lesson)."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, code: str, timeout: float | None = None, marker_end: str | None = None) -> str:
        from websockets.sync.client import connect

        check_lua_line(code)
        deadline = time.monotonic() + (timeout or self._settings.repl_timeout_s)
        chunks: list[str] = []
        try:
            with connect(
                self._settings.ws_url,
                subprotocols=["bus.sp.nanomsg.org"],
                open_timeout=5,
                close_timeout=1,
            ) as ws:
                ws.send(code + "\n")
                finished = False
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    wait = min(remaining, 0.25) if finished else remaining
                    try:
                        frame = ws.recv(timeout=wait)
                    except TimeoutError:
                        if finished:
                            break
                        continue
                    chunks.append(
                        frame if isinstance(frame, str) else frame.decode("utf-8", "replace")
                    )
                    if not finished and _done("".join(chunks), marker_end):
                        finished = True
        except (OSError, TimeoutError) as exc:
            raise ConnectionError(
                f"matron REPL unreachable at {self._settings.ws_url}: {exc}"
            ) from exc
        except Exception as exc:  # websockets.exceptions.* (InvalidHandshake, ConnectionClosed)
            if chunks:
                return "".join(chunks)
            raise ConnectionError(f"matron REPL error at {self._settings.ws_url}: {exc}") from exc
        return "".join(chunks)


# ── OSC ──────────────────────────────────────────────────────────────────────────


def _osc_pad(raw: bytes) -> bytes:
    raw += b"\x00"
    return raw + b"\x00" * (-len(raw) % 4)


def encode_osc(address: str, *args: float | str) -> bytes:
    """Encode one OSC 1.0 message with int32 / float32 / string arguments."""
    if not address.startswith("/"):
        raise ValueError("OSC address must start with '/'")
    tags = ","
    payload = b""
    for arg in args:
        if isinstance(arg, bool):
            raise ValueError("OSC booleans are not supported")
        if isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        elif isinstance(arg, float):
            tags += "f"
            payload += struct.pack(">f", arg)
        elif isinstance(arg, str):
            tags += "s"
            payload += _osc_pad(arg.encode("utf-8"))
        else:
            raise ValueError(f"unsupported OSC argument type {type(arg).__name__}")
    return _osc_pad(address.encode("utf-8")) + _osc_pad(tags.encode("ascii")) + payload


class UdpOsc:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, address: str, *args: float | str) -> bytes:
        packet = encode_osc(address, *args)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(packet, (self._settings.host, self._settings.osc_port))
        return packet
