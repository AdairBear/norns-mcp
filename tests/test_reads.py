from pathlib import Path

from fakes import CURRENT_AWAKE, CURRENT_NONE, make

from norns_mcp.config import REMOTE_CODE, REMOTE_DUST


def test_status_all_up():
    ctl, shell, repl, *_ = make()
    shell.on(["cat", "/home/we/version.txt"], "231114\n")
    shell.on(["systemctl", "is-active"], "active\n" * 6)
    repl.on(r"norns\.state\.name", CURRENT_AWAKE)
    res = ctl.status()
    assert res.success
    assert res.data["norns_version"] == "231114"
    assert res.data["script"]["name"] == "awake"
    assert res.data["services"]["norns-matron.service"] == "active"
    assert res.data["live"] is False


def test_status_repl_down_is_reported_not_raised():
    ctl, shell, repl, *_ = make()
    shell.on(["cat"], "231114\n")
    shell.on(["systemctl"], "active\n")
    repl.down = True
    res = ctl.status()
    assert not res.success and res.data["repl"] is False and "unreachable" in res.data["repl_error"]


def test_list_scripts_groups_by_folder():
    ctl, shell, *_ = make()
    shell.on(["find", REMOTE_CODE], "awake/awake.lua\nwe/study1.lua\nwe/study2.lua\nmlr/mlr.lua\n")
    res = ctl.list_scripts()
    by = {s["folder"]: s for s in res.data["scripts"]}
    assert by["awake"]["loadable"] == ["awake"]
    assert by["we"]["entry"] is None and by["we"]["loadable"] == ["we/study1", "we/study2"]
    assert list(by) == ["awake", "mlr", "we"]


def test_current_script_none_and_loaded():
    ctl, _, repl, *_ = make()
    repl.on("norns.state.name", CURRENT_NONE)
    res = ctl.current_script()
    assert res.success and res.data["loaded"] is False and res.data["engine"] is None
    repl.on("norns.state.name", CURRENT_AWAKE)
    res = ctl.current_script()
    assert res.data["loaded"] and res.data["engine"] == "PolyPerc" and res.data["param_count"] == 80


def test_params_parse_types_and_values():
    ctl, _, repl, *_ = make()
    repl.on(
        "params.count",
        "1\tLEVELS\t7\tLEVELS\t9\tLEVELS\n2\toutput_level\t3\toutput\t0.0\t0.0 dB\n"
        "3\tscale\t2\tscale\t4\tdorian",
    )
    res = ctl.params()
    assert res.success and res.message.startswith("2 params")
    p = res.data["params"]
    assert p[0]["type"] == "group" and p[1]["type"] == "control" and p[1]["value"] == 0.0
    assert p[2]["value"] == 4 and p[2]["display"] == "dorian"


def test_get_param_missing():
    ctl, _, repl, *_ = make()
    repl.on("params.lookup", "missing")
    assert not ctl.get_param("nope").success
    assert not ctl.get_param('x"); os.exit()').success
    assert len(repl.sent) == 1  # the bad id never reached the REPL


def test_read_script_paths():
    ctl, shell, *_ = make()
    shell.on(["stat"], "12\n")
    shell.on(["cat", f"{REMOTE_CODE}/awake/awake.lua"], "-- awake\n")
    shell.on(["cat", f"{REMOTE_CODE}/awake/lib/x.lua"], "-- lib\n")
    assert ctl.read_script("awake").data["text"] == "-- awake\n"
    assert ctl.read_script("awake", "lib/x.lua").data["text"] == "-- lib\n"
    for bad in ["../../etc/passwd", "lib/../../x", ".git/config", "/etc/passwd"]:
        assert not ctl.read_script("awake", bad).success
    assert not ctl.read_script("../x").success


def test_read_script_refuses_large_files():
    ctl, shell, *_ = make()
    shell.on(["stat"], str(10_000_000))
    assert "larger" in ctl.read_script("awake").message


def test_logs_validation_and_call():
    ctl, shell, *_ = make()
    shell.on(["journalctl"], "line1\nline2\n")
    res = ctl.logs("matron", 50)
    assert res.success and shell.calls[-1][:4] == ["journalctl", "-u", "norns-matron.service", "-n"]
    assert not ctl.logs("sshd").success
    assert not ctl.logs("matron", 0).success
    assert not ctl.logs("matron", True).success
    assert not ctl.logs("matron", 10, "1 min; rm -rf").success


def test_inventory_and_backup_compare(tmp_path: Path):
    ctl, shell, *_ = make(local_backup=tmp_path)
    shell.on(
        ["find", REMOTE_DUST],
        "code/awake/awake.lua\t100\t1.0\ncode/awake/lib/a.lua\t50\t1.0\n"
        "audio/x.wav\t1000\t1.0\ndata/system.state\t10\t1.0\n",
    )
    sha_a, sha_b = "a" * 64, "b" * 64
    (tmp_path / "MANIFEST.sha256").write_text(
        f"{sha_a}  100\tdate\tdust/code/awake/awake.lua\n"
        f"{sha_b}  999\tdate\tdust/audio/x.wav\n"
        f"{sha_a}  5\tdate\tdust/audio/gone.wav\n"
    )
    shell.on(["sha256sum"], f"{'c' * 64}  {REMOTE_DUST}/code/awake/awake.lua\n")
    res = ctl.dust_inventory(compare_backup=True, hashes=True)
    assert res.success
    assert res.data["top"]["code"] == {"files": 2, "bytes": 150}
    assert res.data["scripts"]["awake"]["files"] == 2
    b = res.data["backup"]
    assert b["only_in_backup"] == ["audio/gone.wav"]
    assert b["size_differs"] == ["audio/x.wav"]
    assert "data/system.state" in b["only_on_norns"]
    assert b["hash_differs"] == ["code/awake/awake.lua"]


def test_screenshot_exports_then_copies(tmp_path: Path):
    ctl, _shell, repl, _osc, runner = make(tmp=tmp_path)
    repl.on("screen_export_screenshot", "saved")
    runner.on(lambda a: a[0] == "scp", effect=lambda a: Path(a[-1]).write_bytes(b"\x89PNG"))
    res = ctl.screenshot()
    assert res.success and Path(res.data["path"]).read_bytes() == b"\x89PNG"
    assert "/tmp/norns-mcp-screen.png" in repl.sent[0]
    assert runner.calls[0][-2] == "norns:/tmp/norns-mcp-screen.png"


def test_reads_never_touch_osc():
    ctl, shell, repl, osc, _ = make(live=True)
    shell.on(["cat"], "231114\n")
    repl.on(".", CURRENT_NONE)
    ctl.status()
    ctl.current_script()
    ctl.params()
    assert osc.sent == []
