"""Report file references in the active load order that resolve to
neither a loose file nor any BSA entry, grouped by the plugin that
made the reference.

Run from the furrifier project root with the package installed:

    python scripts/find_missing_assets.py
    python scripts/find_missing_assets.py --data-dir "C:/Skyrim SE/Data"
    python scripts/find_missing_assets.py --plugin "BD*.esp"
    python scripts/find_missing_assets.py --verbose

Output is grouped by plugin; within each plugin, missing paths are
sorted. A count line per plugin shows the total. Suppress the
per-file list with ``--summary`` if you just want totals.

How extraction works:

The script only scans record types that esplib already schemas
(``esplib.defs.tes5``). For each schemaed record, it walks the
schema's members — recursing through ``EspGroup`` blocks like
ARMA's "Male World Model" — and picks out subrecord signatures
whose ``value_def`` is an ``EspString`` semantically tagged as a
path (``name='model'`` or ``name='icon'``). Those are the only
subrecords scanned; their values come from
``record[signature]`` after a one-shot ``record.bind_schema()``,
so we get esplib's parsed string directly — no raw-byte work in
this script.

Records whose signature has no esplib schema are counted in a
"skipped" tally and reported at the end of the run. That tally
tells you which record types in your load order would benefit
from an esplib schema (or, failing that, a script-side fallback).

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
from esplib.defs import tes5
from esplib.defs.types import EspString

from furrifier.facegen.assets import AssetResolver


log = logging.getLogger("find_missing_assets")


# EspString.name values esplib uses to mean "this string is a file
# path." Update when esplib gains a new path-bearing record schema
# under a fresh semantic name. Today: 'model' (MODL, MOD2..MOD5),
# 'icon' (ICON / MICO), 'filename' (HDPT NAM1 chargen morph),
# 'texture' (TXST TX00..TX07).
_PATH_VALUE_NAMES = {"model", "icon", "filename", "texture"}


def _collect_path_subrecords(schema) -> set[str]:
    """Recursively walk an EspRecord/EspGroup's members, returning
    the set of subrecord signatures whose value is a path string —
    detected by ``EspString`` with semantic name ``"model"`` or
    ``"icon"``. Recurses through nested ``EspGroup`` members
    (e.g. ARMA's "Male World Model" group wrapping MOD2)."""
    out: set[str] = set()
    for member in getattr(schema, "members", ()):
        if hasattr(member, "members"):
            out.update(_collect_path_subrecords(member))
            continue
        if not hasattr(member, "signature") or not hasattr(member, "value_def"):
            continue
        v = member.value_def
        if (isinstance(v, EspString)
                and getattr(v, "name", None) in _PATH_VALUE_NAMES):
            out.add(member.signature)
    return out


def _build_schema_index():
    """Return ``(schemas_by_sig, path_subrecords)``.

    schemas_by_sig: ``{record_signature: EspRecord}`` for every
    record schema esplib's tes5 module exposes. Used to
    ``record.bind_schema(...)`` before reading subrecord values.

    path_subrecords: ``{record_signature: set(subrecord_signatures)}``
    derived purely from those schemas — no hardcoded augment. If a
    record type isn't in esplib's schemas, it's not scanned at all.
    """
    schemas: dict[str, "any"] = {}
    paths: dict[str, set[str]] = {}
    for name in dir(tes5):
        if (not name.isupper() or len(name) > 4
                or name.startswith("_")):
            continue
        schema = getattr(tes5, name, None)
        if schema is None or not hasattr(schema, "members"):
            continue
        # Filter out struct schemas (e.g. ACBS) that share a module-
        # level name with their subrecord signature but aren't records
        # themselves. EspRecord exposes a top-level signature equal to
        # its record sig.
        if getattr(schema, "signature", None) != name:
            continue
        schemas[name] = schema
        sigs = _collect_path_subrecords(schema)
        if sigs:
            paths[name] = sigs
    return schemas, paths


_SCHEMA_BY_SIG, _PATH_SUBRECORDS = _build_schema_index()


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
    """Yield ``(path, subrecord_signature)`` for every path-bearing
    subrecord on this record, using esplib's parsed-value access.

    We bind the record to its schema (one-shot; idempotent thanks
    to the ``record.schema`` check) and then read each known path
    subrecord via ``record[sig]`` — esplib gives us back the
    decoded string, no raw-byte handling here. Records whose
    signature has no esplib schema return nothing.
    """
    sig_set = _PATH_SUBRECORDS.get(record.signature)
    if not sig_set:
        return
    schema = _SCHEMA_BY_SIG.get(record.signature)
    if schema is None:
        return
    if record.schema is None:
        record.bind_schema(schema)
    for sig in sig_set:
        try:
            value = record[sig]
        except KeyError:
            continue
        if not value or not isinstance(value, str):
            continue
        yield (value.strip().replace("/", "\\").lower(), sig)


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


def _build_load_order_remap(plugin_set):
    """Build ``{plugin_name_lower: (is_esl, idx_among_kind)}`` so we
    can format form ids the way xEdit / Vortex display them.

    esplib's ``LoadOrder.index_of`` (and therefore
    ``record.normalize_form_id``) treats ESLs as regular slots,
    which produces high-byte indices that are off by however many
    ESLs precede the plugin. ESLs actually live in the FE
    namespace (``FE NNN ooo`` — 12-bit ESL index, 12-bit object
    index). This helper computes the right counter for each kind.
    """
    remap: dict[str, tuple[bool, int]] = {}
    regular_idx = 0
    esl_idx = 0
    for plugin in plugin_set:
        name = plugin.file_path.name.lower()
        if plugin.is_esl:
            remap[name] = (True, esl_idx)
            esl_idx += 1
        else:
            remap[name] = (False, regular_idx)
            regular_idx += 1
    return remap


def _format_fid(record, remap: dict) -> str:
    """xEdit-style form id for a record's self-defined entry.
    Regular plugins → ``XXxxxxxx`` (8 hex chars). ESL plugins →
    ``FENNNooo`` where NNN is the ESL load index and ooo is the
    12-bit object index."""
    if record.plugin is None:
        return f"{int(record.form_id) & 0xFFFFFFFF:08X}"
    name = record.plugin.file_path.name.lower()
    info = remap.get(name)
    raw = int(record.form_id)
    if info is None:
        return f"{raw & 0xFFFFFFFF:08X}"
    is_esl, idx = info
    if is_esl:
        return f"FE{idx & 0xFFF:03X}{raw & 0xFFF:03X}"
    return f"{idx & 0xFF:02X}{raw & 0xFFFFFF:06X}"


def scan(plugins, resolver: AssetResolver,
         lo_remap: dict,
         ignore_re: re.Pattern | None = None,
         verbose: bool = False
         ) -> tuple[dict[str, list[tuple]], dict[str, int]]:
    """Return ``(by_plugin, unschemaed_counts)``.

    ``by_plugin`` is ``{plugin_name: [(form_id_str, editor_id,
    subrec_sig, path), ...]}`` of missing references per plugin.
    ``form_id_str`` is the xEdit-formatted hex id from
    ``_format_fid`` (ESL-aware).

    ``unschemaed_counts`` is ``{record_signature: count}`` for record
    types encountered in the scanned plugins that esplib has no
    schema for — meaning the script can't read their paths. Use it
    to decide whether a missing record type should grow an esplib
    schema or be added to a script-side fallback.
    """
    by_plugin: dict[str, list[tuple]] = defaultdict(list)
    unschemaed: dict[str, int] = defaultdict(int)
    plugins = list(plugins)
    for i, plugin in enumerate(plugins, 1):
        name = plugin.file_path.name
        t0 = time.perf_counter()
        refs: list[tuple] = []  # (path, sig, fid_str, editor_id)
        for record in plugin.records:
            if record.signature not in _SCHEMA_BY_SIG:
                unschemaed[record.signature] += 1
                continue
            fid = _format_fid(record, lo_remap)
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
    return by_plugin, dict(unschemaed)


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

    lo_remap = _build_load_order_remap(plugin_set)

    with AssetResolver.for_data_dir(data_dir) as resolver:
        by_plugin, unschemaed = scan(
            selected, resolver, lo_remap=lo_remap,
            ignore_re=ignore_re, verbose=args.verbose)

    if args.output:
        sink = open(args.output, "w", encoding="utf-8")
    else:
        sink = sys.stdout

    try:
        if by_plugin:
            for name in sorted(by_plugin):
                items = sorted(by_plugin[name])
                # Unique missing *paths* per plugin, for the header count.
                unique = len({path for _, _, _, path in items})
                print(f"\n=== {name} -- {len(items)} references "
                      f"({unique} unique missing) ===", file=sink)
                if args.summary:
                    continue
                # Column widths: form id is the xEdit-style hex
                # string (fixed 8 chars: XXxxxxxx for regular,
                # FENNNooo for ESL); edid adapts to the longest in
                # this plugin (minimum 20 for readability).
                edid_w = max([20] + [len(edid) for _, edid, _, _ in items])
                for fid, edid, sig, path in items:
                    print(f"  {fid:>8}  {(edid or '-'):<{edid_w}}  "
                          f"{sig}  {path}", file=sink)

            total = sum(len(v) for v in by_plugin.values())
            print(f"\n{total} missing references across "
                  f"{len(by_plugin)} plugins.", file=sink)
        else:
            print("No missing references found.", file=sink)

        # Coverage report — record types we couldn't scan because
        # esplib has no schema for them. Hugh decides per-type
        # whether to grow esplib or work around in the script.
        if unschemaed:
            print(file=sink)
            print("--- Record types skipped (no esplib schema) ---",
                  file=sink)
            ordered = sorted(unschemaed.items(),
                             key=lambda kv: (-kv[1], kv[0]))
            for sig, count in ordered:
                print(f"  {sig:6s} {count}", file=sink)
            print(f"  {'TOTAL':6s} {sum(unschemaed.values())} records "
                  f"across {len(unschemaed)} record types",
                  file=sink)
    finally:
        if sink is not sys.stdout:
            sink.close()
            log.info("Report written to %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
