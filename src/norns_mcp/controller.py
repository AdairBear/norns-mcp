"""NornsController: every operation the MCP server and ``nornsctl`` expose.

Conventions (same as m8-mcp):
- Methods never raise for bad input or an unreachable norns: they return
  ``ToolResult(success=False, ...)``.
- Reads run any time. Every write goes through ``_gate``: without ``live`` it is a dry run
  that reports exactly what it would send (``data["would_send"]``) and sends nothing;
  with ``live`` it still needs ``confirm=True`` on the call.
- Writes are: load a script, stop the script, set a param, OSC key/enc, deploy.
  Nothing here deletes a file on the norns, runs sudo, or touches the system.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from norns_mcp import lua
from norns_mcp.config import (
    LOG_UNITS,
    REMOTE_CODE,
    REMOTE_DUST,
    REMOTE_HOME,
    REMOTE_SCREENSHOT,
    Settings,
)
from norns_mcp.core.models import ToolResult
from norns_mcp.deploy import Deployer
from norns_mcp.safety import UnsafeCommand, check_name, check_param_id, split_script
from norns_mcp.transports import Osc, Repl, Runner, Shell, ssh_base

MAX_LOG_LINES = 2000
MAX_READ_BYTES = 256 * 1024


def _fail(message: str, **data: Any) -> ToolResult:
    return ToolResult(False, message, data or None)


@dataclass
class NornsController:
    settings: Settings
    shell: Shell
    repl: Repl
    osc: Osc
    runner: Runner
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.deployer = Deployer(self.settings, self.shell, self.runner)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _lua(self, snippet: lua.Snippet, timeout: float | None = None) -> str:
        raw = self.repl.send(snippet.code, timeout=timeout, marker_end=snippet.marker_end)
        return lua.extract(raw, snippet)

    def _gate(self, action: str, confirm: bool, would_send: dict[str, Any]) -> ToolResult | None:
        """None = go ahead. Otherwise the dry-run / needs-confirm result to return."""
        if not self.settings.live:
            return ToolResult(
                True,
                f"DRY RUN: {action} not sent (server not started with --live).",
                {"live": False, "sent": False, "would_send": would_send},
            )
        if confirm is not True:
            return ToolResult(
                False,
                f"{action} needs confirm=true (live write).",
                {"live": True, "sent": False, "would_send": would_send},
            )
        return None

    def _script_exists(self, folder: str, file: str) -> bool:
        res = self.shell.run(["test", "-f", f"{REMOTE_CODE}/{folder}/{file}.lua"])
        return res.returncode == 0

    # ── reads ────────────────────────────────────────────────────────────────

    def status(self) -> ToolResult:
        data: dict[str, Any] = {"host": self.settings.host, "live": self.settings.live}
        try:
            res = self.shell.run(["cat", f"{REMOTE_HOME}/version.txt"])
            data["ssh"] = res.returncode == 0
            data["norns_version"] = res.stdout.strip() if res.returncode == 0 else None
            if res.returncode != 0:
                data["ssh_error"] = res.stderr.strip()[-300:]
            units = [f"norns-{u}.service" for u in LOG_UNITS]
            res = self.shell.run(["systemctl", "is-active", *units])
            states = res.stdout.split()
            data["services"] = dict(zip(units, states, strict=False))
        except UnsafeCommand as exc:  # a bug, not a device problem
            return _fail(f"internal guard refused a command: {exc}")
        try:
            row = lua.rows(self._lua(lua.current_script()))[0]
            data["repl"] = True
            data["script"] = {"name": row[0], "path": row[1], "engine": row[3]}
        except (ConnectionError, lua.LuaError) as exc:
            data["repl"] = False
            data["repl_error"] = str(exc)
        ok = bool(data.get("ssh")) and bool(data.get("repl"))
        return ToolResult(ok, "norns reachable." if ok else "norns partly unreachable.", data)

    def list_scripts(self) -> ToolResult:
        res = self.shell.run(
            [
                "find",
                REMOTE_CODE,
                "-mindepth",
                "2",
                "-maxdepth",
                "2",
                "-name",
                "*.lua",
                "-type",
                "f",
                "-printf",
                "%P\\n",
            ]
        )
        if res.returncode != 0:
            return _fail(f"could not list {REMOTE_CODE}: {res.stderr.strip()}")
        files: dict[str, list[str]] = defaultdict(list)
        for line in sorted(res.stdout.split("\n")):
            if "/" in line:
                folder, name = line.split("/", 1)
                files[folder].append(name.removesuffix(".lua"))
        scripts = [
            {
                "folder": folder,
                "entry": folder if folder in names else None,
                "files": sorted(names),
                "loadable": [folder if n == folder else f"{folder}/{n}" for n in sorted(names)],
            }
            for folder, names in sorted(files.items())
        ]
        return ToolResult(True, f"{len(scripts)} script folders.", {"scripts": scripts})

    def current_script(self) -> ToolResult:
        try:
            row = lua.rows(self._lua(lua.current_script()))[0]
        except (ConnectionError, lua.LuaError) as exc:
            return _fail(f"current script read failed: {exc}")
        name, path, short, engine, count, version = (row + [""] * 6)[:6]
        loaded = name not in ("", "none")
        return ToolResult(
            True,
            f"running: {name}" if loaded else "no script loaded.",
            {
                "loaded": loaded,
                "name": name,
                "path": path,
                "shortname": short,
                "engine": None if engine == "nil" else engine,
                "param_count": int(count) if count.isdigit() else None,
                "norns_version": version or None,
            },
        )

    def read_script(self, script: str, file: str | None = None) -> ToolResult:
        """Read a .lua (or any text file) inside one script folder."""
        try:
            folder, default_file = split_script(script)
            rel = file if file is not None else f"{default_file}.lua"
            parts = rel.split("/")
            if not all(part and part != ".." and not part.startswith(".") for part in parts):
                raise ValueError(f"invalid file {rel!r}")
            for part in parts:
                check_name(part, "file path part")
        except ValueError as exc:
            return _fail(str(exc))
        path = f"{REMOTE_CODE}/{folder}/{rel}"
        size = self.shell.run(["stat", "-c", "%s", path])
        if size.returncode != 0:
            return _fail(f"not found: {path}")
        if int(size.stdout.strip() or 0) > MAX_READ_BYTES:
            return _fail(f"{path} is larger than {MAX_READ_BYTES} bytes; not read.")
        res = self.shell.run(["cat", path])
        if res.returncode != 0:
            return _fail(f"read failed: {res.stderr.strip()}")
        return ToolResult(
            True, f"{path} ({len(res.stdout)} chars)", {"path": path, "text": res.stdout}
        )

    def params(self) -> ToolResult:
        try:
            table = lua.rows(self._lua(lua.params_dump(), timeout=8.0))
        except (ConnectionError, lua.LuaError) as exc:
            return _fail(f"param read failed: {exc}")
        out = []
        for row in table:
            index, pid, ptype, name, value, text = (row + [""] * 6)[:6]
            kind = lua.PARAM_TYPES.get(int(ptype), ptype) if ptype.isdigit() else ptype
            out.append(
                {
                    "index": int(index) if index.isdigit() else index,
                    "id": pid,
                    "type": kind,
                    "name": name,
                    "value": _number(value),
                    "display": text,
                }
            )
        settable = [p for p in out if p["type"] not in ("separator", "group")]
        return ToolResult(True, f"{len(settable)} params ({len(out)} rows).", {"params": out})

    def get_param(self, param_id: str) -> ToolResult:
        try:
            check_param_id(param_id)
            row = lua.rows(self._lua(lua.param_get(param_id)))[0]
        except ValueError as exc:
            return _fail(str(exc))
        except (ConnectionError, lua.LuaError) as exc:
            return _fail(f"param read failed: {exc}")
        if row[0] != "found":
            return _fail(f"no param {param_id!r} in the running script.", id=param_id)
        return ToolResult(
            True,
            f"{param_id} = {row[2]}",
            {"id": param_id, "value": _number(row[1]), "display": row[2]},
        )

    def logs(self, unit: str = "matron", lines: int = 100, since: str | None = None) -> ToolResult:
        if unit not in LOG_UNITS:
            return _fail(f"unknown unit {unit!r}; expected one of {', '.join(LOG_UNITS)}")
        if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= MAX_LOG_LINES:
            return _fail(f"lines must be 1..{MAX_LOG_LINES}")
        argv = [
            "journalctl",
            "-u",
            f"norns-{unit}.service",
            "-n",
            str(lines),
            "--no-pager",
            "-o",
            "short-iso",
        ]
        if since:
            if not all(c.isalnum() or c in " :-+." for c in since):
                return _fail(f"invalid since {since!r}")
            argv += ["--since", since]
        res = self.shell.run(argv)
        if res.returncode != 0:
            return _fail(f"journalctl failed: {res.stderr.strip()}")
        text = res.stdout
        return ToolResult(
            True, f"{len(text.splitlines())} lines from norns-{unit}.", {"unit": unit, "text": text}
        )

    def dust_inventory(self, compare_backup: bool = False, hashes: bool = False) -> ToolResult:
        """Counts and sizes per top folder and per script. ``compare_backup`` checks the norns
        against the Mac copy's MANIFEST.sha256 by path + size; ``hashes`` also runs
        sha256sum on the norns for files present in both, so the hash decides, not a model."""
        res = self.shell.run(["find", REMOTE_DUST, "-type", "f", "-printf", "%P\\t%s\\t%T@\\n"])
        if res.returncode != 0:
            return _fail(f"find failed: {res.stderr.strip()}")
        files: dict[str, int] = {}
        for line in res.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].isdigit():
                files[parts[0]] = int(parts[1])
        tops: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
        scripts: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "bytes": 0})
        for path, size in files.items():
            top = path.split("/", 1)[0] if "/" in path else "(root)"
            tops[top]["files"] += 1
            tops[top]["bytes"] += size
            if top == "code" and path.count("/") >= 2:
                folder = path.split("/")[1]
                scripts[folder]["files"] += 1
                scripts[folder]["bytes"] += size
        data: dict[str, Any] = {
            "total_files": len(files),
            "total_bytes": sum(files.values()),
            "top": dict(sorted(tops.items())),
            "scripts": dict(sorted(scripts.items())),
        }
        if compare_backup:
            cmp = self._compare_backup(files, hashes)
            if not cmp.success:
                return cmp
            data["backup"] = cmp.data
        return ToolResult(True, f"{len(files)} files in dust.", data)

    def _compare_backup(self, files: dict[str, int], hashes: bool) -> ToolResult:
        manifest = self.settings.local_backup / "MANIFEST.sha256"
        try:
            lines = manifest.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            return _fail(f"cannot read {manifest}: {exc}")
        backup: dict[str, tuple[str, int]] = {}
        for line in lines:
            # "<sha256>  <size>\t<date>\tdust/<path>"
            head, _, rest = line.partition("  ")
            fields = rest.split("\t")
            if len(head) == 64 and len(fields) >= 3 and fields[0].isdigit():
                backup[fields[-1].removeprefix("dust/")] = (head, int(fields[0]))
        only_norns = sorted(set(files) - set(backup))
        only_backup = sorted(set(backup) - set(files))
        both = sorted(set(files) & set(backup))
        size_diff = [p for p in both if files[p] != backup[p][1]]
        out: dict[str, Any] = {
            "manifest": str(manifest),
            "in_both": len(both),
            "only_on_norns": only_norns,
            "only_in_backup": only_backup,
            "size_differs": size_diff,
        }
        if hashes:
            same_size = [p for p in both if p not in size_diff]
            remote = self._remote_hashes(same_size)
            out["hash_checked"] = len(remote)
            out["hash_differs"] = sorted(p for p, h in remote.items() if h != backup[p][0])
            out["hash_unreadable"] = sorted(set(same_size) - set(remote))
        return ToolResult(True, "compared.", out)

    def _remote_hashes(self, rel_paths: list[str], batch: int = 100) -> dict[str, str]:
        out: dict[str, str] = {}
        for i in range(0, len(rel_paths), batch):
            chunk = rel_paths[i : i + batch]
            res = self.shell.run(
                ["sha256sum", "--", *[f"{REMOTE_DUST}/{p}" for p in chunk]], timeout=120
            )
            for line in res.stdout.splitlines():
                digest, _, path = line.partition("  ")
                if len(digest) == 64 and path.startswith(REMOTE_DUST + "/"):
                    out[path[len(REMOTE_DUST) + 1 :]] = digest
        return out

    def screenshot(self, out_dir: Path | None = None) -> ToolResult:
        """Export matron's screen buffer to /tmp on the norns (one fixed file, overwritten),
        then copy it to the Mac. Changes nothing the script or the user can see."""
        out_dir = Path(out_dir) if out_dir else self.settings.screenshot_dir
        try:
            self._lua(lua.screenshot(REMOTE_SCREENSHOT))
        except (ConnectionError, lua.LuaError) as exc:
            return _fail(f"screenshot export failed: {exc}")
        self.sleep(0.3)  # the PNG is written on matron's screen thread
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"norns-{time.strftime('%Y%m%d-%H%M%S')}.png"
        scp = [
            "scp",
            "-q",
            "-o",
            "BatchMode=yes",
            f"{self.settings.ssh_alias}:{REMOTE_SCREENSHOT}",
            str(dest),
        ]
        res = self.runner.run(scp, timeout=30)
        if res.returncode != 0:
            return _fail(f"scp failed: {res.stderr.strip()}")
        digest = hashlib.sha256(dest.read_bytes()).hexdigest() if dest.exists() else None
        return ToolResult(True, f"saved {dest}", {"path": str(dest), "sha256": digest})

    # ── writes (gated) ───────────────────────────────────────────────────────

    def load_script(self, script: str, confirm: bool = False) -> ToolResult:
        try:
            folder, file = split_script(script)
        except ValueError as exc:
            return _fail(str(exc))
        if not self._script_exists(folder, file):
            return _fail(f"no script {folder}/{file}.lua in {REMOTE_CODE}; see list_scripts.")
        snippet = lua.load_script(f"code/{folder}/{file}.lua")
        gate = self._gate(f"load {folder}/{file}", confirm, {"lua": snippet.code})
        if gate:
            return gate
        try:
            self._lua(snippet, timeout=10.0)
        except (ConnectionError, lua.LuaError) as exc:
            return _fail(f"load failed: {exc}", live=True, sent=True)
        state = self._wait_for_script(lambda name: name not in ("", "none"), 6.0)
        loaded = bool(state.success and state.data and state.data.get("loaded"))
        return ToolResult(
            loaded,
            f"loaded {folder}/{file}"
            if loaded
            else "load sent; the script did not report as running (check norns_logs).",
            {"live": True, "sent": True, "lua": snippet.code, "now": state.data},
        )

    def start_script(self, script: str | None = None, confirm: bool = False) -> ToolResult:
        """Start = load. With no name, reload the last script norns remembers."""
        if script:
            return self.load_script(script, confirm)
        res = self.shell.run(["cat", f"{REMOTE_DUST}/data/system.state"])
        last = None
        for line in res.stdout.splitlines():
            if line.startswith("norns.state.script = "):
                last = line.split("=", 1)[1].strip().strip("'\"")
        if not last or not last.startswith(f"{REMOTE_CODE}/"):
            return _fail("no script name given and norns has no last script to start.")
        rel = last[len(REMOTE_CODE) + 1 :].removesuffix(".lua")
        folder, _, file = rel.partition("/")
        return self.load_script(folder if file == folder else rel, confirm)

    def stop_script(self, confirm: bool = False) -> ToolResult:
        snippet = lua.stop_script()
        gate = self._gate("stop script", confirm, {"lua": snippet.code})
        if gate:
            return gate
        try:
            self._lua(snippet, timeout=8.0)
        except (ConnectionError, lua.LuaError) as exc:
            return _fail(f"stop failed: {exc}", live=True, sent=True)
        state = self.current_script()
        return ToolResult(True, "script stopped.", {"live": True, "sent": True, "now": state.data})

    def set_param(
        self, param_id: str, value: float | str, via: str = "repl", confirm: bool = False
    ) -> ToolResult:
        try:
            check_param_id(param_id)
            if via not in ("repl", "osc"):
                raise ValueError("via must be 'repl' or 'osc'")
            snippet = lua.param_set(param_id, value)
        except ValueError as exc:
            return _fail(str(exc))
        before = self.get_param(param_id)
        if not before.success:
            return before
        if via == "osc":
            # UNVERIFIED on 231114: lua/core/osc.lua reads /param/<id> but matches it against
            # paramset ids, which looks wrong for 2-part paths. REPL is the default.
            would = {"osc": [f"/param/{param_id}", value], "port": self.settings.osc_port}
        else:
            would = {"lua": snippet.code}
        gate = self._gate(f"set {param_id} = {value!r}", confirm, {**would, "before": before.data})
        if gate:
            return gate
        try:
            if via == "osc":
                packet = self.osc.send(f"/param/{param_id}", value)  # type: ignore[arg-type]
                self.sleep(0.2)
                after = self.get_param(param_id)
                return ToolResult(
                    after.success,
                    f"OSC sent; {param_id} now {after.data and after.data.get('display')}",
                    {
                        "live": True,
                        "sent": True,
                        "osc_hex": packet.hex(" "),
                        "before": before.data,
                        "after": after.data,
                    },
                )
            row = lua.rows(self._lua(snippet))[0]
        except (ConnectionError, lua.LuaError, OSError, ValueError) as exc:
            return _fail(f"set failed: {exc}", live=True)
        if row[0] != "set":
            return _fail(f"no param {param_id!r} in the running script.", live=True, sent=True)
        return ToolResult(
            True,
            f"{param_id}: {before.data['display']} -> {row[2]}",
            {
                "live": True,
                "sent": True,
                "before": before.data,
                "after": {"id": param_id, "value": _number(row[1]), "display": row[2]},
            },
        )

    def key(self, n: int, z: int | None = None, confirm: bool = False) -> ToolResult:
        """OSC /remote/key. ``z`` None = press and release."""
        if isinstance(n, bool) or n not in (1, 2, 3):
            return _fail("key n must be 1, 2 or 3")
        if z is not None and (isinstance(z, bool) or z not in (0, 1)):
            return _fail("key z must be 0 (up) or 1 (down)")
        msgs = (
            [("/remote/key", n, z)]
            if z is not None
            else [("/remote/key", n, 1), ("/remote/key", n, 0)]
        )
        gate = self._gate(f"key {n}", confirm, {"osc": [list(m) for m in msgs]})
        if gate:
            return gate
        sent = []
        for i, (addr, a, b) in enumerate(msgs):
            if i:
                self.sleep(0.08)
            sent.append(self.osc.send(addr, a, b).hex(" "))
        return ToolResult(True, f"key {n} sent.", {"live": True, "sent": True, "osc_hex": sent})

    def enc(self, n: int, delta: int, confirm: bool = False) -> ToolResult:
        if isinstance(n, bool) or n not in (1, 2, 3):
            return _fail("enc n must be 1, 2 or 3")
        if (
            isinstance(delta, bool)
            or not isinstance(delta, int)
            or not -64 <= delta <= 64
            or not delta
        ):
            return _fail("enc delta must be a non-zero integer in -64..64")
        gate = self._gate(f"enc {n} {delta:+d}", confirm, {"osc": ["/remote/enc", n, delta]})
        if gate:
            return gate
        packet = self.osc.send("/remote/enc", n, delta)
        return ToolResult(
            True,
            f"enc {n} {delta:+d} sent.",
            {"live": True, "sent": True, "osc_hex": packet.hex(" ")},
        )

    def deploy(self, local_dir: str, name: str | None = None, confirm: bool = False) -> ToolResult:
        return self.deployer.deploy(Path(local_dir).expanduser(), name, confirm)

    # ── internals ────────────────────────────────────────────────────────────

    def _wait_for_script(self, pred: Callable[[str], bool], timeout_s: float) -> ToolResult:
        last = self.current_script()
        for _ in range(max(1, int(timeout_s / 0.5))):
            if last.success and pred(last.data["name"]):
                break
            self.sleep(0.5)
            last = self.current_script()
        return last

    def ssh_hint(self) -> str:
        return " ".join(ssh_base(self.settings)[:-1])


def _number(raw: str) -> float | int | str:
    try:
        f = float(raw)
    except ValueError:
        return raw
    return int(f) if f.is_integer() and "." not in raw and "e" not in raw.lower() else f
