"""rsync deletes on a type change even without --delete: those block a live deploy
(verifier FAIL 2026-10-21 on f2f036c)."""

from pathlib import Path

import pytest
from fakes import make

from norns_mcp.config import REMOTE_CODE
from norns_mcp.deploy import local_types

TARGET = f"{REMOTE_CODE}/myscript"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    d = tmp_path / "myscript"
    (d / "lib").mkdir(parents=True)
    (d / "myscript.lua").write_text("-- hi\n")
    (d / "lib" / "x.lua").write_text("return 1\n")
    (d / "data").write_text("a file\n")
    return d


def _ctl(listing: str = "", d_rc: int = 0, e_rc: int = 0, l_rc: int = 1):
    ctl, shell, _repl, _osc, runner = make(live=True)
    shell.on(["test", "-L", TARGET], rc=l_rc)
    shell.on(["test", "-d", TARGET], rc=d_rc)
    shell.on(["test", "-e", TARGET], rc=e_rc)
    # find prints NUL-separated entries; the cases below are written one per line.
    shell.on(["find", "-P", TARGET, "-mindepth", "1"], listing.replace("\n", "\0"))
    runner.on(lambda a: a[0] == "rsync", "cd+++++++++ lib/\n")
    return ctl, shell, runner


def _pushed(runner):
    return [c for c in runner.calls if c[0] == "rsync" and "--dry-run" not in c]


def test_local_types(src):
    assert local_types(src) == {"data": "f", "lib": "d", "lib/x.lua": "f", "myscript.lua": "f"}


@pytest.mark.parametrize(
    ("listing", "path"),
    [
        ("f lib\n", "lib"),  # remote file where local has a folder
        ("d data\n", "data"),  # remote folder where local has a file
        ("d lib\nd lib/x.lua\n", "lib/x.lua"),
        ("p myscript.lua\n", "myscript.lua"),  # a FIFO or other special file
    ],
)
def test_type_change_blocks_before_backup(src, listing, path):
    ctl, shell, runner = _ctl(listing)
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "type differs" in res.message and path in res.message
    assert _pushed(runner) == [] and not any(c[0] in ("cp", "mkdir") for c in shell.calls)


def test_matching_types_and_remote_extras_do_not_block(src):
    ctl, _, _ = _ctl("d lib\nf lib/x.lua\nf myscript.lua\nf data\nf only_on_norns.lua\nd .git\n")
    res = ctl.deploy(str(src))  # live but no confirm: refused, and blockers still listed
    assert res.data["blockers"] == []


def test_dangling_symlink_target_blocks(src):
    ctl, _, runner = _ctl(d_rc=1, e_rc=1, l_rc=0)
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "target is a symlink" in res.message and _pushed(runner) == []


def test_target_is_a_file_blocks(src):
    ctl, _, runner = _ctl(d_rc=1, e_rc=0)
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "not a folder" in res.message and _pushed(runner) == []


def test_absent_target_has_no_blockers(src):
    ctl, shell, _ = _ctl(d_rc=1, e_rc=1)
    res = ctl.deploy(str(src))
    assert res.data["blockers"] == []
    assert not any(c[:2] == ["find", "-P"] for c in shell.calls)
