"""
alr.rag_builders.section_rebuilder
==================================

Rebuild one or more attributes' section databases from the per-document
analysis JSONs: the section Excel/JSON is rewritten from source, then the
FAISS index is re-embedded from that Excel.

Two source modes, chosen by the ``sources`` argument:

* ``sources=None`` -- ordinary storage space. The space's own analysis JSONs
  are the source and every recorded document is included, so a rebuild also
  picks up documents added since the last incremental sync.

* ``sources=[...]`` -- common database. The common DB holds no analysis JSONs
  of its own, so content is pulled from the configured source storage spaces.
  Membership and row order are frozen to what the common DB already contains:
  a source space that has grown since the common DB was built must not leak
  new documents into the one attribute being rebuilt, or that attribute ends
  up longer than every other one.

Alignment rules enforced here (see the vector-DB alignment work):

* Excel is the source of truth for embedding; the section JSON is regenerated
  to mirror it row-for-row.
* Blank content becomes a single-space placeholder row, never a dropped row.
* Excel -> JSON -> index, each written atomically, status log updated last.
  A crash leaves the attribute stale but self-consistent, never drifted.

INTEGRATION POINTS
------------------
The imports in the "project helpers" block below are the only coupling to the
rest of the package. If a name lives somewhere else in your tree, fix it there
and nothing else in this module needs to change.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from datetime import datetime as dt      # Alias avoids conflicts

import pandas as pd

try:
    from colorama import Fore, Style
except ImportError:  # colorama is optional for this module
    class _NoColor:
        def __getattr__(self, _):
            return ""
    Fore = Style = _NoColor()

# --------------------------------------------------------------------------
# project helpers
# --------------------------------------------------------------------------
from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.sections import (
    ALL_RAG_SECTIONS, ALR_SECTIONS, RAG_SOURCE_BY_KEY, build_sections_map_full,
)
from alr.common.excel_utils import extract_column
from alr.rag_builders.text_db_updater import (
    _fetch_metadata,
    _load_abstract_json,
    _load_recorded_abstracts,
)


def _all_keys():
    """Every rebuildable attribute: abstract + Introduction + Results & Conclusion."""
    return [spec.key for spec in ALL_RAG_SECTIONS]


def _analysis_source_of(key):
    """Which analysis JSON an attribute's content comes from."""
    return RAG_SOURCE_BY_KEY.get(key, "abstract")


def _load_source_json(MF, uuid, source):
    """One document's analysis JSON for the given source ({} when absent)."""
    if source == "abstract":
        return _load_abstract_json(MF, uuid) or {}
    path = MF.intro_json_path if source == "intro" else MF.rescon_json_path
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size == 0:
            return {}
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _recorded_for_source(MF, source):
    """
    The documents a space has recorded for one analysis source, in log order.

    Each source has its own analysis log, so an Introduction attribute is
    rebuilt from the documents that actually have Introduction data — not from
    the abstract log, which would emit "Not Found" rows for every document
    whose introduction was never analyzed.
    """
    if source == "abstract":
        return [str(u) for u in (_load_recorded_abstracts(MF) or [])]
    log_path = MF.AD_Intro_log_path if source == "intro" else MF.AD_ResCon_log_path
    if log_path and Path(log_path).exists():
        try:
            return [str(u) for u in extract_column(log_path, "UUID")]
        except Exception:  # noqa: BLE001 - an unreadable log means "nothing recorded"
            return []
    return []

# `update_VDB_status` and the embedding helpers are imported lazily inside the
# functions that need them: db_manager imports this module, so importing it
# back at module scope would be circular.


# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

#: Content written for a row whose source value is blank. A blank row is kept
#: as a placeholder rather than dropped, so Excel row N always corresponds to
#: index vector N.
BLANK_PLACEHOLDER = " "

#: Candidate filenames for the common DB's config, searched in this order in
#: the common DB folder. Set COMMON_DB_CONFIG_NAME to pin one explicitly.
COMMON_DB_CONFIG_NAME = None
COMMON_DB_CONFIG_CANDIDATES = (
    "common_db_config.json",
    "Common_DB_config.json",
    ".common_db_config.json",
    "common_db.json",
)

