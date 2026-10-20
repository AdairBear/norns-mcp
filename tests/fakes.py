"""Fakes for the four transports. They record every call; nothing touches a network."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from norns_mcp.config import Settings
from norns_mcp.controller import NornsController
from norns_mcp.safety import check_remote_argv
from norns_mcp.transports import CommandResult, encode_osc

ShellHandler = Callable[[list[str]], CommandResult | None]


@dataclass
class FakeShell:
    """Answers by the first handler whose prefix matches (each element by startswith);
    default exit 1. Every argv still
    goes through the real guard, as SshShell does, so a test cannot send what ssh would not."""

    handlers: list[ShellHandler] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    def on(self, prefix: list[str], stdout: str = "", rc: int = 0, stderr: str = "") -> None:
        def handler(argv: list[str]) -> CommandResult | None:
            if len(argv) >= len(prefix) and all(
                a.startswith(p) for a, p in zip(argv, prefix, strict=False)
            ):
                return CommandResult(rc, stdout, stderr)
            return None

        self.handlers.append(handler)

    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult:
        check_remote_argv(argv)
        self.calls.append(list(argv))
        for handler in self.handlers:
            res = handler(argv)
            if res is not None:
                return res
        return CommandResult(1, "", "no fake for " + " ".join(argv))


@dataclass
class FakeRepl:
    """``replies`` maps a regex on the Lua to the payload put between the markers (str), or
    to a raw reply (``raw:`` prefix). Unmatched code gets a plain ``<ok>``."""

    replies: list[tuple[str, str]] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    down: bool = False

    def on(self, pattern: str, payload: str) -> None:
        self.replies.insert(0, (pattern, payload))

    def send(self, code: str, timeout: float | None = None, marker_end: str | None = None) -> str:
        if self.down:
            raise ConnectionError("matron REPL unreachable (fake)")
        assert "\n" not in code
        self.sent.append(code)
        for pattern, payload in self.replies:
            if re.search(pattern, code):
                if payload.startswith("raw:"):
                    return payload[4:]
                nonce = re.search(r"<NMCP:([0-9a-f]+)>", code).group(1)
                return f"noise from a script\n<NMCP:{nonce}>{payload}</NMCP:{nonce}>\n<ok>\n"
        return "<ok>\n"


@dataclass
class FakeOsc:
    sent: list[tuple] = field(default_factory=list)

    def send(self, address: str, *args) -> bytes:
        self.sent.append((address, *args))
        return encode_osc(address, *args)


@dataclass
class FakeRunner:
    handlers: list[Callable[[list[str]], CommandResult | None]] = field(default_factory=list)
    calls: list[list[str]] = field(default_factory=list)

    def on(
        self,
        pred: Callable[[list[str]], bool],
        stdout: str = "",
        rc: int = 0,
        stderr: str = "",
        effect: Callable[[list[str]], None] | None = None,
    ) -> None:
        def handler(argv: list[str]) -> CommandResult | None:
            if pred(argv):
                if effect:
                    effect(argv)
                return CommandResult(rc, stdout, stderr)
            return None

        self.handlers.append(handler)

    def run(self, argv: list[str], timeout: float | None = None) -> CommandResult:
        self.calls.append(list(argv))
        for handler in self.handlers:
            res = handler(argv)
            if res is not None:
                return res
        return CommandResult(1, "", "no fake for " + " ".join(argv))


CURRENT_NONE = "none\t\tnone\tnil\t63\t231114"
CURRENT_AWAKE = "awake\t/home/we/dust/code/awake/awake.lua\tawake\tPolyPerc\t80\t231114"


def make(live: bool = False, tmp: Path | None = None, **settings_kw):
    settings = Settings(live=live, state_dir=(tmp or Path("/nonexistent")) / "state", **settings_kw)
    shell, repl, osc, runner = FakeShell(), FakeRepl(), FakeOsc(), FakeRunner()
    ctl = NornsController(settings, shell, repl, osc, runner, sleep=lambda s: None)
    return ctl, shell, repl, osc, runner
