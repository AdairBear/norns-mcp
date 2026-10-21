# Live write checks (need Thomas's OK)

Nothing below has been run. Each step changes the norns' state; none deletes anything.
Run from `~/Developer/norns-mcp`. Watch the norns screen. Stop at any step.

| # | Command | Expect | Undo |
|---|---|---|---|
| 1 | `.venv/bin/nornsctl --live load awake --confirm` | awake starts; result `now.name = awake` | step 4 |
| 2 | `.venv/bin/nornsctl params` then `.venv/bin/nornsctl --live set <id> <value> --confirm` | before/after shown; screen/sound follows | set it back |
| 3 | `.venv/bin/nornsctl --live screenshot --confirm` | PNG shows awake's screen (today it is black: no script loaded) | — |
| 4 | `.venv/bin/nornsctl --live stop --confirm` | script stops, engine freed (same as K1+K3 on the home menu) | step 1 |
| 5 | `.venv/bin/nornsctl --live key 2 --confirm`, `.venv/bin/nornsctl --live enc 2 1 --confirm` | OSC /remote/key and /remote/enc act like the hardware | — |
| 6 | `.venv/bin/nornsctl --live set <id> <v> --via osc --confirm` | UNVERIFIED path (/param/<id>); may do nothing | — |
| 7 | Deploy a NEW test folder: `.venv/bin/nornsctl --live deploy <dir> --confirm` | new `~/dust/code/<name>`; sha256 verify passes | folder stays (no deletes); Thomas removes by hand if wanted |
| 8 | Deploy over an EXISTING folder | backup appears in `~/norns-mcp-backups/` first | restore with `cp -a` from the backup |

Note: `norns.script.load` saves the loaded script as the norns' "last script" (system.state).
