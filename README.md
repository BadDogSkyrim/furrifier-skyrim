# Furrifier

The furrifier furrifies every NPC in your active load order. Vanilla races are given a
furry appearance; headpart and armor races are reassigned so furrified races get furry
variants of armor and headparts; schlongs (if present) are reassigned to apply or not apply appropriately; and optionally NPCs of new races are added to leveled lists.

It operates on your entire active load order by default — change what's included by changing what mods are active, or by selecting them explictly at run time.

The kit ships two executables:

- **`furrify_skyrim_gui.exe`** — a GUI with a live 3D preview pane. Double-click to launch;
  pick options in the form and hit Run.
- **`furrify_skyrim.exe`** — the CLI. Same code, same options, useful for scripted runs.

## Command-line usage

```
furrify_skyrim.exe [--help] [--patch PATCH]
  [--scheme {all_races,cats_dogs,legacy,user}]
  [--no-armor] [--no-schlongs] [--no-facegen]
  [--data-dir DATA_DIR] [-o DIR]
  [--facetint-size {256,512,1024,2048,4096}]
  [--limit N] [--debug] [--log FILE] [--profile PATH]
```

Options:

| Flag | Description | Default |
|------|-------------|---------|
| `--scheme NAME` | Race assignment scheme (see below) | `all_races` |
| `--patch FILE` | Output patch filename | `YASNPCPatch.esp` |
| `--data-dir PATH` | Skyrim Data dir for READING source assets | auto-detected |
| `-o`, `--output DIR` | Directory to WRITE patch + FaceGenData | same as `--data-dir` |
| `--no-armor` | Skip armor furrification | |
| `--no-schlongs` | Don't alter SOS (schlong) compatibility | ignored if SOS not loaded |
| `--no-facegen` | Skip building per-NPC FaceGen nif + DDS | |
| `--facetint-size N` | Baked face-tint size. One of 256, 512, 1024, 2048, 4096 | match first mask's native size (vanilla = 512) |
| `--limit N` | Cap FaceGen to the first N NPCs — useful for previewing | no cap |
| `--debug` | Enable debug logging | |
| `--log FILE` | Write log to file | |
| `--profile PATH` | Run under cProfile and dump stats to PATH (inspect with `snakeviz` or `pstats`) | |

### FaceGen output

By default, furrifier bakes each NPC's FaceGen nif + DDS itself, writing into the output
directory under:

```
meshes\actors\character\FaceGenData\FaceGeom\<plugin>\<formid>.nif
textures\actors\character\FaceGenData\FaceTint\<plugin>\<formid>.dds
```

That means you can launch the game directly after a run — no Creation Kit step required.

If you pass `--no-facegen` or want to re-bake in the CK anyway, the old workflow still
works: load the patch in Creation Kit (CKPE recommended), select all actors, press
Ctrl-F4 to bake. Any mod that generates faces on the fly will also work.

## GUI usage

`furrify_skyrim_gui.exe` exposes every CLI option as a form field plus a live preview pane
on the right: pick an NPC, see how they'd look under the current scheme. Changing the
scheme or plugin set refreshes the preview automatically. Buttons on the preview side:

- **Load NPCs** — build the session and populate the picker.
- **◀ / ▶** — browse the NPCs you've previewed.
- **Reframe** — reset the camera to its default framing if you've orbited off the head.

# Customizing the furrification

Furrifier is configured via two folders of TOML files next to the executable:

```
furrify_skyrim/
├── furrify_skyrim.exe
├── schemes/
│   ├── all_races.toml
│   ├── cats_dogs.toml
│   ├── legacy.toml
│   └── user.toml
└── races/
    ├── yas_races.toml
    └── user_races.toml
```

These schemes provide a variety of ways to assign vanilla to furry races. The "user"
scheme is for you to edit according to your preferences. No Python knowledge needed - just
edit the text files.

## Two kinds of customization

