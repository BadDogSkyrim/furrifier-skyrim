"""Report file references in the active load order that resolve to
neither a loose file nor any BSA entry, grouped by the plugin that
made the reference.

Run from the furrifier project root with the package installed:

    python scripts/find_missing_assets.py
    python scripts/find_missing_assets.py --data-dir "C:/Skyrim SE/Data"
    python scripts/find_missing_assets.py --plugin MyMod.esp
    python scripts/find_missing_assets.py --verbose

Output is grouped by plugin; within each plugin, missing paths are
sorted. A count line per plugin shows the total. Suppress the
per-file list with ``--summary`` if you just want totals.

Notes on the extraction:

- Strings are sniffed from raw subrecord bytes (null-terminated,
  ending in a known Skyrim file extension). This catches the
  common path-carrying subrecords (MODL, ICON, TX00–TX07, FNAM,
  VMAD-embedded script paths) without needing a per-record schema.
- Some path subrecords omit the data-root prefix
  (``textures/``, ``meshes/``, ``sound/``, ``scripts/``). The
  script tries both the raw path and the extension-implied prefix;
  if either resolves, the reference is fine.
- False positives: the odd byte sequence that happens to end in a
  known extension will show up. They're rare in practice; if they
  clutter your output, ``--ignore-regex`` filters them.

Place: ``furrifier/scripts/find_missing_assets.py``. Not part of
the shipped kit (outside the PyInstaller spec's copy list), so it
stays a dev-side tool.
"""
from __future__ import annotations

import argparse
import fnmatch
import logging
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from esplib import LoadOrder, PluginSet, find_game_data

from furrifier.facegen.assets import AssetResolver


log = logging.getLogger("find_missing_assets")


# Known Skyrim asset file extensions we care about. Lowercase.
_EXTENSIONS = [
    "nif", "tri", "hkx", "egm",              # meshes / anim
    "bgem", "bgsm",                          # material files
    "dds",                                   # textures
    "wav", "xwm", "fuz",                     # audio
    "lip",                                   # lip sync
    "pex",                                   # papyrus compiled
    "seq",                                   # sequence
    "swf",                                   # interface
]

# Path-character class: alphanumerics, path separators, common
# filename punctuation. Intentionally conservative — we don't want
# stray binary bytes sliding into the capture.
_PATH_RE = re.compile(
    (r"([A-Za-z0-9 _\-\\/.()]+?\."
     r"(?:" + "|".join(_EXTENSIONS) + r"))\b").encode("ascii"),
    re.IGNORECASE,
)

# Bethesda's root prefixes by file extension. Some subrecords store
# paths already rooted (e.g. "textures\actors\...") while others
# store just the relative portion ("actors\...") expecting the game
# to supply the implicit prefix. For each extension we try both
# forms when resolving.
_IMPLICIT_PREFIXES = {
    "nif":  ["meshes\\"],
    "tri":  ["meshes\\"],
    "hkx":  ["meshes\\"],
    "egm":  ["meshes\\"],
    "bgem": ["materials\\"],
    "bgsm": ["materials\\"],
    "dds":  ["textures\\"],
    "wav":  ["sound\\"],
    "xwm":  ["sound\\"],
    "fuz":  ["sound\\voice\\"],
    "lip":  ["sound\\voice\\"],
    "pex":  ["scripts\\"],
    "seq":  ["seq\\"],
    "swf":  ["interface\\"],
}


def extract_paths_from_record(record):
    """Yield ``(path, subrecord_signature)`` for every path-like
    string found. Same path appearing in multiple subrecords of the
    same record produces multiple yields — each location matters
    when you're trying to fix references."""
    for sr in record.subrecords:
        for match in _PATH_RE.finditer(sr.data):
            raw = match.group(1)
            try:
                s = raw.decode("latin-1")
            except Exception:
                continue
            s = s.strip("\x00").strip().replace("/", "\\").lower()
            if (not s) or "\x00" in s or len(s) > 260 or s.startswith("\\"):
                continue
            yield (s, sr.signature)


def candidate_paths(rel: str) -> list[str]:
    """Return the set of Data-relative paths to try against the
    resolver: the raw string plus any extension-implied prefix."""
    ext = rel.rsplit(".", 1)[-1].lower()
    rel = rel.replace("/", "\\")
    cands = [rel]
    for prefix in _IMPLICIT_PREFIXES.get(ext, []):
        if not rel.startswith(prefix):
            cands.append(prefix + rel)
    return cands


def resolve_any(resolver: AssetResolver, rel: str) -> bool:
    """True if any candidate form of ``rel`` resolves via loose
    files or BSA."""
    for cand in candidate_paths(rel):
        if resolver.resolve(cand) is not None:
            return True
    return False


