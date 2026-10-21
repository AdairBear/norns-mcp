# Multi-script on norns: what's possible and a recommended design

Research and design only (2026-10-20 night). Nothing here was run live on the norns.

Thomas's ask: *"a script that can handle other scripts tied together would be really sweet - that
might allow the MCP to work a coordination layer with them."*

**Sources.** [D] = read on Thomas's norns (231114) over SSH, read-only, this session.
[W] = public docs. [R] = reported in the community; not checked here.

## The hard limits

| Limit | Evidence |
|---|---|
| **One script at a time.** matron holds one `norns.state.script`. `Script.load` calls `Script.clear()` first, which resets the globals (`setmetatable(_G, ...)`) and `init`/`redraw`/`key`/`enc`. | [D] `lua/core/script.lua` |
| **One SuperCollider engine at a time.** `Crone.setEngine` frees the current engine (`engine.deinit`) before creating the new one. Lua side: `Engine.name` is a single value. | [D] `sc/core/Crone.sc:100-118`, `lua/core/engine.lua` |
| **One owner each for the screen, the 3 keys/encoders and the PARAMS menu.** The running script owns them; mods get their own page under SYSTEM > MODS. | [D] `menu.lua`, [W] mods docs |
| **Compute is small but has room:** Pi 4, 4 cores, 1.8 GB RAM (1.36 GB free while idle), load average ~0.5. In practice SuperCollider is the bottleneck. | [D] `free`, `nproc`, `loadavg` |

So the literal "run two scripts side by side" is **not possible** without forking norns. Everything
below works inside these limits.

## What does work

### 1. Mods: code that runs alongside any script ✅
- A mod is any dust project with `lib/mod.lua`. Enable it in SYSTEM > MODS, then restart. [W][D] `lua/core/mods.lua`
- Hooks (5): `system_post_startup`, `system_pre_shutdown`, `script_pre_init`, `script_post_init`,
  `script_post_cleanup`. They run alphabetically by name; a `~` prefix runs last. [D] `lua/core/hook.lua`, [W]
- From those hooks a mod can add params to **whatever script loads**, add OSC handlers, run clocks,
  and drive MIDI, crow or softcut. Its globals survive script switches.
- Errors in hooks are caught and only shown in the REPL. [W]
- **On this norns:** no mods installed; `system.mods` is absent. [D]
- **Fit:** this is the natural home for a **persistent coordination agent**: a mod that survives
  script changes and exposes one stable OSC/REPL surface for norns-mcp.

### 2. nb (Nota Bene): scripts share voices ✅
- A library a script vendors at `lib/nb`. Script API: `nb:init()`, `nb:add_param(id, name)`,
  `nb:add_player_params()`, `params:lookup_param(id):get_player()`, then
  `player:play_note(note, vel, beats)` / `note_on` / `note_off`. [W] README
- Voices register into a global `note_players` table, which nb aliases as `nb.players`. Any code,
  including a mod, can add players before the script calls `nb:add_param`. [D] `flora/lib/nb/lib/nb.lua:5-11`
- MIDI ports connected when the script starts become voices automatically (`add_midi_players`). [D] same file
- **On this norns:** vendored in `flora` and `arcologies`. No nb voice mods installed. [D]
- Voice mods built on SuperCollider (e.g. emplaitress, doubledecker) are reported to run next to a
  script's own engine, by compiling their own SC classes at boot rather than using the engine
  slot. [R] Their names, licences and behaviour on 231114 still need checking before anything relies on them.
- **Fit:** nb is the right "patch bay" for notes. One sequencer can play an nb voice that is MIDI
  to the M8, crow/Just Friends, or a SuperCollider voice, chosen as a param.

### 3. SuperCollider beyond the one engine ⚠️
- sclang compiles every `.sc` under `~/dust` at boot. [D] `includePaths: /home/we/dust`. 12 engines are installed (flora, timber, mx.synths and others).
- Only the class `Crone.setEngine` picks becomes "the engine" with Lua `engine.*` commands. Other
  classes can still add SynthDefs and listen for OSC on sclang (UDP 57120, which Lua can reach with
  `osc.send({"localhost",57120}, ...)`) [D] `osc.lua:26-34`, `ss` showed sclang on 57120. That
  is how voice mods sidestep the one-engine rule. [R]
- **Combining two existing engines** (say Timber plus MollyThePoly) means writing a new combined
  engine class or a mod-side SC voice. That's real SuperCollider work, and RAM and CPU become the
  limit.
- **Fit:** possible, but only for specific pairs worth the effort. Not a general mechanism.

### 4. One "host" script that includes other scripts' code ⚠️
- A host script can `include`/`require` other projects' `lib/*.lua` (sequencers, scale helpers,
  softcut loopers). This works when the libs are written as libraries.
- Including **whole scripts** fails or turns fragile:
  - they all define `init`, `redraw`, `key`, `enc` and `cleanup`;
  - they all set `engine.name`, and only one engine can load;
  - their param ids collide in one PARAMS menu;
  - they assume they own the screen and the 6 softcut voices;
  - many keep global state.
