import pytest

from norns_mcp import lua


def _reply(sn: lua.Snippet, payload: str, noise: str = "") -> str:
    return f"{noise}<NMCP:{sn.nonce}>{payload}</NMCP:{sn.nonce}>\n<ok>\n"


def test_snippets_are_single_line_and_marked():
    for sn in [
        lua.current_script(),
        lua.params_dump(),
        lua.param_get("x"),
        lua.screenshot("/tmp/norns-mcp-screen.png"),
        lua.load_script("code/a/a.lua"),
        lua.stop_script(),
        lua.param_set("cutoff", 0.5),
    ]:
        assert "\n" not in sn.code
        assert sn.code.startswith("do ") and sn.code.endswith(" end")
        assert f"<NMCP:{sn.nonce}>" in sn.code and sn.marker_end in sn.code


def test_nonces_differ():
    assert lua.current_script().nonce != lua.current_script().nonce


def test_extract_ignores_other_output():
    sn = lua.current_script()
    raw = _reply(sn, "awake\tx", noise="<NMCP:deadbeef>fake</NMCP:deadbeef>\nhello\n")
    assert lua.extract(raw, sn) == "awake\tx"


def test_extract_reports_lua_errors():
    sn = lua.param_get("x")
    raw = "lua: stdin:1: attempt to index a nil value\nstack traceback:\n\t[C]: in ?\n"
    with pytest.raises(lua.LuaError, match="attempt to index a nil value"):
        lua.extract(raw, sn)


def test_extract_without_marker_says_print_may_be_replaced():
    with pytest.raises(lua.LuaError, match="replaced print"):
        lua.extract("<ok>\n", lua.current_script())
    with pytest.raises(lua.LuaError, match="timeout"):
        lua.extract("", lua.current_script())


def test_rows_unescape():
    assert lua.rows("a\\tb\tc\\nd\te\\\\f\nx") == [["a\tb", "c\nd", "e\\f"], ["x"]]
    assert lua.rows("") == []


def test_param_set_embeds_quoted_values():
    sn = lua.param_set("name", 'x") os.execute("boom')
    assert 'params:set("name", "x\\") os.execute(\\"boom")' in sn.code


def test_stop_matches_home_menu():
    code = lua.stop_script().code
    assert "norns.script.clear()" in code and "_norns.free_engine()" in code
