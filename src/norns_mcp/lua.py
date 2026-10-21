"""The fixed Lua this server sends to matron, and the parser for what comes back.

Every snippet is ONE line (see ``safety.check_lua_line``). Reads print their answer
between nonce markers, ``<NMCP:xxxx>...</NMCP:xxxx>``, so output from a running script
or another REPL client (maiden) on the same bus cannot be mistaken for the answer.
Fields are tab-separated and rows newline-separated; ``\\``, tab and newline inside a
field are escaped by the Lua helper ``E`` and unescaped by ``unescape``.

Verified on the norns 2026-10-20 (version 231114): ``print`` reaches the websocket,
``io.write`` does NOT (stdout is closed in matron), a statement ends with ``<ok>``.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from norns_mcp.safety import check_lua_line, lua_str, lua_value

# Escapes \, tab, newline. gsub returns two values; the parentheses keep only the string.
_E = (
    "local function E(s) s=tostring(s); "
    'return (s:gsub("\\\\","\\\\\\\\"):gsub("\\t","\\\\t"):gsub("\\n","\\\\n")) end; '
)

# paramset.lua, norns 231114
PARAM_TYPES = {
    0: "separator",
    1: "number",
    2: "option",
    3: "control",
    4: "file",
    5: "taper",
    6: "trigger",
    7: "group",
    8: "text",
    9: "binary",
}


@dataclass(frozen=True)
class Snippet:
    code: str
    marker_end: str
    nonce: str


def _wrap(body: str, payload_expr: str) -> Snippet:
    nonce = secrets.token_hex(4)
    start, end = f"<NMCP:{nonce}>", f"</NMCP:{nonce}>"
    code = f'do {_E}{body} print("{start}" .. {payload_expr} .. "{end}") end'
    return Snippet(check_lua_line(code), end, nonce)


def unescape(field: str) -> str:
    return re.sub(r"\\(\\|t|n)", lambda m: {"\\": "\\", "t": "\t", "n": "\n"}[m.group(1)], field)


class LuaError(RuntimeError):
    pass


def extract(raw: str, snippet: Snippet) -> str:
    """The text between this snippet's markers. Raises ``LuaError`` with matron's message
    if the chunk errored, or if no marker came back."""
    start = f"<NMCP:{snippet.nonce}>"
    i = raw.find(start)
    j = raw.find(snippet.marker_end, i + 1) if i >= 0 else -1
    if i >= 0 and j >= 0:
        return raw[i + len(start) : j]
    if "stack traceback:" in raw or raw.lstrip().startswith("lua:"):
        raise LuaError(lua_error_text(raw))
    if not raw.strip():
        raise LuaError("no reply from matron (timeout)")
    raise LuaError(
        "matron replied without the answer marker; a script may have replaced print(). "
        f"Raw reply: {raw.strip()[:300]!r}"
    )


def lua_error_text(raw: str) -> str:
    text = raw.replace("lua: ", "", 1).strip()
    return text.split("\nstack traceback:")[0].strip() or text


def rows(payload: str) -> list[list[str]]:
    if payload == "":
        return []
    return [[unescape(f) for f in line.split("\t")] for line in payload.split("\n")]


# ── Reads ────────────────────────────────────────────────────────────────────────


def current_script() -> Snippet:
    """name, script path, shortname, engine, params.count, norns version."""
    return _wrap(
        "",
        'E(norns.state.name).."\\t"..E(norns.state.script).."\\t"..E(norns.state.shortname)'
        '.."\\t"..E(engine.name).."\\t"..E(params.count).."\\t"..E(norns.version and '
        'norns.version.update or "")',
    )


def params_dump() -> Snippet:
    """One row per param: index, id, type, name, raw value, display string."""
    body = (
        "local T={}; for i=1,params.count do local p=params:lookup_param(i); "
        "local ok1,v=pcall(function() return params:get(i) end); "
        "local ok2,s=pcall(function() return params:string(i) end); "
        'T[#T+1]=E(i).."\\t"..E(p.id).."\\t"..E(p.t).."\\t"..E(p.name).."\\t"'
        '..E(ok1 and v or "").."\\t"..E(ok2 and s or "") end;'
    )
    return _wrap(body, 'table.concat(T,"\\n")')


def param_get(param_id: str) -> Snippet:
    """``found\\tvalue\\tstring`` or ``missing``."""
    pid = lua_str(param_id)
    body = (
        f'local R; if params.lookup[{pid}]==nil then R="missing" else '
        f'R="found\\t"..E(params:get({pid})).."\\t"..E(params:string({pid})) end;'
    )
    return _wrap(body, "R")


def screenshot(remote_path: str) -> Snippet:
    body = f"_norns.screen_export_screenshot({lua_str(remote_path)});"
    return _wrap(body, '"saved"')


# ── Writes (only sent behind the live gate + confirm) ────────────────────────────


def load_script(dust_relative: str) -> Snippet:
    """``norns.script.load`` takes a path relative to dust (``code/awake/awake.lua``).
    Answers the norns' own clock, read just BEFORE the load, so the journal can be read
    from that second on (``journalctl --since``)."""
    body = f'local T=os.date("%Y-%m-%d %H:%M:%S"); norns.script.load({lua_str(dust_relative)});'
    return _wrap(body, "T")


def stop_script() -> Snippet:
    """What K1+K3 'stop' on the home menu does (lua/core/menu/home.lua, norns 231114),
    minus the UI lock."""
    body = 'norns.script.clear(); _norns.free_engine(); _menu.m["PARAMS"].reset();'
    return _wrap(body, '"stopped"')


def param_set(param_id: str, value: object) -> Snippet:
    """Set, then read back. ``missing`` if the id is not registered."""
    pid = lua_str(param_id)
    body = (
        f'local R; if params.lookup[{pid}]==nil then R="missing" else '
        f"params:set({pid}, {lua_value(value)}); "
        f'R="set\\t"..E(params:get({pid})).."\\t"..E(params:string({pid})) end;'
    )
    return _wrap(body, "R")
