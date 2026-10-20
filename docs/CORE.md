# Shared device core (with m8-mcp)

Approved architecture (Thomas, 2026-10-20): norns-mcp is a separate MCP that shares a device core
with m8-mcp. Both are AdairHub nodes.

Until the core is its own package, `src/norns_mcp/core/` holds copies:

| norns-mcp | m8-mcp | rule |
|---|---|---|
| `core/serialize.py` | `src/m8_mcp/serialize.py` | byte-identical |
| `core/signals.py` | `src/m8_mcp/_signals.py` | byte-identical |
| `core/models.py` `ToolResult` | `src/m8_mcp/models.py` `ToolResult` | same fields |

`tests/test_frontends.py::test_shared_core_matches_m8_mcp` fails if the copies drift (skipped when
no m8-mcp checkout). Shared conventions: `{success, message, data}` results, never raise to the
client, dry run unless `--live`, Protocol-based transports tested with fakes.

Next step (not v0.1): extract `adairhub-device-core` as a local package both repos depend on.
m8-mcp was not modified.