#: Keys under which the config may store the list of source storage spaces.
COMMON_DB_SOURCE_KEYS = ("sources", "source_spaces", "storage_spaces", "Sources")
COMMON_DB_MATCH_KEYS = ("match_filename", "match_by_filename", "Match_filename")

#: Members that could not be resolved back to a source document are listed in
#: ``{YYYY-MM-DD_HHMMSS}_Rebuild_unresolved_members.txt`` — one file per run, so
#: a later attempt never overwrites or appends to the evidence of an earlier one.
REBUILD_UNRESOLVED_LOG = "Rebuild_unresolved_members.txt"


def unresolved_log_name(when=None) -> str:
    """Timestamped filename for one run's unresolved-members log."""
    from datetime import datetime
    stamp = (when or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return f"{stamp}_{REBUILD_UNRESOLVED_LOG}"

#: Column order for a section Excel. "Count" is present only for list-valued
#: attributes (Results, Research Areas, Key Concepts), matching what
#: text_db_updater._save_list_section / _save_single_section already write.
_BASE_COLUMNS = ["UUID", "Original_UUID", "Title", "Filename"]


class CommonRebuildError(RuntimeError):
    """Raised when an attribute cannot be rebuilt without dropping documents."""

    def __init__(self, key, unresolved):
        self.key = key
        self.unresolved = list(unresolved)
        super().__init__(
            f"{key}: {len(self.unresolved)} member(s) could not be resolved back "
            f"to a source document; refusing to write a short section Excel."
        )


# --------------------------------------------------------------------------
# common DB config
# --------------------------------------------------------------------------

def find_common_db_config(common_path):
    """Return the Path of the common DB's config file, or None."""
    root = Path(common_path)
    names = ([COMMON_DB_CONFIG_NAME] if COMMON_DB_CONFIG_NAME
             else list(COMMON_DB_CONFIG_CANDIDATES))
    for name in names:
        candidate = root / name
        if candidate.is_file():
            return candidate
    # Fall back to any single *config*.json sitting in the common DB root.
    loose = [p for p in root.glob("*.json") if "config" in p.name.lower()]
    return loose[0] if len(loose) == 1 else None


def load_common_db_sources(common_path):
    """
    Read the common DB's config and return ``(sources, match_filename)``.

    ``sources`` is a list of source storage-space paths (possibly empty);
    ``match_filename`` is the stored matching mode, or None if not recorded.
    """
    cfg_path = find_common_db_config(common_path)
    if cfg_path is None:
        return [], None
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] Could not read common DB config {cfg_path}: {e}")
        return [], None

    if not isinstance(cfg, dict):
        return [], None

    sources = []
    for key in COMMON_DB_SOURCE_KEYS:
        value = cfg.get(key)
        if isinstance(value, (list, tuple)):
            sources = [str(s).strip() for s in value if str(s).strip()]
            break

    match_filename = None
    for key in COMMON_DB_MATCH_KEYS:
        if key in cfg:
            match_filename = bool(cfg[key])
            break

    return sources, match_filename


# --------------------------------------------------------------------------
# row construction
# --------------------------------------------------------------------------

def _placeholder(value):
    """Blank/None content becomes the placeholder; real text passes through."""
    if value is None:
        return BLANK_PLACEHOLDER
    text = str(value)
    return text if text.strip() else BLANK_PLACEHOLDER


def _section_rows(uuid, content_value, title, file_name):
    """
    Build the Excel rows for one document's value of one attribute.

    List-valued attributes expand to one row per item with the ``<uuid>_<idx>``
    UUID and 1-based ``Count`` that text_db_updater writes; single-valued
    attributes produce exactly one row keyed by the bare UUID. An empty list
    still yields one placeholder row so the document keeps its slot.
    """
    base = {"Original_UUID": str(uuid), "Title": title or "",
            "Filename": file_name or ""}

    if isinstance(content_value, list):
        if not content_value:
            return [dict(base, UUID=f"{uuid}_0", Count="1",
                         Content=BLANK_PLACEHOLDER)]
        return [dict(base, UUID=f"{uuid}_{idx}", Count=str(idx + 1),
                     Content=_placeholder(item))
                for idx, item in enumerate(content_value)]

    return [dict(base, UUID=str(uuid), Content=_placeholder(content_value))]