- **Schemes** (`schemes/*.toml`) say *which* furry race a given vanilla race, faction, or
  NPC becomes. You pick a scheme at runtime with `--scheme NAME`.
- **Race catalogs** (`races/*.toml`) describe the furry races - primarily headparts at
  this point - defining vanilla headparts they're equivalent to and what labels apply to
  them for label-based matching. Every file in `races/` is loaded at startup, regardless
  of which scheme you picked.

The split means if you're just using existing race mods you can define schemes without
touching race data. If you're providing a race you don't need to specify how it is used.

## Schemes

`--scheme NAME` matches against any `.toml` file in the `schemes/`
folder next to the exe — drop a new file in there and it's
selectable next launch (CLI tab-completion + the GUI's scheme
combo both pick it up automatically). Furrifier ships with four:

| Scheme       | Description                                                                                    |
| ------------ | ---------------------------------------------------------------------------------------------- |
| `all_races`  | Default. Maps every vanilla humanoid race to a Yiffy Age (YAS) furry race. Includes Cellans (otters) and ungulates. Some NPCs are furrified outside Skyrim's races, e.g. Skaal are jackals, Falkreath is all deer, and so forth.   |
| `cats_dogs`  | Cats and dogs only, like it says on the box. Canids are human, felines mer. |
| `legacy`     | Original BDFurrySkyrim mappings (Imperial → Vaalsark, Breton → Kettu, etc.).  |
| `user`       | A minimal starting point for your own customizations. Edit, run. Save a copy so it doesn't get overwritten when the furrifier updates. |

### Scheme file sections

A scheme file has up to four sections. Follow this format exactly when editing.

```toml
# 1. One vanilla race → one furry race.
races = [
  {vanilla = "NordRace",        furry = "YASLykaiosRace"},
  {vanilla = "NordRaceVampire", furry = "YASLykaiosRaceVampire"},
  # ...
]

# 2. Subraces — derived races for a subset of a vanilla race (a faction,
#    specific NPCs, etc.). `basis` is the vanilla race they derive from;
#    the subrace only kicks in when an NPC matches that vanilla race AND
#    whatever faction/NPC rule promotes them to the subrace.
subraces = [
  {id = "YASReachmanRace", name = "Reachmen", basis = "BretonRace", furry = "YASKonoiRace"},
  # ...
]

# 3. Faction-wide race overrides. Every NPC in the faction becomes the
#    listed race (or subrace) regardless of their vanilla race tag.
[faction_races]
ForswornFaction                = "YASReachmanRace"
DLC2SkaalVillageCitizenFaction = "YASSkaalRace"

# 4. Per-NPC overrides (by NPC EditorID). These beat everything above.
#    Useful for NPCs whose vanilla race tag is wrong for the character
#    (e.g. Forsworn who are tagged Breton but should be Reachmen).
[npc_races]
Ainethach = "YASReachmanRace"
Gralnach  = "YASReachmanRaceChild"

# 5. Leveled-NPC list extension (optional). For furry races that don't
#    have a direct vanilla pairing in the `races =` table above, this
#    section adds occasional duplicates of vanilla NPCs into existing
#    LVLN leveled lists, so they show up at low rates in random
#    encounters. Omit the whole `[leveled_npcs]` section to skip this
#    pass entirely.
[leveled_npcs]
exclude_substrings = ["LCharOrc", "Thalmor"]  # LVLNs whose EditorID contains any of these are skipped

# A group fires on LVLNs whose EditorID matches any of its substrings
# (case-insensitive). First-match-wins across groups; a group with no
# match_substrings is the catch-all.
[[leveled_npcs.groups]]
match_substrings = ["bandit"]
races = [
  {race = "BDDeerRace"     , probability = 0.01},  # 1% per LVLO entry
  {race = "BDHorseRace"    , probability = 0.10},
  {race = "YASVaalsarkRace", probability = 0.10},
]

# Catch-all (no match_substrings): applies to any LVLN that didn't match
# any earlier group.
[[leveled_npcs.groups]]
races = [
  {race = "BDDeerRace" , probability = 0.05},
]
```

