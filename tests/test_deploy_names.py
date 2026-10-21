"""Names with newlines cannot hide a type conflict (verifier FAIL 2026-10-21 on 9380cb8)."""

from pathlib import Path

from fakes import make

from norns_mcp.config import REMOTE_CODE
from norns_mcp.deploy import unsafe_names

TARGET = f"{REMOTE_CODE}/myscript"


def _src(tmp_path: Path, extra: str) -> Path:
    d = tmp_path / "myscript"
    d.mkdir()
    (d / "myscript.lua").write_text("-- hi\n")
    (d / extra).write_text("x\n")
    return d


def _ctl(listing: str):
    ctl, shell, _repl, _osc, runner = make(live=True)
    shell.on(["test", "-L", TARGET], rc=1)
    shell.on(["test", "-d", TARGET], rc=0)
    shell.on(["find", "-P", TARGET, "-mindepth", "1"], listing)
    runner.on(lambda a: a[0] == "rsync", ">f+++++++++ x\n")
    return ctl, shell, runner


def test_newline_in_local_name_blocks(tmp_path):
    src = _src(tmp_path, "a\nb")
    assert unsafe_names(src) == ["a\nb"]
    ctl, shell, runner = _ctl("f myscript.lua\0d a\nb\0")
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "control character" in res.message
    assert not any(c[0] == "rsync" and "--dry-run" not in c for c in runner.calls)
    assert not any(c[0] in ("cp", "mkdir") for c in shell.calls)


def test_remote_name_with_newline_is_parsed_whole(tmp_path):
    src = _src(tmp_path, "data")
    ctl, _, _ = _ctl("f myscript.lua\0f odd\nname\0d data\0")
    res = ctl.deploy(str(src))
    assert any("type differs" in b and "data" in b for b in res.data["blockers"])
    assert not any("odd" in b for b in res.data["blockers"])


def test_plain_names_are_fine(tmp_path):
    assert unsafe_names(_src(tmp_path, "notes.txt")) == []