def _rows_to_frame(rows):
    """Rows -> DataFrame with a stable column order; Count only if used."""
    has_count = any("Count" in r for r in rows)
    columns = list(_BASE_COLUMNS)
    if has_count:
        columns.append("Count")
    columns.append("Content")
    frame = pd.DataFrame(rows, columns=columns)
    return frame.fillna("")


# --------------------------------------------------------------------------
# atomic writes
# --------------------------------------------------------------------------

def _atomic_replace(tmp_path, final_path):
    os.replace(tmp_path, final_path)


def _write_section_excel_atomic(path, frame):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".xlsx", dir=str(path.parent))
    os.close(fd)
    try:
        frame.to_excel(tmp, index=False)
        _atomic_replace(tmp, str(path))
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _write_section_json_atomic(path, frame):
    """Mirror the Excel exactly -- same rows, same order, same values."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    records = frame.to_dict(orient="records")
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        _atomic_replace(tmp, str(path))
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _backup_once(path, suffix=".prerebuild"):
    """Keep one copy of the pre-rebuild file next to the original."""
    path = Path(path)
    if path.is_file():
        backup = path.with_suffix(path.suffix + suffix)
        try:
            shutil.copy2(str(path), str(backup))
        except OSError as e:
            print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] Could not back up {path.name}: {e}")


# --------------------------------------------------------------------------
# reading an existing section Excel
# --------------------------------------------------------------------------

def read_section_excel(path):
    """
    Read a section Excel as strings with blanks preserved as empty cells.

    ``keep_default_na=False`` is deliberate: a dropped or NaN-coerced row is
    exactly what breaks positional alignment against the FAISS index.
    """
    path = Path(path)
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_excel(path, dtype=str, keep_default_na=False)


def read_content_column(path):
    """Positional list of Content values, blanks replaced by the placeholder."""
    frame = read_section_excel(path)
    if frame.empty or "Content" not in frame.columns:
        return []
    return [_placeholder(v) for v in frame["Content"].tolist()]


def _members_from_frame(frame, match_filename):
    """Ordered-unique membership keys taken from a section Excel."""
    if frame.empty:
        return []
    if match_filename:
        column = "Filename"
    else:
        column = "Original_UUID" if "Original_UUID" in frame.columns else "UUID"
    if column not in frame.columns:
        return []

    seen, members = set(), []
    for value in frame[column].tolist():
        key = str(value).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        members.append(key)
    return members


def read_common_members(common_path, match_filename):
    """
    The common DB's membership, in the order it was built.

    Taken from the first non-empty section Excel found in canonical section
    order. Every attribute is written per document in the same pass, so any
    intact attribute yields the same document order.
    """
    VDB = Vec_DB_Manager(common_path)
    section_paths = build_sections_map_full(VDB)
    for spec in ALR_SECTIONS:
        paths = section_paths.get(spec.key)
        if not paths:
            continue
        members = _members_from_frame(read_section_excel(paths[0]), match_filename)
        if members:
            print(f"[Rebuild] Membership taken from '{spec.key}': "
                  f"{len(members)} document(s).")
            return members
    return []


# --------------------------------------------------------------------------
# locating source documents
# --------------------------------------------------------------------------

def _index_sources(sources, match_filename, should_cancel=None):
    """
    Map every membership key to the ``(source_path, uuid)`` it came from.

    Earlier sources win, matching the dedup precedence of the common DB build.
    """
    located = {}
    for src in sources:
        if should_cancel and should_cancel():
            return located
        try:
            MF = DataAnalyzeManager(src)
            uuids = _load_recorded_abstracts(MF) or []
        except Exception as e:
            print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] Source unreadable: {src} ({e})")
            continue

        added = 0
        for uuid in uuids:
            try:
                MF.update_id_files(uuid)
                _, file_name = _fetch_metadata(MF, uuid)
            except Exception:
                continue
            key = str(file_name).strip() if match_filename else str(uuid).strip()
            if not key or key in located:
                continue
            located[key] = (src, str(uuid))
            added += 1
        print(f"[Rebuild] Indexed {added} document(s) from {src}")
    return located


def _log_unresolved(storage_path, key, unresolved, log_path=None):
    """
    Write one run's unresolved members to a timestamped file in the space.

    ``log_path`` lets several calls within one run share a single file; without
    it a fresh timestamped file is created, so each run's evidence stands on its
    own instead of accumulating in one ever-growing text file.
    """
    from datetime import datetime
    path = Path(log_path) if log_path else Path(storage_path) / unresolved_log_name()
    try:
        new_file = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            if new_file:
                f.write(f"Rebuild run {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Storage: {storage_path}\n\n")
            f.write(f"--- {key} ({len(list(unresolved))} unresolved) ---\n")
            for item in unresolved:
                f.write(f"{item}\n")
            f.write("\n")
    except OSError:
        pass
    return path


# --------------------------------------------------------------------------
# index rebuild
# --------------------------------------------------------------------------

def _rebuild_index_from_excel(VDB, key, excel_path, bin_path):
    """Re-embed one attribute's index from its section Excel, positionally."""
    from alr.rag_builders.db_manager import update_VDB_status
    from alr.rag_builders.vector_db_updater import (
        create_faiss_index_cosine,
        save_index_file,
        vectorize_strings,
    )

    strings = read_content_column(excel_path)
    if not strings:
        print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] '{key}': no rows to embed; index untouched.")
        return 0

    print(Fore.CYAN + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] '{key}': embedding {len(strings)} row(s)...")
    embeds = vectorize_strings(strings)
    if len(embeds) != len(strings):
        raise RuntimeError(
            f"{key}: embedding returned {len(embeds)} vector(s) for "
            f"{len(strings)} row(s); refusing to save a misaligned index.")

    index = create_faiss_index_cosine(embeds)
    _backup_once(bin_path)
    save_index_file(index, bin_path)

    vec_count = getattr(index, "ntotal", len(embeds))
    update_VDB_status(VDB, key, len(strings), vec_count)
    print(Fore.GREEN + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] '{key}': index rebuilt "
                       f"({len(strings)} rows / {vec_count} vectors).")
    return vec_count


