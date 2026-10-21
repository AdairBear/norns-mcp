"""MCP stdio server for the norns. stdout is the MCP channel: nothing here may print.

Reads run any time. Writes (load, start, stop, set param, key, enc, deploy) are dry runs
unless the server was started with --live, and even then each call needs confirm=true.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from typing import Annotated, Any

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from pydantic import BeforeValidator

from norns_mcp.config import Settings
from norns_mcp.controller import NornsController
from norns_mcp.core.models import ToolResult
from norns_mcp.core.serialize import serialize_result
from norns_mcp.core.signals import exit_on_signals

logger = logging.getLogger("norns_mcp")

INSTRUCTIONS = """\
Talks to a monome norns (norns.local) over SSH (reads, deploy), matron's websocket Lua
REPL (script state, params, load/stop) and OSC (keys, encoders). Every result is
{success, message, data}. READS are always allowed: norns_status, norns_list_scripts,
norns_current_script, norns_read_script, norns_params, norns_get_param, norns_logs,
norns_dust_inventory. WRITES (norns_screenshot, norns_load_script, norns_start_script,
norns_stop_script, norns_set_param, norns_key, norns_enc, norns_deploy) are DRY RUN unless
the server was started with --live; then each call ALSO needs confirm=true. A dry run
reports data.would_send and sends nothing. Never deletes files on the norns; deploy backs
up the target folder first and never uses rsync --delete. Call norns_status first.
"""


def _no_bool(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    return value


Int = Annotated[int, BeforeValidator(_no_bool)]
NumOrStr = Annotated[float | int | str, BeforeValidator(_no_bool)]


def _fail(message: str) -> dict[str, Any]:
    return {"success": False, "message": message, "data": None}


async def _call(name: str, fn: Callable[..., ToolResult], *args: Any) -> dict[str, Any]:
    """Run a synchronous controller method off the event loop; never raise."""
    try:
        return serialize_result(await anyio.to_thread.run_sync(fn, *args))
    except Exception as exc:
        logger.exception("tool %s failed", name)
        return _fail(f"{name} failed: {type(exc).__name__}: {exc}")


def build_server(ctl: NornsController) -> MCPServer:
    mcp = MCPServer("norns-mcp", instructions=INSTRUCTIONS)

    # ── reads ──
    @mcp.tool()
    async def norns_status() -> dict[str, Any]:
        """SSH reachable, norns version, service states, REPL reachable, running script,
        and whether this server is live. Sends nothing that changes the norns."""
        return await _call("norns_status", ctl.status)

    @mcp.tool()
    async def norns_list_scripts() -> dict[str, Any]:
        """Every folder in ~/dust/code with its .lua files. data.scripts[].loadable lists the
        names norns_load_script accepts ('awake' or 'folder/file')."""
        return await _call("norns_list_scripts", ctl.list_scripts)

    @mcp.tool()
    async def norns_current_script() -> dict[str, Any]:
        """The running script (name, path, engine, param count) from matron. name 'none'
        means nothing is loaded."""
        return await _call("norns_current_script", ctl.current_script)

    @mcp.tool()
    async def norns_read_script(script: str, file: str | None = None) -> dict[str, Any]:
        """Source of a script: script='awake' reads awake/awake.lua; file='lib/x.lua' reads
        another file in that folder. Read only, max 256 KB."""
        return await _call("norns_read_script", ctl.read_script, script, file)

    @mcp.tool()
    async def norns_params() -> dict[str, Any]:
        """All params of the running script plus the system params: index, id, type, name,
        raw value and the display string the norns shows."""
        return await _call("norns_params", ctl.params)

    @mcp.tool()
    async def norns_get_param(param_id: str) -> dict[str, Any]:
        """One param's raw value and display string."""
        return await _call("norns_get_param", ctl.get_param, param_id)

    @mcp.tool()
    async def norns_logs(
        unit: str = "matron",
        lines: Int = 100,
        since: str | None = None,
        hide_repl_echo: bool = True,
    ) -> dict[str, Any]:
        """journalctl for norns-<unit>.service: matron (Lua, script errors), crone, sclang,
        maiden, jack, watcher. lines 1-2000; since like '10 min ago' or '2026-10-20 18:00'.
        hide_repl_echo drops this server's own REPL traffic from the matron journal."""
        return await _call("norns_logs", ctl.logs, unit, lines, since, hide_repl_echo)

    @mcp.tool()
    async def norns_dust_inventory(
        compare_backup: bool = False, hashes: bool = False
    ) -> dict[str, Any]:
        """Files and bytes in dust per top folder and per script. compare_backup=true checks
        against the Mac backup's MANIFEST.sha256 by path and size; hashes=true also compares
        sha256 for files present in both (slower)."""
        return await _call("norns_dust_inventory", ctl.dust_inventory, compare_backup, hashes)

    @mcp.tool()
    async def norns_screenshot(confirm: bool = False) -> dict[str, Any]:
        """WRITE (small). Grab the norns screen (640x384 PNG of the 128x64 display) to the
        Mac. It makes matron write one fixed temp file on the norns
        (/tmp/norns-mcp-screen.png), so it is dry run unless --live; live needs
        confirm=true."""
        return await _call("norns_screenshot", ctl.screenshot, confirm)

    # ── writes (gated) ──
    @mcp.tool()
    async def norns_load_script(script: str, confirm: bool = False) -> dict[str, Any]:
        """WRITE. Load a script by name ('awake' or 'folder/file'); replaces the running
        one. Dry run unless --live; live needs confirm=true."""
        return await _call("norns_load_script", ctl.load_script, script, confirm)

    @mcp.tool()
    async def norns_start_script(
        script: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """WRITE. Start a script (same as load). With no name, start the last script the
        norns remembers. Dry run unless --live; live needs confirm=true."""
        return await _call("norns_start_script", ctl.start_script, script, confirm)

    @mcp.tool()
    async def norns_stop_script(confirm: bool = False) -> dict[str, Any]:
        """WRITE. Stop the running script and free its engine (what K1+K3 'stop' does on the
        home menu). Dry run unless --live; live needs confirm=true."""
        return await _call("norns_stop_script", ctl.stop_script, confirm)

    @mcp.tool()
    async def norns_set_param(
        param_id: str, value: NumOrStr, via: str = "repl", confirm: bool = False
    ) -> dict[str, Any]:
        """WRITE. Set a param (option params take the 1-based index) and read it back.
        via='repl' (default, verified path) or 'osc' (/param/<id>, unverified). Dry run
        unless --live; live needs confirm=true."""
        return await _call("norns_set_param", ctl.set_param, param_id, value, via, confirm)

    @mcp.tool()
    async def norns_key(n: Int, z: Int | None = None, confirm: bool = False) -> dict[str, Any]:
        """WRITE. Press key n (1-3) over OSC; z=1 down / z=0 up, omitted = press and
        release. Dry run unless --live; live needs confirm=true."""
        return await _call("norns_key", ctl.key, n, z, confirm)

    @mcp.tool()
    async def norns_enc(n: Int, delta: Int, confirm: bool = False) -> dict[str, Any]:
        """WRITE. Turn encoder n (1-3) by delta (-64..64, not 0) over OSC. Dry run unless
        --live; live needs confirm=true."""
        return await _call("norns_enc", ctl.enc, n, delta, confirm)

    @mcp.tool()
    async def norns_deploy(
        local_dir: str, name: str | None = None, confirm: bool = False
    ) -> dict[str, Any]:
        """WRITE. Push a script folder from this Mac to ~/dust/code/<name>/ with rsync over
        SSH. Always runs an rsync dry run first and returns the plan. Never deletes on the
        norns (no --delete). Live (needs --live and confirm=true): backs up the existing
        folder to ~/norns-mcp-backups/ first, then pushes, then checks sha256 per file."""
        return await _call("norns_deploy", ctl.deploy, local_dir, name, confirm)

    return mcp


def build_controller(settings: Settings) -> NornsController:
    from norns_mcp.transports import LocalRunner, SshShell, UdpOsc, WsRepl

    return NornsController(
        settings, SshShell(settings), WsRepl(settings), UdpOsc(settings), LocalRunner()
    )


def main(
    argv: list[str] | None = None,
    factory: Callable[[Settings], NornsController] | None = None,
    run: Callable[[MCPServer], None] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="norns-mcp", description="MCP server for a monome norns.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--live",
        action="store_true",
        help="allow writes (or NORNS_MCP_LIVE=1); each write still needs confirm=true",
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="no writes, even if NORNS_MCP_LIVE is set"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_env(live=False if args.dry_run else (True if args.live else None))
    logger.info(
        "norns-mcp starting: %s",
        "LIVE (writes need confirm)" if settings.live else "DRY RUN (no writes)",
    )
    ctl = (factory or build_controller)(settings)
    with exit_on_signals(lambda signum: 0):
        try:
            (run or (lambda s: s.run()))(build_server(ctl))
        except KeyboardInterrupt:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
