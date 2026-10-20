import asyncio
import io
import json
from pathlib import Path

import pytest
from fakes import CURRENT_AWAKE, make

from norns_mcp import cli, server
from norns_mcp.transports import encode_osc

M8_CORE = Path.home() / "Developer" / "m8-mcp" / "src" / "m8_mcp"


def test_osc_encoding():
    assert encode_osc("/remote/key", 2, 1) == (
        b"/remote/key\x00,ii\x00" + b"\x00\x00\x00\x02" + b"\x00\x00\x00\x01"
    )
    pkt = encode_osc("/param/x", 0.5, "ab")
    assert pkt.startswith(b"/param/x\x00\x00\x00\x00,fs\x00") and len(pkt) % 4 == 0
    with pytest.raises(ValueError):
        encode_osc("no-slash")
    with pytest.raises(ValueError):
        encode_osc("/x", True)


def test_tools_registered_and_described():
    ctl, *_ = make()
    mcp = server.build_server(ctl)
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    reads = {
        "norns_status",
        "norns_list_scripts",
        "norns_current_script",
        "norns_read_script",
        "norns_params",
        "norns_get_param",
        "norns_logs",
        "norns_dust_inventory",
        "norns_screenshot",
    }
    writes = {
        "norns_load_script",
        "norns_start_script",
        "norns_stop_script",
        "norns_set_param",
        "norns_key",
        "norns_enc",
        "norns_deploy",
    }
    assert set(tools) == reads | writes
    for name in writes:
        assert tools[name].description.startswith("WRITE")
        props = tools[name].input_schema["properties"]
        assert props["confirm"]["default"] is False


def test_server_call_dry_run_by_default():
    ctl, shell, repl, _osc, _ = make()
    shell.on(["test", "-f"], rc=0)
    mcp = server.build_server(ctl)
    out = asyncio.run(mcp.call_tool("norns_load_script", {"script": "awake", "confirm": True}))
    text = json.dumps(out.model_dump() if hasattr(out, "model_dump") else out, default=str)
    assert "DRY RUN" in text
    assert not any("script.load" in c for c in repl.sent)


def test_server_main_defaults_to_dry_run(monkeypatch):
    monkeypatch.delenv("NORNS_MCP_LIVE", raising=False)
    seen = {}

    def factory(settings):
        seen["live"] = settings.live
        return make()[0]

    assert server.main([], factory=factory, run=lambda s: None) == 0
    assert seen["live"] is False
    monkeypatch.setenv("NORNS_MCP_LIVE", "1")
    server.main(["--dry-run"], factory=factory, run=lambda s: None)
    assert seen["live"] is False


def _cli(argv, ctl_parts):
    ctl = ctl_parts[0]
    out = io.StringIO()
    code = cli.main(argv, factory=lambda s: _rebind(ctl, s), out=out)
    return code, out.getvalue()


def _rebind(ctl, settings):
    ctl.settings = settings
    ctl.deployer.settings = settings
    return ctl


def test_cli_write_needs_live_and_confirm(monkeypatch):
    monkeypatch.delenv("NORNS_MCP_LIVE", raising=False)
    parts = make()
    shell, repl = parts[1], parts[2]
    shell.on(["test", "-f"], rc=0)
    repl.on("norns.state.name", CURRENT_AWAKE)
    repl.on("script.load", "called")
    code, text = _cli(["load", "awake", "--confirm"], parts)
    assert code == 0 and "DRY RUN" in text
    code, text = _cli(["--live", "load", "awake"], parts)
    assert code == 1 and "confirm" in text
    assert not any("script.load" in c for c in repl.sent)
    code, text = _cli(["--live", "load", "awake", "--confirm"], parts)
    assert code == 0 and any("script.load" in c for c in repl.sent)


def test_cli_reads(monkeypatch):
    parts = make()
    parts[2].on("norns.state.name", CURRENT_AWAKE)
    code, text = _cli(["current"], parts)
    assert code == 0 and "running: awake" in text
    code, text = _cli(["--json", "current"], parts)
    assert json.loads(text)["data"]["engine"] == "PolyPerc"


@pytest.mark.skipif(not M8_CORE.exists(), reason="m8-mcp checkout not present")
def test_shared_core_matches_m8_mcp():
    """docs/CORE.md: these two files stay byte-identical to m8-mcp until the core is a package."""
    here = Path(__file__).resolve().parents[1] / "src" / "norns_mcp" / "core"
    assert (here / "serialize.py").read_bytes() == (M8_CORE / "serialize.py").read_bytes()
    assert (here / "signals.py").read_bytes() == (M8_CORE / "_signals.py").read_bytes()