### How leveled-NPC extension works

For each entry in a matched LVLN, furrifier rolls one decision per
listed `{race, probability}` pair. On a hit, it duplicates the source
NPC, reassigns the duplicate to the target furry race, runs full
furrification on it, and appends a new leveled-list entry pointing at
the duplicate (preserving the source entry's level and count). The
same `(source NPC, target race)` pair only generates one shared
duplicate even if it hits in multiple lists.

Rolls are **deterministic**: the same scheme + load order produces the
same set of duplicates every run. Tweaking probabilities will reshuffle
who gets added.

Useful conventions:
- **List races that don't already have a direct vanilla pairing.** If
  `OrcRace → BDMinoRace` is in the top-of-file `races = [...]` table,
  every Orc bandit is already a Mino bandit; listing `BDMinoRace` here
  too just stacks duplicates of Nord bandits *also* turning into Minos.
  Use this section for races that only show up via subraces or NPC
  overrides (e.g. Cellan, Vaalsark, Deer, Horse, Bagha).
- **`match_substrings` is case-insensitive substring matching** on the
  LVLN's EditorID. Skyrim names them `LCharBanditMelee`,
  `LCharNecromancer`, etc., so `["bandit"]` is enough to catch the
  whole bandit family.
- **`exclude_substrings`** at the section root applies before group
  matching — a useful escape hatch for LVLNs whose names happen to
  collide with one of your group rules but shouldn't be touched
  (vanilla example: skipping Thalmor lists so they stay all-Altmer).
- Omit the `[leveled_npcs]` section entirely to skip the whole pass.
  `cats_dogs.toml` and `user.toml` ship without it; `all_races.toml`
  uses it for ungulate / sailor / Skaal diversity.

### Where to put your own preferences

Easiest path: edit **`schemes/user.toml`**. It ships as a
Reachman-and-Skaal-only subset of `all_races` — simple enough to read in one sitting and
then reshape to your taste. Save your edits, run `furrify_skyrim --scheme user`.

Want to keep multiple of your own variants? Drop them in as new files —
`schemes/my_minoraids.toml`, `schemes/my_canines_only.toml`, whatever — and select with
`--scheme my_minoraids` etc. The folder is scanned at startup so any `.toml` in there
becomes a valid `--scheme` value. The GUI's scheme combo populates from the same scan.

The shipped four (`all_races`, `cats_dogs`, `legacy`, `user`) are furrifier's defaults. You
can edit them directly, but your edits will be overwritten the next time you update
furrifier. (`user.toml` may get overwritten too, but not with anything important. Save
copies of files you don't want to lose.)

## Races: furry headpart catalogs

The race files provide race definitions. If you want to include new races and customize
how they are used, this is where you go. Right now the only real customization is
headparts. In the future we plan to give you more control over tint layers and morphs.

Headpart defition is the bridge between vanilla headpart EditorIDs and the furry headparts
a given mod provides. It's how furrifier knows that `MaleEyesHumanAmber` should become
`YASDayPredMaleEyesAmber` when a Nord becomes a Lykaios. Without such specifications the
furrifier would just choose a random eye color.

(Except blind and half-blind eyes. The furrifier attempts to recognize those and only
assign them when the original was blind or half-blind.)

Race catalogs are **not scheme-specific**. Every file in `races/` is merged into the
context at load time, regardless of which scheme you pick. Furrifier ships with:

| File               | Description                                                                                   |
| ------------------ | --------------------------------------------------------------------------------------------- |
| `yas_races.toml`   | The catalog for the Yiffy Age of Skyrim races. Cats, dogs, otters, ungulates.     |
| `user_races.toml`  | An empty template for your own additions.                                                     |

### Race catalog file sections

```toml
# Vanilla headpart → furry equivalent. The same vanilla id may appear
# multiple times (once per target furry race) — this is how the loader
# discovers all possible furry equivalents for a given vanilla headpart.
headpart_equivalents = [
  {vanilla = "MaleEyesHumanAmber",               furry = "YASDayPredMaleEyesAmber"},
  {vanilla = "MarksFemaleHumanoid10RightGashR",  furry = "YASLykaiosFemScarC01"},
  {vanilla = "MarksFemaleHumanoid10RightGashR",  furry = "YASKettuFemScarC01"},
  # ...
]

# Labels on furry headparts, for label-based fallback matching when no
# direct headpart_equivalent is defined. Comma-separated strings; the
# loader splits and trims whitespace.
[headpart_labels]
YASDogMaleHairDreads001 = "DREADS,BOLD,FUNKY,LONG"
YASCatMaleHairDreads001 = "DREADS,BOLD,FUNKY,LONG"
```

### Adding your own race catalog

Drop `my_races.toml` (or any other name) into `races/`. It gets merged alongside
`yas_races.toml` automatically — no code changes, no config to update. Your file can add
new entries OR duplicate existing ones (duplicates are additive for
`headpart_equivalents`).

To support a completely different furry race mod:

1. Edit the user scheme file (in `schemes/`) mapping vanilla races to *your* mod's race
   EditorIDs.
2. Create a race catalog file (in `races/`) with your mod's headpart equivalents and
   labels.

Both files are merged with whatever else is in those folders, so you don't have to delete
or edit the shipped files. Just drop in new ones.

### Graceful fallback

If a race referenced by your scheme has no catalog data, furrifier uses ESP-defined
paths for its headparts. It will first look for similar headparts by label (or
blind/non-blind for eyes); failing that it chooses one randomly.

## What's NOT in these files

Some data is kept as Python code in `src/furrifier/vanilla_setup.py` rather
than TOML:

- **Vanilla hair labels** (`HairMaleNord01` → `SHORT,NEAT,MILITARY`) —
  static facts about the base game.
- **Vanilla NPC aliases** (`CiceroDawnstar` → `Cicero`) — same reason.
- **Vanilla race corrections** — a short list of NPCs whose vanilla race
  tag is wrong for the character (`SeptimusSignus` is tagged Nord but
  should be Imperial, etc.). Scheme-independent; always applied.

These aren't externalized because nobody has a reason to override them —
they describe the base game, not furrifier's configuration. If you run
into a case where you *do* need to override them, open an issue; that's a
good signal the data belongs in a catalog file instead.

## TOML format notes

- **Top-level arrays before `[table]` headers.** If you write
  `headpart_equivalents = [...]` *after* `[npc_races]`, TOML's scoping rule
  treats it as a key *inside* `[npc_races]`, not at the top level. The
  furrifier test suite has a regression guard for this, but it's worth
  knowing if you're hand-assembling a file from scratch.
- **Inline tables** (`{a = 1, b = 2}`) must fit on a single line — that's
  a TOML spec constraint, not a furrifier one. The shipped files use
  aligned columns for readability; alignment is optional.
- **Comments** are just `# ...`. Sections in the shipped files use
  `# === Section name ===` for visual grouping; the parser ignores them.
- **Duplicate keys** are an error in a table (`[faction_races]` can't list
  the same faction twice) but are allowed in array-of-inline-tables
  (`headpart_equivalents` relies on this).

## Loader behavior, in one paragraph

When furrifier starts, it parses `schemes/<name>.toml` (the one named by
`--scheme`) and registers its races, subraces, faction overrides, and NPC
overrides. Then it walks every `*.toml` file in `races/` in filesystem
order, merging each file's `headpart_equivalents` and `headpart_labels`
into the same context. Missing `races/` directory is non-fatal — the tool
falls back to ESP-only headpart matching. Duplicate `headpart_equivalents`
entries across files are additive; duplicate `headpart_labels` keys across
files take the last value read.