def scan(plugins, resolver: AssetResolver,
         ignore_re: re.Pattern | None = None,
         verbose: bool = False) -> dict[str, list[tuple]]:
    """Return ``{plugin_name: [(form_id, editor_id, subrec_sig, path), ...]}``
    for the given iterable of plugins. Each missing reference is
    listed with the record that made it and the subrecord signature
    that carried the path. Resolver caches hits so the same path hit
    from many plugins only costs one resolution."""
    by_plugin: dict[str, list[tuple]] = defaultdict(list)
    plugins = list(plugins)
    for i, plugin in enumerate(plugins, 1):
        name = plugin.file_path.name
        t0 = time.perf_counter()
        refs: list[tuple] = []  # (path, sig, form_id, editor_id)
        for record in plugin.records:
            fid = int(record.form_id)
            edid = record.editor_id or ""
            for path, sig in extract_paths_from_record(record):
                if ignore_re is not None and ignore_re.search(path):
                    continue
                refs.append((path, sig, fid, edid))
        unique_paths = {r[0] for r in refs}
        missing_paths = {p for p in unique_paths
                         if not resolve_any(resolver, p)}
        if missing_paths:
            # Dedupe on (path, sig, fid) — one line per distinct
            # subrecord location that referenced the missing file.
            seen: set[tuple] = set()
            for path, sig, fid, edid in refs:
                if path not in missing_paths:
                    continue
                key = (path, sig, fid)
                if key in seen:
                    continue
                seen.add(key)
                by_plugin[name].append((fid, edid, sig, path))
        if verbose:
            dt = time.perf_counter() - t0
            log.info("%3d/%3d  %-40s %5d refs (%d unique), "
                     "%d missing (%d hits), %.1fs",
                     i, len(plugins), name,
                     len(refs), len(unique_paths),
                     len(missing_paths), len(by_plugin[name]), dt)
    return by_plugin


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report missing asset references per plugin.")
    parser.add_argument("--data-dir",
                        help="Skyrim Data directory (auto-detected if "
                             "omitted).")
    parser.add_argument("--plugin", action="append",
                        help="Restrict to these plugins (repeatable). "
                             "Case-insensitive; accepts fnmatch "
                             "wildcards (e.g. 'BD*.esp', 'Cellan?.esp'). "
                             "Other plugins still load so override "
                             "chains are intact.")
    parser.add_argument("--summary", action="store_true",
                        help="Show only per-plugin counts, not the "
                             "missing paths themselves.")
    parser.add_argument("--verbose", action="store_true",
                        help="Progress line per plugin.")
    parser.add_argument("--ignore-regex",
                        help="Python regex; any matching path is "
                             "dropped from the output.")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="Write the report to FILE (UTF-8) "
                             "instead of stdout. Progress and errors "
                             "still go to stderr.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        data_dir = find_game_data("tes5")
        if data_dir is None:
            print("ERROR: Couldn't auto-detect Skyrim Data directory. "
                  "Pass --data-dir.", file=sys.stderr)
            return 1

    lo = LoadOrder.from_game("tes5", active_only=True)
    plugin_set = PluginSet(lo)
    plugin_set.load_all()

    if args.plugin:
        # fnmatch each pattern against every plugin's name so wildcards
        # (`*.esp`, `BD*.esp`, `Cellan?.esp`) work regardless of shell.
        # Windows cmd/PowerShell don't expand globs before exec, so the
        # script has to handle them itself.
        patterns = [p.lower() for p in args.plugin]
        selected = [p for p in plugin_set
                    if any(fnmatch.fnmatchcase(p.file_path.name.lower(), pat)
                           for pat in patterns)]
        if not selected:
            print(f"No plugins match {args.plugin}", file=sys.stderr)
            return 1
    else:
        selected = list(plugin_set)

    ignore_re = re.compile(args.ignore_regex) if args.ignore_regex else None

    with AssetResolver.for_data_dir(data_dir) as resolver:
        by_plugin = scan(selected, resolver,
                         ignore_re=ignore_re, verbose=args.verbose)

    if args.output:
        sink = open(args.output, "w", encoding="utf-8")
    else:
        sink = sys.stdout

    try:
        if not by_plugin:
            print("No missing references found.", file=sink)
            return 0

        for name in sorted(by_plugin):
            items = sorted(by_plugin[name])
            # Unique missing *paths* per plugin, for the header count.
            unique = len({path for _, _, _, path in items})
            print(f"\n=== {name} -- {len(items)} references "
                  f"({unique} unique missing) ===", file=sink)
            if args.summary:
                continue
            # Column widths: form id is fixed 8 hex; edid adapts to
            # the longest in this plugin (minimum 20 for readability).
            edid_w = max([20] + [len(edid) for _, edid, _, _ in items])
            for fid, edid, sig, path in items:
                print(f"  {fid:08X}  {(edid or '-'):<{edid_w}}  "
                      f"{sig}  {path}", file=sink)

        total = sum(len(v) for v in by_plugin.values())
        print(f"\n{total} missing references across {len(by_plugin)} plugins.",
              file=sink)
    finally:
        if sink is not sys.stdout:
            sink.close()
            log.info("Report written to %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
