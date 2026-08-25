# Release notes

## v1.8.0 — 2026-08-25

The furrifier now works under Mod Organizer 2. It didn't before — not
partially, not slowly, it produced an empty patch and told you it had
succeeded. If you use MO2, including any Wabbajack modlist, this is the
release that makes the tool usable at all.

Vortex users are unaffected by the bug and unaffected by the fix.

### Fixed

- **Mod Organizer 2 support.** MO2 shows programs a merged view of
  vanilla plus your mods, but that view is only visible to some of the
  ways Windows can ask about a file. The furrifier was using one of the
  ways it *isn't* visible to, so every plugin, mesh, texture and archive
  that came from a mod rather than from the base game read as "missing"
  even though it was right there and perfectly readable.

  On a 366-plugin modlist that meant 10 plugins loaded, 0 races
  furrified, and an empty patch written without a single error. The same
  blind spot then broke asset lookup (every BSA "not found", no headpart
  meshes resolved), directory creation during FaceGen, and even the
  plugin picker's own "is this a real folder?" check.

  A full Wildlander run now completes: 362 of 366 plugins, 32,264 NPCs
  furrified, 4,685 FaceGen bakes, zero failures.

- **FaceGen no longer discards work it has already done.** Under MO2 a
  diagnostic line that measured each finished mesh was failing, and
  taking the finished mesh down with it — a run reported "0 succeeded,
  4685 failed" when in fact all 4,685 had been built correctly.

- **A single broken mod no longer stops the whole run.** One
  malformed script record — `OBodyNGWeight.esp` is the known case —
  used to abort furrification for the entire load order. Now it's
  skipped with a warning and everything else proceeds.

- **The plugin list shows what's actually loadable.** "Edit plugins"
  used to list everything named in plugins.txt whether the file could be
  read or not, so plugins you'd ticked came back "missing" mid-run. It
  now lists what's really there, and says plainly when active plugins
  are absent from the data folder you chose.

- **Failures are visible.** A run that can't load your plugins now says
  so, names the directory it looked in, and lists what it couldn't find.
  Previously that information was only in the debug log, and the run
  looked like a success.

### New

- **`--data-dir` on the GUI**, so a Mod Organizer executable entry can
  launch the furrifier already pointed at the right Data folder. For a
  Wabbajack list that's `<modlist>\Stock Game\Data` (some lists call it
  `Game Root`) — *not* your Steam install, which MO2 doesn't touch and
  which auto-detection would otherwise find.

- **Changing the data directory reloads the plugin list** and reports
  how much of your load order is present there, immediately rather than
  after a long load.

### Notes for MO2 users

Point "Data dir" at the folder MO2 manages, not at Steam. If you're not
sure which that is, `gamePath=` in your instance's `ModOrganizer.ini`
names it; add `\Data`.

Output can be left to accumulate in MO2's Overwrite and turned into a
mod afterwards, which is the normal workflow and is what these fixes
were tested against.

FaceGen writes a face tint per NPC at whatever resolution the source
artwork uses — on a large modlist that can be many gigabytes. "Tint
size" in the GUI (`--facetint-size`) lowers it if you'd rather trade
some detail for disk space and build time.

## v1.7.0 — 2026-08-18

`--armor` and `--schlongs` now partition the armor space instead of
overlapping on it, so a run can ask for one half without dragging in the
other.

### Changed

- **`--armor` and `--schlongs` split on biped slot 52.** `--armor` alone
  furrifies every furrifiable slot *except* 52; `--schlongs` alone
  furrifies slot 52 and nothing else; both together behave exactly as
  before. The armor pass now runs whenever either flag is set — it used
  to be gated on `--armor` alone, so `--no-armor --schlongs` silently did
  no armor work at all and hoodie sheaths kept their vanilla race lists.

  Both halves are needed by the YAS Reborn package split. The SFW patch
  must carry no sheath records — it ships without the schlong plugins, so
  a sheath override in it is a missing master waiting to happen — and
  previously got that only by leaving the SOS plugins out of `--plugins`,
  which is a load-order trick rather than a stated intent. The NSFW patch
  wanted the opposite half and was shipping 68 generic armor ARMAs that
  duplicated the SFW patch's own work.

- **The ARMO merge is scoped by the same mask.** An ARMO whose
  furrifiable addons all sit outside the active mask is no longer copied
  into the patch, since `furrify_all_armor` would ignore it anyway. ARMOs
  carrying no furrifiable addon at all are still merged — that part is
  load-order conflict resolution, unrelated to furrification, and
  dropping it would resurrect armor conflicts.

- **The briarheart fixup follows `--armor`.** It is body armor, nowhere
  near slot 52, so a schlongs-only run no longer fires it.

- The run log now states the resolved bodypart mask and the flag pair
  that produced it.

### Notes

Nothing changes for a run that passes both flags, which is every
historical invocation of the shipping build scripts. `FURRIFIABLE_BODYPARTS`
is still `0x401803`, and `merge_armor_overrides`/`furrify_all_armor`
default to it, so existing callers are unaffected.

## v1.6.1 — 2026-08-04

Four fixes to additive runs — a second pass layered over an earlier
furrifier patch (`--preserve-existing`) was minting duplicates of
records the first patch already owned, which split race identity and
quietly broke SOS. Plus the reason SOS never gave subrace NPCs a
schlong on its own.

### Fixed

- **Duplicate subrace RACE records.** An additive pass created its own
  copy of every subrace (Sailor, Reachman, Skaal, ...) instead of
  reusing the ones already in the load order. Two RACE records ended up
  sharing an EditorID while holding different FormIDs, so everything the
  second patch wrote — SOS compatible-race lists, sheath ARMA race lists
  — pointed at the duplicate while the NPCs still pointed at the
  original. SOS then read the schlong as invalid for the actor's race.
  In xEdit both sides read `YASSailorRace`, which is what made it hard
  to see. Real run: 12 duplicate races, gone.

- **No schlongs auto-assigned to subrace NPCs.** SOS spreads itself with
  a cloak whose magic effect gates on "playable race, or one of six
  named non-playable ones." Subraces are deliberately non-playable so
  they stay out of the chargen menu, so the cloak never fired on them,
  they never received `SOS_ActorSpell`, and SOS never considered them.
  The symptom was specific: the MCM listed the race as compatible and
  enabled at 100%, manual assignment worked, automatic distribution
  silently never happened. Subraces are now added to that condition
  list the same way SOS handles its own non-playable races. Affected
  every subrace, not just the one it was noticed on — 185 Forsworn
  included.

- **Leveled lists and race presets duplicated on additive runs.** Both
  passes mint brand-new NPC records, and re-running them over a patch
  that already has them stacked a second set. A real NSFW pass was
  creating ~900 leveled-list NPCs on top of the ~906 the first patch
  contributed, none with FaceGen. Both passes are now skipped under
  `--preserve-existing`. They still run under `--no-facegen`: baking
  heads in the Creation Kit instead is a legitimate workflow.

- **Bad FormID in headpart FormLists.** The headpart pass read a
  subrace's FormID raw, which is only correct for a record the patch
  minted itself; an adopted one is in its own plugin's master space and
  wrote a reference to an unrelated record. The same pass could also
  append a race the list already contained, when the existing entry sat
  later in the list than the furry race that triggers the add.

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