# --------------------------------------------------------------------------
# one attribute
# --------------------------------------------------------------------------

def rebuild_one_section(storage_path, key, plan, match_filename=False,
                        should_cancel=None):
    """
    Rebuild a single attribute: Excel, then JSON, then index.

    ``plan`` is the ordered list of ``(source_path, uuid)`` pairs to emit, one
    per member document -- see :func:`_build_plan`.
    """
    VDB = Vec_DB_Manager(storage_path)
    # Every RAG attribute, not just the abstract ones: the Introduction and
    # Results & Conclusion attributes have their own Excel/JSON/index paths.
    section_paths = build_sections_map_full(VDB, only=_all_keys())
    if key not in section_paths:
        raise KeyError(f"Unknown attribute {key!r}")
    excel_path, json_path, bin_path = section_paths[key]

    source = _analysis_source_of(key)
    rows = []
    cache = {}
    for src, uuid in plan:
        if should_cancel and should_cancel():
            return None
        MF = cache.get(src)
        if MF is None:
            MF = cache[src] = DataAnalyzeManager(src)
        MF.update_id_files(uuid)
        title, file_name = _fetch_metadata(MF, uuid)
        data = _load_source_json(MF, uuid, source)
        content = data.get(key, "Not Found") if data else "Not Found"
        rows.extend(_section_rows(uuid, content, title, file_name))

    if not rows:
        print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] '{key}': nothing to write; skipped.")
        return 0

    frame = _rows_to_frame(rows)
    _backup_once(excel_path)
    _write_section_excel_atomic(excel_path, frame)
    _write_section_json_atomic(json_path, frame)
    _rebuild_index_from_excel(VDB, key, excel_path, bin_path)
    return len(frame)


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------

def _build_plan(storage_path, sources, match_filename, should_cancel=None, source=None):
    """
    Decide which documents to emit, in which order.

    Single space: everything the space has recorded for ``source``, in log
    order (each analysis source has its own log, so an Introduction attribute
    is rebuilt from the documents that actually have Introduction data).
    Common DB: exactly the members the common DB already holds, in its order —
    membership is frozen there, so it is the same plan for every attribute.
    """
    if not sources:
        MF = DataAnalyzeManager(storage_path)
        uuids = _recorded_for_source(MF, source or "abstract")
        return [(storage_path, str(u)) for u in uuids], []

    members = read_common_members(storage_path, match_filename)
    if not members:
        raise CommonRebuildError(
            "<membership>",
            ["no populated section Excel found in the common DB"])

    located = _index_sources(sources, match_filename, should_cancel)
    plan, unresolved = [], []
    for member in members:
        if member in located:
            plan.append(located[member])
        else:
            unresolved.append(member)
    return plan, unresolved


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------

