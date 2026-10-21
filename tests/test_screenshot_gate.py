"""Screenshot writes /tmp/norns-mcp-screen.png on the norns, so it is gated."""

from pathlib import Path

from fakes import make


def _ctl(live, tmp_path):
    ctl, _shell, repl, _osc, runner = make(live=live, tmp=tmp_path)
    repl.on("screen_export_screenshot", "saved")
    runner.on(lambda a: a[0] == "scp", effect=lambda a: Path(a[-1]).write_bytes(b"\x89PNG"))
    return ctl, repl, runner


def test_dry_run_sends_nothing(tmp_path):
    for confirm in (False, True):
        ctl, repl, runner = _ctl(False, tmp_path)
        res = ctl.screenshot(confirm=confirm)
        assert res.success and res.message.startswith("DRY RUN")
        assert res.data["would_send"]["writes_on_norns"] == "/tmp/norns-mcp-screen.png"
        assert repl.sent == [] and runner.calls == []


def test_live_needs_confirm(tmp_path):
    ctl, repl, runner = _ctl(True, tmp_path)
    res = ctl.screenshot()
    assert not res.success and "confirm=true" in res.message
    assert repl.sent == [] and runner.calls == []
    assert not ctl.screenshot(confirm="yes").success
