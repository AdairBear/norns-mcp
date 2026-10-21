"""``nornsctl``: the same controller methods as the MCP server, from a terminal.

Writes are dry runs unless BOTH ``--live`` (before the command) and ``--confirm`` (after
it) are given, e.g. ``nornsctl --live load awake --confirm``.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import TextIO

from norns_mcp.config import LOG_UNITS, Settings
from norns_mcp.controller import NornsController
from norns_mcp.core.serialize import serialize_result


def _value(raw: str) -> float | int | str:
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nornsctl", description="Read and (with --live) drive a norns."
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--live", action="store_true", help="allow writes (each also needs --confirm)"
    )
    mode.add_argument(
        "--dry-run", action="store_true", help="never write, even if NORNS_MCP_LIVE=1"
    )
    p.add_argument("--json", action="store_true", help="print the full JSON result")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="reachability, version, services, running script")
    sub.add_parser("scripts", help="list ~/dust/code")
    sub.add_parser("current", help="running script")
    s = sub.add_parser("read", help="print a script's source")
    s.add_argument("script")
    s.add_argument("--file")
    sub.add_parser("params", help="all params")
    s = sub.add_parser("get", help="one param")
    s.add_argument("param_id")
    s = sub.add_parser("logs", help="journalctl for a norns unit")
    s.add_argument("unit", nargs="?", default="matron", choices=LOG_UNITS)
    s.add_argument("-n", "--lines", type=int, default=100)
    s.add_argument("--since")
    s.add_argument("--raw", action="store_true", help="keep norns-mcp's own REPL echo")
    s = sub.add_parser("inventory", help="dust inventory")
    s.add_argument("--compare-backup", action="store_true")
    s.add_argument("--hashes", action="store_true")

    def write(name: str, help_: str) -> argparse.ArgumentParser:
        sp = sub.add_parser(name, help=f"WRITE: {help_}")
        sp.add_argument("--confirm", action="store_true", help="really do it (needs --live)")
        return sp

    write("screenshot", "save the norns screen as PNG on the Mac (writes a /tmp file there)")
    s = write("load", "load a script")
    s.add_argument("script")
    s = write("start", "start a script (default: the last one)")
    s.add_argument("script", nargs="?")
    write("stop", "stop the running script")
    s = write("set", "set a param")
    s.add_argument("param_id")
    s.add_argument("value")
    s.add_argument("--via", choices=("repl", "osc"), default="repl")
    s = write("key", "press a key over OSC")
    s.add_argument("n", type=int)
    s.add_argument("z", type=int, nargs="?")
    s = write("enc", "turn an encoder over OSC")
    s.add_argument("n", type=int)
    s.add_argument("delta", type=int)
    s = write("deploy", "rsync a script folder to the norns (dry run by default)")
    s.add_argument("local_dir")
    s.add_argument("--name")
    return p


def run(ctl: NornsController, args: argparse.Namespace):
    c = getattr(args, "confirm", False)
    match args.cmd:
        case "status":
            return ctl.status()
        case "scripts":
            return ctl.list_scripts()
        case "current":
            return ctl.current_script()
        case "read":
            return ctl.read_script(args.script, args.file)
        case "params":
            return ctl.params()
        case "get":
            return ctl.get_param(args.param_id)
        case "logs":
            return ctl.logs(args.unit, args.lines, args.since, not args.raw)
        case "inventory":
            return ctl.dust_inventory(args.compare_backup, args.hashes)
        case "screenshot":
            return ctl.screenshot(c)
        case "load":
            return ctl.load_script(args.script, c)
        case "start":
            return ctl.start_script(args.script, c)
        case "stop":
            return ctl.stop_script(c)
        case "set":
            return ctl.set_param(args.param_id, _value(args.value), args.via, c)
        case "key":
            return ctl.key(args.n, args.z, c)
        case "enc":
            return ctl.enc(args.n, args.delta, c)
        case "deploy":
            return ctl.deploy(args.local_dir, args.name, c)
    raise AssertionError(args.cmd)


def _short(result, out: TextIO) -> None:
    print(("OK  " if result.success else "FAIL ") + result.message, file=out)
    data = result.data or {}
    if "text" in data:
        print(data["text"], file=out, end="" if data["text"].endswith("\n") else "\n")
    elif "params" in data:
        for p in data["params"]:
            if p["type"] not in ("separator", "group"):
                print(f"  {p['index']:>3} {p['id']:<28} {p['display']}", file=out)
    elif "scripts" in data and isinstance(data["scripts"], list):
        for s in data["scripts"]:
            print(f"  {s['folder']:<20} {' '.join(s['loadable'])}", file=out)
    elif data:
        print(json.dumps(serialize_result(result)["data"], indent=2), file=out)


def main(
    argv: list[str] | None = None,
    factory: Callable[[Settings], NornsController] | None = None,
    out: TextIO = sys.stdout,
) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env(live=False if args.dry_run else (True if args.live else None))
    if factory is None:
        from norns_mcp.server import build_controller

        factory = build_controller
    result = run(factory(settings), args)
    if args.json:
        print(json.dumps(serialize_result(result), indent=2), file=out)
    else:
        _short(result, out)
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
