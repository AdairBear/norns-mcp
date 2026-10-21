import pytest

from norns_mcp.config import REMOTE_BACKUPS, REMOTE_CODE
from norns_mcp.safety import (
    UnsafeCommand,
    check_lua_line,
    check_name,
    check_param_id,
    check_remote_argv,
    check_rsync_argv,
    lua_str,
    lua_value,
    remote_command,
    split_script,
)


@pytest.mark.parametrize("name", ["awake", "mx.synths", "_install_helper", "a-b_c.1"])
def test_good_names(name):
    assert check_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "..", "a/b", "../x", ".hidden", "a b", "a;rm", "x" * 70, "a..b"]
)
def test_bad_names(name):
    with pytest.raises(ValueError):
        check_name(name)


def test_split_script():
    assert split_script("awake") == ("awake", "awake")
    assert split_script("awake.lua") == ("awake", "awake")
    assert split_script("we/study1") == ("we", "study1")
    for bad in ["a/b/c", "../x", "/abs", ""]:
        with pytest.raises(ValueError):
            split_script(bad)


@pytest.mark.parametrize("pid", ["cutoff", "1_level", "output_level", "grid/rows"])
def test_param_ids(pid):
    assert check_param_id(pid) == pid


@pytest.mark.parametrize("pid", ["", "a b", 'x")', "a..b", "x;y"])
def test_bad_param_ids(pid):
    with pytest.raises(ValueError):
        check_param_id(pid)


@pytest.mark.parametrize(
    "argv",
    [
        ["sudo", "ls"],
        ["ls", "sudo"],
        ["rm", "-rf", "/home/we/dust"],
        ["/bin/rm", "x"],
        ["mv", "a", "b"],
        ["find", "/home/we/dust", "-delete"],
        ["find", "/home/we/dust", "-exec", "rm", "{}", ";"],
        ["apt-get", "upgrade"],
        ["reboot"],
        ["systemctl", "restart", "norns-matron"],
        ["journalctl", "--vacuum-time=1s"],
        ["mkdir", "-p", "/home/we/dust/code/new"],
        ["cp", "-a", "/home/we/dust/code/awake", "/home/we/dust/code/awake2"],
        ["cp", "-a", "/etc/passwd", f"{REMOTE_BACKUPS}/x"],
        ["cp", "-a", "/home/we/dust/code/awake", REMOTE_BACKUPS],
        ["cp", "-r", "/home/we/dust/code/awake", f"{REMOTE_BACKUPS}/x"],
        ["bash", "-c", "ls"],
        ["python3", "-c", "1"],
        ["update"],
        [],
    ],
)
def test_remote_guard_refuses(argv):
    with pytest.raises(UnsafeCommand):
        check_remote_argv(argv)


@pytest.mark.parametrize(
    "argv",
    [
        ["cat", "/home/we/version.txt"],
        ["find", "/home/we/dust", "-type", "f", "-printf", "%P\\t%s\\n"],
        ["journalctl", "-u", "norns-matron.service", "-n", "10", "--no-pager"],
        ["systemctl", "is-active", "norns-matron.service"],
        ["mkdir", "-p", REMOTE_BACKUPS],
        ["cp", "-a", "/home/we/dust/code/awake", f"{REMOTE_BACKUPS}/awake-20261020T000000Z"],
        ["sha256sum", "--", "/home/we/dust/code/awake/awake.lua"],
    ],
)
def test_remote_guard_allows(argv):
    assert check_remote_argv(argv) == argv


def test_remote_command_quotes_metacharacters():
    cmd = remote_command(["cat", "/home/we/dust/code/a;b $(x)/f.lua"])
    assert cmd == "cat '/home/we/dust/code/a;b $(x)/f.lua'"


def _rsync(*flags, dest=f"norns:{REMOTE_CODE}/awake/"):
    return ["rsync", "-rlt", *flags, "-e", "ssh -o BatchMode=yes", "/tmp/awake/", dest]


@pytest.mark.parametrize(
    "flag",
    [
        "--delete",
        "--delete-after",
        "--del",
        "--remove-source-files",
        "--inplace",
        "--rsync-path=sudo rsync",
        "-a",
        "-rltz",
        "--force",
    ],
)
def test_rsync_guard_refuses_flags(flag):
    with pytest.raises(UnsafeCommand):
        check_rsync_argv(_rsync(flag), f"{REMOTE_CODE}/awake")


def test_rsync_guard_refuses_other_destinations():
    for dest in [
        "norns:/home/we/dust/code/",
        "norns:/etc/",
        "norns:/home/we/dust/code/awake2/",
        "/local/only/",
    ]:
        with pytest.raises(UnsafeCommand):
            check_rsync_argv(_rsync(dest=dest), f"{REMOTE_CODE}/awake")


def test_rsync_guard_allows_plain_push():
    argv = _rsync("--itemize-changes", "--dry-run", "--exclude=.git")
    assert check_rsync_argv(argv, f"{REMOTE_CODE}/awake") == argv


def test_lua_quoting():
    assert lua_str('a"b\\c') == '"a\\"b\\\\c"'
    assert lua_str("x\ny") == '"x\\010y"'
    assert lua_value(3) == "3"
    assert lua_value(0.5) == "0.5"
    assert lua_value('") os.execute("x') == '"\\") os.execute(\\"x"'
    for bad in [True, float("nan"), float("inf"), None, [1]]:
        with pytest.raises(ValueError):
            lua_value(bad)


def test_lua_line_must_be_single_line():
    assert check_lua_line("print(1)") == "print(1)"
    with pytest.raises(ValueError):
        check_lua_line("print(1)\nprint(2)")


def test_backup_paths_with_dotdot_are_refused():
    for argv in (
        ["mkdir", "-p", f"{REMOTE_BACKUPS}/../dust/code/x"],
        ["cp", "-a", "/home/we/dust/code/awake", f"{REMOTE_BACKUPS}/../dust/code/x"],
        ["cp", "-a", "/home/we/dust/../.ssh", f"{REMOTE_BACKUPS}/x"],
    ):
        with pytest.raises(UnsafeCommand):
            check_remote_argv(argv)
    with pytest.raises(UnsafeCommand):
        check_rsync_argv(_rsync(dest=f"norns:{REMOTE_CODE}/awake/../mlr/"), f"{REMOTE_CODE}/awake")
