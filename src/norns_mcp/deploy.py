"""Deploy a script folder from the Mac to ~/dust/code/<name>/ on the norns.

Rules (Thomas, 2026-10-20):
- Dry run by default. A real push needs the server/CLI started with --live AND confirm.
- Never ``--delete`` (or any flag that removes files): files on the norns that are not in
  the local folder stay. ``safety.check_rsync_argv`` refuses every such flag.
- Before a real push, if the target folder exists it is copied with ``cp -a`` to
  ~/norns-mcp-backups/<name>-<UTC timestamp>/ and the copy is checked (same paths, same
  sizes). If the backup fails or does not match, nothing is pushed.
- After the push every local file is checked against ``sha256sum`` on the norns.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from norns_mcp.config import REMOTE_BACKUPS, REMOTE_CODE, Settings
from norns_mcp.core.models import ToolResult
from norns_mcp.safety import UnsafeCommand, check_name, check_rsync_argv
from norns_mcp.transports import Runner, Shell

EXCLUDES = (".git", ".DS_Store", "__pycache__", ".venv")


def _fail(message: str, **data: Any) -> ToolResult:
    return ToolResult(False, message, data or None)


def parse_itemized(stdout: str) -> dict[str, list[str]]:
    """Split ``rsync --itemize-changes`` output into new / changed files and new dirs."""
    out: dict[str, list[str]] = {"new_files": [], "changed_files": [], "new_dirs": [], "other": []}
    for line in stdout.splitlines():
        if len(line) < 13 or line[11] != " ":
            continue
        flags, path = line[:11], line[12:]
        if flags.startswith("*"):
            out["other"].append(line)  # e.g. *deleting: impossible without --delete
        elif flags[1] == "d":
            if "+++++++" in flags:
                out["new_dirs"].append(path)
        elif flags[1] == "f":
            (out["new_files"] if "+++++++" in flags else out["changed_files"]).append(path)
        else:
            out["other"].append(line)
    return out


def local_files(root: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in EXCLUDES for part in rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            files[rel.as_posix()] = path
    return files


class Deployer:
    def __init__(self, settings: Settings, shell: Shell, runner: Runner) -> None:
        self.settings = settings
        self.shell = shell
        self.runner = runner

    def rsync_argv(self, local_dir: Path, name: str, dry_run: bool) -> list[str]:
        # --keep-dirlinks: a symlinked dir on the norns stays a symlink, not replaced by a dir.
        argv = ["rsync", "-rlt", "--keep-dirlinks", "--itemize-changes"]
        if dry_run:
            argv.append("--dry-run")
        argv += [f"--exclude={e}" for e in EXCLUDES]
        argv += [
            "-e",
            "ssh -o BatchMode=yes -o ConnectTimeout=8",
            f"{local_dir}/",
            f"{self.settings.ssh_alias}:{REMOTE_CODE}/{name}/",
        ]
        return check_rsync_argv(argv, f"{REMOTE_CODE}/{name}")

    def _tree(self, remote_dir: str) -> dict[str, int] | None:
        res = self.shell.run(["find", remote_dir, "-type", "f", "-printf", "%P\\t%s\\n"])
        if res.returncode != 0:
            return None
        tree: dict[str, int] = {}
        for line in res.stdout.splitlines():
            path, _, size = line.rpartition("\t")
            if size.isdigit():
                tree[path] = int(size)
        return tree

    def deploy(self, local_dir: Path, name: str | None, confirm: bool) -> ToolResult:
        local_dir = local_dir.resolve()
        if not local_dir.is_dir():
            return _fail(f"not a folder: {local_dir}")
        try:
            name = check_name(name or local_dir.name, "script folder")
        except ValueError as exc:
            return _fail(str(exc))
        files = local_files(local_dir)
        if not files:
            return _fail(f"{local_dir} has no files to deploy.")
        warnings = []
        if f"{name}.lua" not in files:
            warnings.append(f"no {name}.lua at the top: norns will not list it as '{name}'.")
        target = f"{REMOTE_CODE}/{name}"
        try:
            exists = self.shell.run(["test", "-d", target]).returncode == 0
            dry = self.rsync_argv(local_dir, name, dry_run=True)
        except UnsafeCommand as exc:
            return _fail(f"guard refused: {exc}")
        plan = self.runner.run(dry, timeout=120)
        if plan.returncode != 0:
            return _fail(f"rsync dry run failed: {plan.stderr.strip()}", rsync=dry)
        changes = parse_itemized(plan.stdout)
        data: dict[str, Any] = {
            "local": str(local_dir),
            "target": f"{self.settings.ssh_alias}:{target}/",
            "target_exists": exists,
            "plan": changes,
            "rsync_dry_run": dry,
            "warnings": warnings,
            "never_deleted": "files on the norns not in the local folder are kept",
        }
        if not self.settings.live:
            data.update(live=False, sent=False)
            return ToolResult(True, f"DRY RUN: would push {_summary(changes)} to {target}.", data)
        if confirm is not True:
            data.update(live=True, sent=False)
            return ToolResult(False, "deploy needs confirm=true (live write).", data)
        if not (changes["new_files"] or changes["changed_files"] or changes["new_dirs"]):
            data.update(live=True, sent=False)
            return ToolResult(True, f"nothing to push: {target} already matches.", data)

        if exists:
            backup = self._backup(name, target)
            if not backup.success:
                return ToolResult(
                    False,
                    backup.message,
                    {**data, **(backup.data or {}), "live": True, "sent": False},
                )
            data["backup"] = backup.data
        live_argv = self.rsync_argv(local_dir, name, dry_run=False)
        res = self.runner.run(live_argv, timeout=600)
        data.update(live=True, sent=True, rsync=live_argv, pushed=parse_itemized(res.stdout))
        if res.returncode != 0:
            return ToolResult(False, f"rsync failed: {res.stderr.strip()}", data)
        verify = self._verify(files, target)
        data["verify"] = verify
        ok = not verify["mismatch"] and not verify["missing"]
        return ToolResult(
            ok,
            f"deployed {_summary(changes)} to {target}; sha256 "
            + ("matches for every file." if ok else "MISMATCH, see data.verify."),
            data,
        )

    def _backup(self, name: str, target: str) -> ToolResult:
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        dest = f"{REMOTE_BACKUPS}/{name}-{stamp}"
        if self.shell.run(["test", "-e", dest]).returncode == 0:
            return _fail(f"backup path already exists: {dest}")
        for argv in (["mkdir", "-p", REMOTE_BACKUPS], ["cp", "-a", target, dest]):
            res = self.shell.run(argv, timeout=300)
            if res.returncode != 0:
                return _fail(f"backup step {argv[0]} failed: {res.stderr.strip()}; nothing pushed.")
        before, copy = self._tree(target), self._tree(dest)
        if before is None or copy is None or before != copy:
            return _fail(
                "backup copy does not match the target folder; nothing pushed.", backup_path=dest
            )
        return ToolResult(True, "backed up.", {"path": dest, "files": len(copy)})

    def _verify(self, files: dict[str, Path], target: str) -> dict[str, Any]:
        rels = sorted(files)
        remote: dict[str, str] = {}
        for i in range(0, len(rels), 100):
            chunk = rels[i : i + 100]
            res = self.shell.run(
                ["sha256sum", "--", *[f"{target}/{r}" for r in chunk]], timeout=120
            )
            for line in res.stdout.splitlines():
                digest, _, path = line.partition("  ")
                if len(digest) == 64 and path.startswith(target + "/"):
                    remote[path[len(target) + 1 :]] = digest
        mismatch, missing = [], []
        for rel in rels:
            local = hashlib.sha256(files[rel].read_bytes()).hexdigest()
            if rel not in remote:
                missing.append(rel)
            elif remote[rel] != local:
                mismatch.append(rel)
        return {"checked": len(rels), "mismatch": mismatch, "missing": missing}


def _summary(changes: dict[str, list[str]]) -> str:
    return (
        f"{len(changes['new_files'])} new, {len(changes['changed_files'])} changed files, "
        f"{len(changes['new_dirs'])} new dirs"
    )
