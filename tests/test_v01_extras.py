from fakes import CLEAN_LOAD, CURRENT_AWAKE, CURRENT_NONE, LOAD_TIME, load_journal, make

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


def _live_load(journal: str, current: str = CURRENT_AWAKE, script: str = "awake"):
    ctl, shell, repl, *_ = make(live=True)
    shell.on(["test", "-f"], rc=0)
    shell.on(["journalctl"], journal)
    repl.on("norns.state.name", current)
    repl.on("script.load", LOAD_TIME)
    return ctl.load_script(script, confirm=True), shell


def test_load_reports_clean_log():
    """A normal first load prints '### initializing data folder': not an error."""
    res, shell = _live_load(CLEAN_LOAD)
    assert res.success and res.data["load_log"]["status"] == "ok"
    assert res.data["load_log"]["lines"][0].endswith("code/awake/awake.lua")
    journal_calls = [c for c in shell.calls if c[0] == "journalctl"]
    assert journal_calls and all(c[c.index("--since") + 1] == LOAD_TIME for c in journal_calls)


def test_load_fails_on_script_error_in_log():
    for err in ["### SCRIPT ERROR: load fail", "### MISSING INCLUDE: lib/x"]:
        res, _ = _live_load(load_journal("awake/awake", err))
        assert not res.success and res.data["load_log"]["status"] == "error"
        assert res.data["load_log"]["errors"] == [load_journal("awake/awake", err).splitlines()[1]]


def test_init_error_after_script_init_is_caught():
    res, _ = _live_load(load_journal("awake/awake", "# script init", "### SCRIPT ERROR: init"))
    assert not res.success and res.data["load_log"]["status"] == "error"


def test_errors_before_the_load_line_are_ignored():
    old = load_journal("awake/awake", "### SCRIPT ERROR: load fail").splitlines()[1] + "\n"
    res, _ = _live_load(old + CLEAN_LOAD)
    assert res.success


def test_missing_load_line_is_unknown_not_success():
    res, _ = _live_load("")
    assert not res.success and res.data["load_log"]["status"] == "unknown"
    assert "UNKNOWN" in res.message


def test_no_init_before_timeout_is_unknown():
    res, _ = _live_load(load_journal("awake/awake", "# script run", "loading engine: Big"))
    assert not res.success and res.data["load_log"]["status"] == "unknown"


def test_other_scripts_load_line_does_not_count():
    res, _ = _live_load(load_journal("mlr/mlr", "# script init"))
    assert not res.success and res.data["load_log"]["status"] == "unknown"


def test_clean_journal_but_wrong_script_running_is_unknown():
    res, _ = _live_load(CLEAN_LOAD, current=CURRENT_NONE)
    assert not res.success and res.data["load_log"]["status"] == "unknown"


def test_script_prints_containing_error_words_are_not_errors():
    res, _ = _live_load(
        load_journal("awake/awake", "lua: just a print", "error: user text", "# script init")
    )
    assert res.success


def test_bad_clock_reply_is_unknown():
    ctl, shell, repl, *_ = make(live=True)
    shell.on(["test", "-f"], rc=0)
    repl.on("norns.state.name", CURRENT_AWAKE)
    repl.on("script.load", "1; rm -rf")
    res = ctl.load_script("awake", confirm=True)
    assert not res.success and not any(c[0] == "journalctl" for c in shell.calls)


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