- Fine for 2-3 Lua or softcut parts built for it (each with its own params prefix, an nb voice
  out, and a UI "page"). Bad as a way to run awake + mlr + timber unchanged.

### 5. crow / ii, MIDI and OSC between devices ✅
- crow: norns drives it over USB (`crow.*`, `crow.ii.*` through `lua/core/crow.lua`). crow speaks ii
  to Just Friends, W/, ansible and others. [D][W] No crow was seen on this norns today (not checked live).
- MIDI: norns is a USB MIDI host (`midi.connect`). **The M8 is a USB MIDI device, so it can plug
  straight into the norns.** [W] The M8 also accepts MIDI clock and transport (see m8-mcp SPEC).
- OSC: matron listens on UDP 10111 [D] (`ss`), and scripts can send and receive OSC to any host.
- Clock: norns' clock source can be internal, MIDI, **Ableton Link**, or crow.
  [D] `lua/core/clock.lua:94-170`
- **Fit:** solid, documented plumbing for tying norns to everything else.

### 6. Coordinate at the Mac (AdairHub) instead ✅ (best value)
- norns-mcp already reads state, sets params, loads and stops scripts, and presses keys. m8-mcp
  already drives the M8's clock, transport, mute/solo, cue rows and Launchpad mode. Both share the
  same core and result shape.
- A small **coordinator** on the Mac can treat "norns running script X" as one voice or part in a
  larger setup with the M8 and the DAW: start/stop together, a shared tempo, scene changes as
  param sets.
- This works **today, with no change on the norns**.

## Recommended design: three layers, built in this order

```
            AdairHub coordinator (Mac)   ← agent / you
           /            |             \
     m8-mcp        norns-mcp        DAW (Cubase via MIDI)
      (M8)       /          \
        ↑    REPL/OSC    "conductor" mod on the norns (layer 2)
        |                   │ nb players · clock · OSC API
        └── MIDI (USB) ─────┘ + whichever script is loaded (layer 3 host, optional)
```

**Layer 1: Mac-side coordination (start here).** Effort: **S, about 1-2 days.**
- New `scenes` tools in a tiny coordinator, or a module of norns-mcp, built on the shared core and
  calling norns-mcp and m8-mcp.
- A scene is a TOML file: norns script plus param values, M8 song row / mutes, tempo. Tools:
  `scene_preview` (dry run) and `scene_apply` (live + confirm, per device).
- Tempo, two options:
  1. Mac sends MIDI clock to the M8 (m8-mcp already does) and norns follows MIDI clock: the M8 or a
     MIDI interface plugged into the norns.
  2. Link: norns `clock.source = link`, with a Link peer on the Mac.
  Decide after one hardware test.
- Same safety model: dry run by default, `--live` plus confirm, nothing deleted.

**Layer 2: "conductor" mod on the norns.** Effort: **M, about 3-5 days**, plus a hardware session.
- A mod (`lib/mod.lua`) that is always loaded, whatever script runs.
- Exposes one stable OSC namespace, for example `/conductor/scene`, `/conductor/note`,
  `/conductor/param`, `/conductor/status`. norns-mcp then talks to one API instead of ad-hoc Lua per
  script.
- Registers nb players (MIDI to the M8, crow/JF, and SC voice mods once they're vetted) so **any
  nb-aware script** (flora and arcologies today) can play into the shared setup.
- Uses `script_post_init` to snapshot and announce the loaded script's params; reports clock state.
- Needs Thomas's OK to install and enable a mod. That is a deploy plus a SYSTEM > MODS toggle and a
  restart: live writes.

**Layer 3: optional host script for tied-together parts.** Effort: **L, 1-2 weeks.**
- One script whose "pages" are small Lua parts (a sequencer, a softcut looper, a generative voice),
  each with namespaced params, output through nb, and one engine chosen for the set (or SC voices
  through mods).
- Only worth building if layers 1-2 leave a gap. Existing scripts are not reused unchanged.

**Not recommended:** forking matron to run several scripts, or chaining two full engines by hand.
Both are high effort, fragile across norns updates, and fight the platform.

## Open questions for Thomas

1. Is the main goal **live performance** (scenes across norns + M8 + DAW) or **composition**
   (several generative parts on the norns itself)? Layer 1 serves the first; layer 3 the second.
2. Which clock leads: the Mac/Cubase, the M8, or the norns? And is Link acceptable?
3. Which crow or ii gear is in use, if any (crow, Just Friends, W/)?
4. OK to try one nb voice mod (and later the conductor mod) on the norns? Each is a live write.

## Next steps (no live writes until Thomas OKs)

- After L1 passes: register norns-mcp in Claude Code (already decided).
- Draft the layer 1 scene format and a `scene_preview` dry run with fakes. No hardware needed.
- Shortlist nb voice mods with licences, from their repos. Read-only research.