def rebuild_section_databases(storage_path, keys=None, sources=None,
                              match_filename=False, should_cancel=None,
                              progress_callback=None):
    """
    Rebuild the chosen attributes' section Excel/JSON and FAISS index.

    Args:
        storage_path: the storage space, or the common DB folder when
            ``sources`` is given.
        keys: attribute keys to rebuild; None means all of them.
        sources: source storage spaces for a common DB rebuild. None (the
            default) keeps the original single-space behaviour, so existing
            callers are unaffected.
        match_filename: how common DB membership is keyed -- must match how
            the common DB was built.
        should_cancel: callable returning True to abort between documents.
        progress_callback: ``callback(done, total, text)``.

    Returns:
        The list of attribute keys that failed. An empty list means every
        requested attribute was rebuilt.
    """
    keys = list(keys) if keys else _all_keys()
    unknown = [k for k in keys if k not in RAG_SOURCE_BY_KEY]
    if unknown:
        print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] Not rebuildable attribute(s): {', '.join(unknown)}")
    keys = [k for k in keys if k in RAG_SOURCE_BY_KEY]
    total = len(keys) + len(unknown)

    def report(done, text):
        if progress_callback:
            progress_callback(done, total, text)

    report(0, "Resolving source documents...")

    # One plan per analysis source. A single space has a separate log per
    # source, so the Introduction attributes must be rebuilt from the
    # Introduction log; a common DB freezes its membership, so all attributes
    # share the one plan built from what it already holds.
    plans = {}
    needed_sources = {_analysis_source_of(k) for k in keys} or {"abstract"}
    # One log file for this whole run (created only if something is unresolved).
    run_log = Path(storage_path) / unresolved_log_name()
    for src_name in sorted(needed_sources):
        try:
            plan, unresolved = _build_plan(storage_path, sources, match_filename,
                                           should_cancel, source=src_name)
        except CommonRebuildError as e:
            print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] {e}")
            return list(keys) + unknown

        if unresolved:
            # A partial rebuild would drop these documents from the rebuilt
            # attributes only, leaving the common DB ragged. Refuse instead.
            log_path = _log_unresolved(storage_path, f"membership ({src_name})",
                                       unresolved, log_path=run_log)
            print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] {len(unresolved)} member(s) could not be "
                             f"resolved to a source document -- listed in {log_path}. "
                             f"Nothing was rebuilt.")
            return list(keys) + unknown
        plans[src_name] = plan

    if not any(plans.values()):
        print(Fore.YELLOW + "[Rebuild] No documents to rebuild from.")
        return list(keys) + unknown
    for src_name, plan in sorted(plans.items()):
        print(f"[Rebuild] {src_name}: {len(plan)} document(s) recorded.")
    print(f"[Rebuild] {len(keys)} attribute(s) in: {storage_path}")

    failures = list(unknown)
    for i, key in enumerate(keys):
        if should_cancel and should_cancel():
            failures.extend(keys[i:])
            print(Fore.YELLOW + "[Rebuild] Cancelled.")
            break
        plan = plans.get(_analysis_source_of(key)) or []
        if not plan:
            failures.append(key)
            print(Fore.YELLOW + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] '{key}' skipped: this space has no "
                                f"{_analysis_source_of(key)} data recorded.")
            continue
        report(i, f"Rebuilding '{key}' ({i + 1}/{len(keys)})...")
        try:
            result = rebuild_one_section(storage_path, key, plan,
                                         match_filename=match_filename,
                                         should_cancel=should_cancel)
            if result is None:  # cancelled mid-attribute
                failures.extend(keys[i:])
                print(Fore.YELLOW + "[Rebuild] Cancelled.")
                break
        except Exception as e:
            failures.append(key)
            print(Fore.RED + f"\n[{dt.now().strftime('%Y-%m-%d %H:%M:%S')}]:[Rebuild] '{key}' failed: {e}")

    report(total, "Done.")
    return failures