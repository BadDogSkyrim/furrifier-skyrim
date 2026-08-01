# Release notes

## v1.6.0 — 2026-08-01

Three silent facegen failures fixed — each one produced NPCs with no
head and reported success — plus a memory fix, and enough build
identity to tell which kit you're running.

### Fixed

- **Headless NPCs on subraces.** NPCs assigned to a furrifier-created
  subrace (Reachmen, Skaal, Sailors, ...) baked facegen nifs with eyes
  and hair but no head, and kept whatever stale FaceTint DDS was lying
  around. The patch was added to the plugin set without dropping the
  cached load-order index, so every FormID the patch pointed at *itself*
  failed to resolve — including the race whose Head Data supplies the
  head. On a real run: 261 of 262 subrace NPCs affected, 0 of 1989
  others. That run now reports 3611 succeeded / 3611 DDSes encoded, up
  from 3595 / 3314.

- **Headless NPCs when previewing after a Run.** A plugin's reference to
  its own record resolved to the last entry in its master list instead.
  Only reachable after a save — saving stamps local FormIDs to a
  concrete index, which stopped the "is this local?" check from
  matching. Live example: an NPC's race came back as
  `ccBGSSSE001ReelLineAct`, a Creation Club fishing record, which
  carries eyes and hair defaults but no Face part. Any write of a
  self-reference into an already-saved patch was corrupting it; the
  preview is simply where it showed.

- **Out-of-memory failures during FaceGen.** The decoded-mask cache grew
  without bound for the life of each worker — 16 MiB per mask at a
  2048px canvas, eight workers at once, ~40 GiB on a 64 GiB machine.
  NPCs whose composite landed while memory was tight were skipped
  outright, no nif and no tint. The cache is now LRU-bounded (256 MiB
  per worker, `FURRIFY_MASK_CACHE_MB` to override) and coverage is
  cached as `uint8` instead of `float32`. Worst case per worker with 200
  unique masks: 3.1 GiB → 0.25 GiB. Output is unchanged within rounding.

- **Crash when `-o` pointed at a new folder.** Logging was set up before
  the session created the output directory, so the first run into a
  fresh folder died on the log file with a raw traceback.

- **Subraces were playable.** They're NPC-only variants, so they no
  longer appear in the chargen race menu.

### New / changed

- **Version and build number**, in the run log's first line, the GUI
  title, and the GUI's bottom bar. The build number is a plain integer
  that increments per build and resets when the version changes, so a
  bug report can name exactly which kit produced it. Running from source
  reports `(dev)`.

- **The run's settings as a pasteable command line.** Replaces the old
  per-setting block. Every toggle states itself (`--armor` as well as
  `--no-armor`, and likewise for schlongs, facegen, preserve-existing,
  throttle), and the plugin selection is included via the new
  `--plugins` flag, so a GUI run can be re-run from a batch file.

- **Diagnostics for a missing head.** If a bake ends up with no Face
  part it now says so, naming the resolved race, what the race defaults
  yielded, and the final head-part set. An unresolved race is a warning
  rather than a debug line.

## v1.2.0 — 2026-04-26

Bug fix on the install path, faster + better-looking face tints, a
sharper preview pane, and a few user-facing polish items.

### Fixed

- **"No module named pyn" on clean kit installs.** The kit didn't
  bundle PyNifly; only Hugh's dev machine had it. Now bundled
  alongside `NiflyDLL.dll` so any unzipped kit just works.

### New / changed

- **In-process BC7 face-tint encoding.** Dropped `tools/texconv.exe`
  from the kit (~1 MB smaller). Face tints encode in-process, no PNG
  round-trip, no subprocess spawn. Slightly higher quality than the
  old texconv pipeline (RMS error 0.21 vs 0.25 against CK reference
  on a real face).

- **Preview pane closer to in-game rendering.** Three improvements:
  the head's diffuse alpha is preserved through the tint composite
  (eye sockets, nostrils, neck seam now carve out correctly); each
  shape uses its own alpha mode from the NIF (hair / scars / hairlines
  blend smoothly instead of getting binary-cut); skin tint is composited
  with Soft Light (was Overlay — too punchy).

- **Session cache shared between Preview and Run.** Loading NPCs in
  the preview pane populates a cache; the Run button reuses it
  instead of reloading every plugin from scratch.

- **Custom schemes.** Drop any `*.toml` into `schemes/` and it
  becomes a valid `--scheme` value (and shows up in the GUI combo).
  Documented in the README "Schemes" section.

- **Per-race TOML files for ungulates and Cellan.** `yas_minorace.toml`,
  `yas_deerrace.toml`, `yas_horserace.toml`, `yas_cellanrace.toml`
  ship alongside `yas_races.toml` so editing one race's headparts
  + probability + labels lives in one place. Drop in your own
  per-race files the same way.

- **Schlongs (SOS) compatibility option** is documented more clearly
  in the README and the `--no-schlongs` flag description; it's a
  furrifier-side toggle, not an SOS-side toggle.

- **README Troubleshooting section.** Covers the most common install
  problems (Mod Organizer 2 launch failures, Windows SmartScreen
  warnings, scheme edits not taking effect).

### Known issues

- *None new.* The v1.1.0 orphan-PNG issue is gone — the in-process
  BC7 path doesn't write PNGs at all.

Polish release on top of 1.0.0: CLI ergonomics cleanup, a user-facing
tint-size control, and a camera-reset button in the preview pane.

### New

- First stable release. Ships both a CLI (`furrify_skyrim.exe`) and a GUI
(`furrify_skyrim_gui.exe`) with a live 3D preview pane. Bakes FaceGen nif +
tint DDS per NPC as part of the run, so Creation Kit's Ctrl-F4 step is no
longer required. Scheme and race-catalog TOMLs ship alongside the exe and
can be extended without code changes.

- **Tint size option:** `--facetint-size {256,512,1024,2048,4096}` — force baked face-tint
DDSes to a specific size. Defaults to the first resolvable
mask's native size (vanilla = 512), matching prior behavior. Mirrored
in the GUI as a "Tint size" dropdown on the options row.

- **Reframe button** on the preview nav row. Resets yaw, pitch, pan, and
zoom to the scene's default framing when you've orbited off the head.

### Known issues

- Aborted runs can leave orphan PNGs in the FaceTint output folder (PNGs
  without a matching DDS). Subsequent runs don't clean them up or
  re-encode them. If you run into it, delete the stale PNGs and re-run.
