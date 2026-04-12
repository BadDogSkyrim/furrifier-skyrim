# Furrifier

The furrifier is a command-line executable that furrifiers every NPC in your active load
order. Vanilla races are given a furry appearance; headpart and armor races are reassigned
so furrified races get furry variants of armor and headparts. 

It operates on your entire active load order--change what's included by changing what mods are active.

Usage:

```
furrify_skyrim.exe [--help] [--patch PATCH] 
  [--scheme {all_races,cats_dogs,legacy,user}] [--no-armor] [--no-male]
  [--no-female] [--no-schlongs] [--data-dir DATA_DIR] [--debug] 
  [--log-file LOG_FILE]
```

Options:

| Flag | Description | Default |
|------|-------------|---------|
| `--scheme NAME` | Race assignment scheme | `all_races` |
| `--patch FILE` | Output patch filename | Game mods folder, `YASNPCPatch.esp` |
| `--data-dir PATH` | Skyrim mod folder | auto-detected |
| `--no-armor` | Skip armor furrification | |
| `--no-male` | Skip male NPCs | |
| `--no-female` | Skip female NPCs | |
| `--no-schlongs` | Disable SOS compatibility | Ignore if SOS is not loaded |
| `--debug` | Enable debug logging | |
| `--log-file FILE` | Write log to file | |

Once the furrifier has run, load the patch in Creation Kit and run facegen on all NPCs:

* Get Creation Kit Platform Extended if you don't have it alredy. (Not required but makes 
everything more convenient.) 
* Load up the new plugin.
* Select "Actors" in the left pane of the object window.
* Click "Show only active forms" in the upper left, above the pane. (If you don't have CKPE
it won't  be there. Skip this step.)
* Select everything in the right pane
* Enter Ctrl-F4. Click okay.

The CK will run for quite a while, generating every unique NPC. It will create nif files in 
`meshes\actors\character\FaceGenData\FaceGeom` and texture files in `textures\actors\character\FaceGenData\FaceTint`. If you use multiple profiles, collect
this output and put it and the patch into a mod unique to this profile.

You can also run one of the mods that generates faces on the fly, but performance will be worse. 

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
scheme is for you to edit according to your preferences. No Python knowledge needed just
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

Furrifier ships with four schemes:

| Scheme       | Description                                                                                    |
| ------------ | ---------------------------------------------------------------------------------------------- |
| `all_races`  | Default. Maps every vanilla humanoid race to a Yiffy Age (YAS) furry race. Includes Sailors.   |
| `cats_dogs`  | Same as `all_races` minus the Sailor subrace.                                                  |
| `legacy`     | Original BDFurrySkyrim mappings (Imperial → Vaalsark instead of Kettu, Breton → Kettu, etc.).  |
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
```

### Where to put your own preferences

The `--scheme` flag accepts exactly four values: `all_races`, `cats_dogs`, `legacy`, and
`user`. **`schemes/user.toml` is the one you're meant to edit.** It ships as a
Reachman-and-Skaal-only subset of `all_races` — simple enough to read in one sitting and
then reshape to your taste. Edit it and run `furrify_skyrim --scheme user`.

The other three (`all_races`, `cats_dogs`, `legacy`) are furrifier's shipped defaults. You
can edit them directly, but your edits will be overwritten the next time you update
furrifier. (`user.toml` may get overwritten too, but not with anything important. Just
make sure you save a copy of your changes.)

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
| `yas_races.toml`   | The catalog for the Yiffy Age of Skyrim races. 231 headpart equivalents, 126 hair labels.     |
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
fallback paths for its headparts. It will first look for similar headparts by label (or
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
