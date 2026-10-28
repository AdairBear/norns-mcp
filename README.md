# norns-mcp

MCP server and CLI (`nornsctl`) for a monome norns on the local network. v0.1.
> **Status: work in progress, building in public.** v0.1 works against a real norns; expect breaking changes.

Shares its device core design with [`m8-mcp`](https://github.com/AdairBear/m8-mcp) (see docs/CORE.md).

## What it does

| | Tools | Path |
|---|---|---|
| Reads (any time) | `norns_status`, `norns_list_scripts`, `norns_current_script`, `norns_read_script`, `norns_params`, `norns_get_param`, `norns_logs`, `norns_dust_inventory` | SSH (`ssh norns`), matron REPL ws :5555 |
| Writes (gated) | `norns_screenshot`, `norns_load_script`, `norns_start_script`, `norns_stop_script`, `norns_set_param`, `norns_key`, `norns_enc` | matron REPL, OSC :10111 |
| Deploy (gated) | `norns_deploy` | rsync over SSH |

## Safety

- Writes are **dry runs** unless the server/CLI is started with `--live` (the only switch; no env var),
  and even then each call needs `confirm=true` (`--confirm` on the CLI). A dry run returns
  `data.would_send` (the exact Lua / OSC / rsync) and sends nothing.
- No sudo, no system or firmware commands, nothing that deletes on the norns: every remote command
  goes through an allowlist (`safety.check_remote_argv`); rsync never gets `--delete` or similar
  (`safety.check_rsync_argv`).
- Deploy: rsync dry run first, always. Live: `cp -a` backup of the existing folder to
  `~/norns-mcp-backups/<name>-<UTC>` on the norns, backup checked, then push, then per-file
  SHA-256 check against the Mac.
- Lua goes to matron as one line with nonce markers (lessons from the iMac norns-mcp-server:
  multi-line sends corrupt matron's REPL buffer).
- The only file written outside deploy is the screenshot temp file `/tmp/norns-mcp-screen.png`;
  screenshot is gated like any write.

## Use

```bash
uv sync
.venv/bin/nornsctl status            # reads
.venv/bin/nornsctl scripts | current | params | get <id> | logs [unit] -n 50 | read awake
.venv/bin/nornsctl inventory --compare-backup --hashes
.venv/bin/nornsctl --live screenshot --confirm   # gated: writes one /tmp file on the norns
.venv/bin/nornsctl load awake        # DRY RUN
.venv/bin/nornsctl --live load awake --confirm    # real (check nobody else is playing first)
.venv/bin/nornsctl deploy ~/path/to/myscript      # DRY RUN plan
```

MCP (stdio), dry run: `.venv/bin/norns-mcp`. Live: `.venv/bin/norns-mcp --live`.
Not registered in any client yet.

## Tests

`uv run pytest` (fakes, no device). `uv run pytest -m device` (read-only, real norns).

## Roadmap

- [ ] Verify every live write on hardware (keys, encoders, OSC `/param` path)
- [ ] Extract the shared device core into a package used by norns-mcp and m8-mcp
- [ ] Safe recovery from the SYSTEM > RESTART / JACK hang over SSH
- [ ] Multi-script coordination from the host (see docs/MULTI-SCRIPT.md)
- [ ] Packaging and a one-line install for other norns owners

## Licence

MIT, see [LICENSE](LICENSE).
