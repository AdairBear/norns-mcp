# CONTINUATION

## State (2026-10-20)

- v0.1 built. Local git only (`main`), no remote. Never push or publish without Thomas's OK.
- Old iMac norns-mcp-server read (copy-only) from the Backblaze restore; reused: single-line Lua,
  `<ok>` handling, fresh websocket per call, script-load via `norns.script.load`. Not copied:
  free-form Lua exec, delete/write-file tools, sudo/systemd deploy. mofongo/norns-mcp not used (no licence).
- Verified read-only on the norns (231114, 2026-10-20): status, scripts (22 folders), current
  (none loaded), params (63 rows), get, logs, read, screenshot (PNG export works; black because no
  script runs), inventory vs `~/norns-dust-2026-10-19` MANIFEST: 523/523 SHA-256 match, 0 missing;
  474 extra on the norns are .git internals + 12 dotfiles. Deploy dry run: awake = 0 changes.
- `print` reaches the REPL websocket; `io.write` does not (stdout closed in matron).
- Not verified: every live write. Plan: docs/THOMAS_AT_THE_NORNS.md. OSC /param path unverified.
- matron journal echoes REPL output; `norns_logs` hides norns-mcp's own echo by default (`--raw` keeps it).
- Status also reports maiden's version (HTTP GET /api/v1: v1.1.6). A live load now reads matron's journal after the load line and fails if it shows a script error (`data.load_log`).
- Also running on the norns, untouched: an older `~/norns-mcp-server` and `picoclaw` (:38905).

## Next

1. Thomas OKs live checks (docs/THOMAS_AT_THE_NORNS.md).
2. Register in Claude Code (dry run first).
3. Extract the shared core package with m8-mcp (docs/CORE.md).
