import hashlib
from pathlib import Path

import pytest
from fakes import make

from norns_mcp.config import REMOTE_BACKUPS, REMOTE_CODE
from norns_mcp.deploy import parse_itemized

TARGET = f"{REMOTE_CODE}/myscript"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    d = tmp_path / "myscript"
    (d / "lib").mkdir(parents=True)
    (d / "myscript.lua").write_text("-- hi\n")
    (d / "lib" / "x.lua").write_text("return 1\n")
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref\n")
    return d


ITEMIZED = ">f+++++++++ myscript.lua\ncd+++++++++ lib/\n>f.st...... lib/x.lua\n"


def _setup(src: Path, live: bool, exists: bool = True, backup_matches: bool = True):
    ctl, shell, _repl, _osc, runner = make(live=live)
    shell.on(["test", "-d", TARGET], rc=0 if exists else 1)
    shell.on(["test", "-e"], rc=1)
    shell.on(["test", "-L", TARGET], rc=1)  # target is a real folder
    shell.on(["find", "-P", TARGET, "-type", "l"], "")  # no symlinks under it
    shell.on(["mkdir", "-p", REMOTE_BACKUPS])
    shell.on(["cp", "-a", TARGET])
    shell.on(["find", TARGET], "myscript.lua\t6\n")
    shell.on(["find", REMOTE_BACKUPS], "myscript.lua\t6\n" if backup_matches else "")
    sums = "".join(
        f"{hashlib.sha256((src / rel).read_bytes()).hexdigest()}  {TARGET}/{rel}\n"
        for rel in ["lib/x.lua", "myscript.lua"]
    )
    shell.on(["sha256sum"], sums)
    runner.on(lambda a: a[0] == "rsync", ITEMIZED)
    return ctl, shell, runner


def _rsyncs(runner):
    return [c for c in runner.calls if c[0] == "rsync"]


def test_parse_itemized():
    out = parse_itemized(ITEMIZED + "*deleting   old.lua\n")
    assert out["new_files"] == ["myscript.lua"]
    assert out["changed_files"] == ["lib/x.lua"]
    assert out["new_dirs"] == ["lib/"]
    assert out["other"] == ["*deleting   old.lua"]


def test_default_is_dry_run(src):
    ctl, shell, runner = _setup(src, live=False)
    res = ctl.deploy(str(src))
    assert res.success and res.message.startswith("DRY RUN")
    calls = _rsyncs(runner)
    assert len(calls) == 1 and "--dry-run" in calls[0]
    assert res.data["plan"]["new_files"] == ["myscript.lua"]
    assert not any(c[0] in ("cp", "mkdir") for c in shell.calls)


def test_live_needs_confirm(src):
    ctl, shell, runner = _setup(src, live=True)
    res = ctl.deploy(str(src), confirm=False)
    assert not res.success and "confirm" in res.message
    assert all("--dry-run" in c for c in _rsyncs(runner))
    assert not any(c[0] in ("cp", "mkdir") for c in shell.calls)


def test_live_backs_up_then_pushes_then_verifies(src):
    ctl, shell, runner = _setup(src, live=True)
    res = ctl.deploy(str(src), confirm=True)
    assert res.success, res.message
    cp = [c for c in shell.calls if c[0] == "cp"]
    assert (
        len(cp) == 1 and cp[0][2] == TARGET and cp[0][3].startswith(f"{REMOTE_BACKUPS}/myscript-")
    )
    order = [c[0] for c in shell.calls]
    assert order.index("cp") < order.index("sha256sum")
    live = [c for c in _rsyncs(runner) if "--dry-run" not in c]
    assert len(live) == 1
    assert live[0][-1] == f"norns:{TARGET}/" and live[0][-2] == f"{src}/"
    assert res.data["verify"] == {"checked": 2, "mismatch": [], "missing": []}


def test_never_delete_flags(src):
    ctl, _, runner = _setup(src, live=True)
    ctl.deploy(str(src), confirm=True)
    for call in _rsyncs(runner):
        assert not any(t.startswith(("--del", "--remove", "--force")) for t in call)
        assert "--exclude=.git" in call


def test_backup_mismatch_blocks_push(src):
    ctl, _, runner = _setup(src, live=True, backup_matches=False)
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and "nothing pushed" in res.message
    assert all("--dry-run" in c for c in _rsyncs(runner))


def test_new_folder_needs_no_backup(src):
    ctl, shell, _runner = _setup(src, live=True, exists=False)
    res = ctl.deploy(str(src), confirm=True)
    assert res.success and "backup" not in res.data
    assert not any(c[0] == "cp" for c in shell.calls)


def test_hash_mismatch_is_a_failure(src):
    ctl, shell, _runner = _setup(src, live=True)
    shell.on(["sha256sum"], f"{'0' * 64}  {TARGET}/myscript.lua\n")
    shell.handlers.insert(0, shell.handlers.pop())  # the bad sums win
    res = ctl.deploy(str(src), confirm=True)
    assert not res.success and res.data["verify"]["mismatch"] == ["myscript.lua"]
    assert res.data["verify"]["missing"] == ["lib/x.lua"]


def test_bad_inputs(tmp_path, src):
    ctl, _, runner = _setup(src, live=True)
    assert not ctl.deploy(str(tmp_path / "missing"), confirm=True).success
    assert not ctl.deploy(str(src), name="../etc", confirm=True).success
    empty = tmp_path / "empty"
    empty.mkdir()
    assert not ctl.deploy(str(empty), confirm=True).success
    assert _rsyncs(runner) == []


def test_warns_without_entry_script(src):
    ctl, _, _ = _setup(src, live=False)
    res = ctl.deploy(str(src), name="other")
    assert res.data["warnings"] and "other.lua" in res.data["warnings"][0]


def test_nothing_to_push(src):
    ctl, _, runner = _setup(src, live=True)
    runner.handlers[:] = []
    runner.on(lambda a: a[0] == "rsync", "")
    res = ctl.deploy(str(src), confirm=True)
    assert res.success and "already matches" in res.message
    assert all("--dry-run" in c for c in _rsyncs(runner))
