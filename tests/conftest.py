import pytest

from norns_mcp import controller


@pytest.fixture(autouse=True)
def _no_network_http(request, monkeypatch):
    """Fake-based tests must not reach maiden's HTTP API; device tests may."""
    if request.node.get_closest_marker("device"):
        return

    def refuse(url: str) -> str:
        raise OSError(f"network disabled in tests: {url}")

    monkeypatch.setattr(controller, "_http_get", refuse)
