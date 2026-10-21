"""Every write is a dry run without --live, and needs confirm=True with it."""

import pytest
from fakes import CLEAN_LOAD, CURRENT_AWAKE, CURRENT_NONE, LOAD_TIME, make

from norns_mcp.config import REMOTE_CODE

WRITE_CALLS = [
    lambda c, confirm: c.load_script("awake", confirm=confirm),
    lambda c, confirm: c.start_script("awake", confirm=confirm),
    lambda c, confirm: c.stop_script(confirm=confirm),
    lambda c, confirm: c.set_param("cutoff", 0.5, confirm=confirm),
    lambda c, confirm: c.set_param("cutoff", 0.5, via="osc", confirm=confirm),
    lambda c, confirm: c.key(2, confirm=confirm),
    lambda c, confirm: c.enc(3, -2, confirm=confirm),
]


def _primed(live: bool):
    ctl, shell, repl, osc, _runner = make(live=live)
    shell.on(["test", "-f"], rc=0)
    repl.on("params.lookup", "found\t0.2\t0.2")
    repl.on("norns.state.name", CURRENT_AWAKE)
    repl.on("script.load", LOAD_TIME)
    repl.on("script.clear", "stopped")
    shell.on(["journalctl"], CLEAN_LOAD)
    return ctl, shell, repl, osc


def _writes(repl, osc):
    """Anything that changes state: load/stop/set Lua, or any OSC packet."""
    lua_writes = [
        c for c in repl.sent if "script.load" in c or "script.clear" in c or "params:set" in c
    ]
    return lua_writes, osc.sent


@pytest.mark.parametrize("call", WRITE_CALLS)
@pytest.mark.parametrize("confirm", [False, True])
def test_dry_run_sends_nothing_even_with_confirm(call, confirm):
    ctl, _, repl, osc = _primed(live=False)
    res = call(ctl, confirm)
    assert res.success and res.message.startswith("DRY RUN")
    assert res.data["live"] is False and res.data["sent"] is False and res.data["would_send"]
    assert _writes(repl, osc) == ([], [])


@pytest.mark.parametrize("call", WRITE_CALLS)
def test_live_without_confirm_sends_nothing(call):
    ctl, _, repl, osc = _primed(live=True)
    res = call(ctl, False)
    assert not res.success and "confirm=true" in res.message
    assert _writes(repl, osc) == ([], [])


def test_live_load_with_confirm():
    ctl, shell, repl, _ = _primed(live=True)
    res = ctl.load_script("awake", confirm=True)
    assert res.success and res.data["now"]["name"] == "awake"
    assert res.data["load_log"]["status"] == "ok"
    assert any('norns.script.load("code/awake/awake.lua")' in c for c in repl.sent)
    assert ["test", "-f", f"{REMOTE_CODE}/awake/awake.lua"] in shell.calls


def test_load_unknown_script_is_refused_before_any_lua():
    ctl, shell, repl, _ = make(live=True)[:4]
    shell.on(["test", "-f"], rc=1)
    res = ctl.load_script("nope", confirm=True)
    assert not res.success and repl.sent == []


def test_load_reports_when_script_does_not_come_up():
    ctl, shell, repl, _ = make(live=True)[:4]
    shell.on(["test", "-f"], rc=0)
    shell.on(["journalctl"], CLEAN_LOAD)
    repl.on("norns.state.name", CURRENT_NONE)
    repl.on("script.load", LOAD_TIME)
    res = ctl.load_script("awake", confirm=True)
    assert not res.success and "UNKNOWN" in res.message


def test_start_without_name_uses_last_script():
    ctl, shell, repl, _ = _primed(live=True)
    shell.on(
        ["cat", "/home/we/dust/data/system.state"],
        "norns.state.script = '/home/we/dust/code/awake/awake.lua'\n",
    )
    res = ctl.start_script(None, confirm=True)
    assert res.success and any("code/awake/awake.lua" in c for c in repl.sent)


def test_start_without_name_and_no_last_script():
    ctl, shell, *_ = make(live=True)
    shell.on(["cat"], "norns.state.script = ''\n")
    assert not ctl.start_script(None, confirm=True).success


def test_live_set_param_reads_back():
    ctl, _, repl, _ = _primed(live=True)
    repl.on(r"params:set", "set\t0.5\t0.5")
    res = ctl.set_param("cutoff", 0.5, confirm=True)
    assert res.success and res.data["before"]["value"] == 0.2 and res.data["after"]["value"] == 0.5


def test_set_param_missing_id_refused_before_write():
    ctl, _, repl, osc = make(live=True)[:4]
    repl.on("params.lookup", "missing")
    res = ctl.set_param("nope", 1, confirm=True)
    assert not res.success and _writes(repl, osc) == ([], [])


def test_set_param_rejects_bad_values():
    ctl, _, repl, osc = _primed(live=True)
    for bad in [True, float("nan"), None]:
        assert not ctl.set_param("cutoff", bad, confirm=True).success
    assert _writes(repl, osc) == ([], [])


def test_live_key_press_and_release():
    ctl, _, _, osc = _primed(live=True)
    assert ctl.key(2, confirm=True).success
    assert osc.sent == [("/remote/key", 2, 1), ("/remote/key", 2, 0)]


def test_key_enc_validation():
    ctl, _, _, osc = _primed(live=True)
    for res in [
        ctl.key(4, confirm=True),
        ctl.key(1, 2, confirm=True),
        ctl.key(True, confirm=True),
        ctl.enc(0, 1, confirm=True),
        ctl.enc(1, 0, confirm=True),
        ctl.enc(1, 999, confirm=True),
    ]:
        assert not res.success
    assert osc.sent == []


def test_confirm_must_be_true_not_truthy():
    ctl, _, repl, osc = _primed(live=True)
    assert not ctl.stop_script(confirm="yes").success
    assert _writes(repl, osc) == ([], [])
