"""Symlinks block a live deploy (verifier FAIL 2026-10-21 on --keep-dirlinks)."""

import os
from pathlib import Path

import pytest
from fakes import make

from norns_mcp.config import REMOTE_CODE

TARGET = f"{REMOTE_CODE}/myscript"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    d = tmp_path / "myscript"
    d.mkdir()
    (d / "myscript.lua").write_text("-- hi\n")
    return d


def _ctl(live: bool, link_rc: int = 1, links: str = "", find_rc: int = 0):
    ctl, shell, _repl, _osc, runner = make(live=live)
    shell.on(["test", "-d", TARGET], rc=0)
    shell.on(["test", "-L", TARGET], rc=link_rc)
    listing = "f myscript.lua\n" + "".join(f"l {p}\n" for p in links.split())
    shell.on(["find", "-P", TARGET, "-mindepth", "1"], listing, rc=find_rc)
    runner.on(lambda a: a[0] == "rsync", ">f.st...... myscript.lua\n")
    return ctl, shell, runner


def _pushed(runner):
    return [c for c in runner.calls if c[0] == "rsync" and "--dry-run" not in c]


def test_no_keep_dirlinks_flag(src):
    ctl, _, runner = _ctl(live=False)
    ctl.deploy(str(src))
    assert all("--keep-dirlinks" not in c and "-K" not in c for c in runner.calls)


def test_remote_symlink_blocks_live_deploy_before_backup(src):
    ctl, shell, runner = _ctl(live=True, links="lib\n")
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "symlink on the norns" in res.message
    assert _pushed(runner) == []
    assert not any(c[0] in ("cp", "mkdir") for c in shell.calls)


def test_target_itself_a_symlink_blocks(src):
    ctl, _, runner = _ctl(live=True, link_rc=0)
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "target is a symlink" in res.message and _pushed(runner) == []


def test_unreadable_symlink_check_blocks(src):
    ctl, _, runner = _ctl(live=True, find_rc=1)
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "could not check" in res.message and _pushed(runner) == []


def test_local_symlink_blocks(src, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    os.symlink(tmp_path / "elsewhere", src / "lib")
    ctl, _, runner = _ctl(live=True)
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "local symlink: lib" in res.message and _pushed(runner) == []


def test_dry_run_reports_blockers(src):
    ctl, _, _ = _ctl(live=False, links="lib\n")
    res = ctl.deploy(str(src))
    assert res.success and "BLOCKED" in res.message
    assert res.data["blockers"] == [f"symlink on the norns: {TARGET}/lib"]
