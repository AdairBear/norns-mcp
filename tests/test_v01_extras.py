from fakes import CURRENT_AWAKE, make

from norns_mcp.controller import strip_repl_echo

JOURNAL = """\
2026-10-20T19:27:10+0100 norns ws-wrapper[536]: # script clear
2026-10-20T19:27:10+0100 norns ws-wrapper[536]: <NMCP:0f5e3f01>1        LEVELS
2026-10-20T19:27:10+0100 norns ws-wrapper[536]: 63        clock_crow_in_div</NMCP:0f5e3f01>
2026-10-20T19:27:10+0100 norns ws-wrapper[536]: <ok>
2026-10-20T19:27:13+0100 norns ws-wrapper[536]: <NMCP:b279c48d>found 0.0</NMCP:b279c48d>
2026-10-20T19:27:14+0100 norns ws-wrapper[536]: # script load: /home/we/dust/code/awake/awake.lua
"""


def test_strip_repl_echo_keeps_real_lines():
    out = strip_repl_echo(JOURNAL)
    assert out.splitlines() == [JOURNAL.splitlines()[0], JOURNAL.splitlines()[-1]]


def test_logs_hide_echo_by_default_and_on_request():
    ctl, shell, *_ = make()
    shell.on(["journalctl"], JOURNAL)
    assert "NMCP" not in ctl.logs("matron", 10).data["text"]
    assert "NMCP" in ctl.logs("matron", 10, hide_repl_echo=False).data["text"]


def _live_load(journal: str):
    ctl, shell, repl, *_ = make(live=True)
    shell.on(["test", "-f"], rc=0)
    shell.on(["journalctl"], journal)
    repl.on("norns.state.name", CURRENT_AWAKE)
    repl.on("script.load", "called")
    return ctl.load_script("awake", confirm=True)


def test_load_reports_clean_log():
    res = _live_load(JOURNAL + "2026-10-20T19:27:15+0100 norns ws-wrapper[536]: # script run\n")
    assert res.success and res.data["load_log"]["errors"] == []
    assert res.data["load_log"]["lines"][0].endswith("code/awake/awake.lua")


def test_load_fails_on_script_error_in_log():
    bad = JOURNAL + (
        "2026-10-20T19:27:15+0100 norns ws-wrapper[536]: ### load fail\n"
        "2026-10-20T19:27:15+0100 norns ws-wrapper[536]: lua: awake.lua:12: bad thing\n"
    )
    res = _live_load(bad)
    assert not res.success and len(res.data["load_log"]["errors"]) == 2


def test_errors_before_the_load_line_are_ignored():
    old = "2026-10-20T19:00:00+0100 norns ws-wrapper[536]: ### load fail\n" + JOURNAL
    assert _live_load(old).success


def test_status_reports_maiden_version():
    ctl, shell, repl, *_ = make()
    ctl.http_get = lambda url: '{"api":"maiden","version":"v1.1.6 / 2022-03-22T06:47"}'
    shell.on(["cat"], "231114\n")
    shell.on(["systemctl"], "active\n")
    repl.on("norns.state.name", CURRENT_AWAKE)
    assert ctl.status().data["maiden"].startswith("v1.1.6")


def test_status_survives_maiden_down():
    ctl, shell, repl, *_ = make()
    shell.on(["cat"], "231114\n")
    shell.on(["systemctl"], "active\n")
    repl.on("norns.state.name", CURRENT_AWAKE)
    res = ctl.status()
    assert res.success and res.data["maiden"] is None


def test_strip_repl_echo_when_window_starts_inside_a_block():
    text = (
        "-- Journal begins at ... --\n"
        "2026 norns ws-wrapper[536]: 62        clock_crow_out_div\n"
        "2026 norns ws-wrapper[536]: 63        clock_crow_in_div</NMCP:96316107>\n"
        "2026 norns ws-wrapper[536]: <ok>\n"
        "2026 norns ws-wrapper[536]: # script run\n"
    )
    assert strip_repl_echo(text).splitlines() == [
        "-- Journal begins at ... --",
        "2026 norns ws-wrapper[536]: # script run",
    ]
