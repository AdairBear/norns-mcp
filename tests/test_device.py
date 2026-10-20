"""Read-only checks against the real norns. Run with: uv run pytest -m device

They never write: the controller is built with live=False, and the only file touched on
the norns is the fixed screenshot temp file.
"""

import pytest

from norns_mcp.config import Settings
from norns_mcp.server import build_controller

pytestmark = pytest.mark.device


@pytest.fixture(scope="module")
def ctl():
    return build_controller(Settings(live=False))


def test_status(ctl):
    res = ctl.status()
    assert res.success, res
    assert set(res.data["services"].values()) == {"active"}


def test_list_and_read(ctl):
    res = ctl.list_scripts()
    assert res.success and res.data["scripts"]
    folder = res.data["scripts"][0]["loadable"][0]
    assert ctl.read_script(folder).success


def test_current_and_params(ctl):
    assert ctl.current_script().success
    res = ctl.params()
    assert res.success and any(p["id"] == "output_level" for p in res.data["params"])
    assert ctl.get_param("output_level").success


def test_logs_and_inventory(ctl):
    assert ctl.logs("matron", 5).success
    assert ctl.dust_inventory().data["total_files"] > 0


def test_writes_stay_dry(ctl):
    before = ctl.current_script().data["name"]
    res = ctl.load_script("awake", confirm=True)
    assert res.success and res.data["sent"] is False
    assert ctl.current_script().data["name"] == before


def test_deploy_dry_run(ctl, tmp_path):
    (tmp_path / "nmcp_probe").mkdir()
    (tmp_path / "nmcp_probe" / "nmcp_probe.lua").write_text("-- probe\n")
    res = ctl.deploy(str(tmp_path / "nmcp_probe"), confirm=True)
    assert res.success and res.data["sent"] is False
    assert res.data["plan"]["new_files"] == ["nmcp_probe.lua"]
