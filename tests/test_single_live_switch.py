"""Thomas, 2026-10-20: --live is the only live switch; no environment variable turns it on."""

import io

from fakes import make

from norns_mcp import cli, server
from norns_mcp.config import Settings


def test_env_var_does_not_turn_live_on(monkeypatch):
    for name in ("NORNS_MCP_LIVE", "NORNS_LIVE", "LIVE"):
        monkeypatch.setenv(name, "1")
    assert Settings.from_env().live is False
    assert Settings.from_env(env={"NORNS_MCP_LIVE": "1"}).live is False


def test_frontends_live_only_with_flag(monkeypatch):
    monkeypatch.setenv("NORNS_MCP_LIVE", "1")
    seen = []

    def factory(settings):
        seen.append(settings.live)
        return make(live=settings.live)[0]

    server.main([], factory=factory, run=lambda s: None)
    server.main(["--live"], factory=factory, run=lambda s: None)
    cli.main(["current"], factory=factory, out=io.StringIO())
    cli.main(["--live", "current"], factory=factory, out=io.StringIO())
    assert seen == [False, True, False, True]
