"""Settings: where the norns is, the live gate, local folders.

Safety defaults:
- ``live`` is False unless ``--live`` or ``NORNS_MCP_LIVE=1`` is given. Without it every
  write action (load, stop, set param, key/enc, deploy) is a dry run that reports what it
  would send and sends nothing. With it, each write call still needs ``confirm=True``.
- SSH goes through the ``Host norns`` entry in ``~/.ssh/config`` (key in the keychain),
  in BatchMode: it never prompts for a password.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

LIVE_ENV = "NORNS_MCP_LIVE"
HOST_ENV = "NORNS_MCP_HOST"
SSH_ALIAS_ENV = "NORNS_MCP_SSH"
STATE_DIR_ENV = "NORNS_MCP_STATE_DIR"
BACKUP_DIR_ENV = "NORNS_MCP_LOCAL_BACKUP"

DEFAULT_HOST = "norns.local"  # <lan-ip> on the home network (2026-10-20)
DEFAULT_SSH_ALIAS = "norns"
WS_PORT = 5555  # matron Lua REPL (ws-wrapper, nanomsg bus subprotocol)
OSC_PORT = 10111  # matron OSC

REMOTE_HOME = "/home/we"
REMOTE_DUST = f"{REMOTE_HOME}/dust"
REMOTE_CODE = f"{REMOTE_DUST}/code"
# Deploy backups live OUTSIDE dust so maiden and the SELECT menu never list them.
REMOTE_BACKUPS = f"{REMOTE_HOME}/norns-mcp-backups"
# The only file this server ever writes outside a deploy: the screenshot, overwritten each time.
REMOTE_SCREENSHOT = "/tmp/norns-mcp-screen.png"

LOG_UNITS = ("matron", "crone", "sclang", "maiden", "jack", "watcher")


def _truthy(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def default_state_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "norns-mcp"


def default_local_backup() -> Path:
    return Path.home() / "norns-dust-2026-10-19"


@dataclass(frozen=True)
class Settings:
    live: bool = False
    host: str = DEFAULT_HOST
    ssh_alias: str = DEFAULT_SSH_ALIAS
    ws_port: int = WS_PORT
    osc_port: int = OSC_PORT
    state_dir: Path = field(default_factory=default_state_dir)
    # A copy of dust on the Mac with MANIFEST.sha256, for the inventory comparison.
    local_backup: Path = field(default_factory=default_local_backup)
    repl_timeout_s: float = 5.0
    ssh_timeout_s: float = 30.0

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.ws_port}/"

    @property
    def screenshot_dir(self) -> Path:
        return self.state_dir / "screenshots"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, **overrides: object) -> Settings:
        """Build settings from the environment. ``overrides`` win (e.g. live=True from --live)."""
        env = dict(os.environ) if env is None else env
        settings = cls(
            live=_truthy(env.get(LIVE_ENV)),
            host=env.get(HOST_ENV) or DEFAULT_HOST,
            ssh_alias=env.get(SSH_ALIAS_ENV) or DEFAULT_SSH_ALIAS,
            state_dir=(
                Path(env[STATE_DIR_ENV]).expanduser()
                if env.get(STATE_DIR_ENV)
                else default_state_dir()
            ),
            local_backup=(
                Path(env[BACKUP_DIR_ENV]).expanduser()
                if env.get(BACKUP_DIR_ENV)
                else default_local_backup()
            ),
        )
        return replace(settings, **{k: v for k, v in overrides.items() if v is not None})
