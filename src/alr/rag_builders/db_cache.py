"""
alr.rag_builders.db_cache
=========================

Process-wide write-behind cache for the per-section JSON databases.

Two different writers append to the SAME ``{section}.json`` file:

  * :func:`alr.rag_builders.text_db_updater.save_to_db` writes the per-section
    Excel/JSON DB rows (list sections under ``"{uuid}_{idx}"`` UUIDs), and
  * :func:`alr.rag_builders.master_excel_db_builder.save_to_db` writes the
    master workbook's rows (list sections consolidated under the bare UUID).

While a build is running both hold their data in memory and write once, so
they must share ONE image of each JSON file — with an image each, whichever
flushed last would silently drop the other's entries.

The cache is only active inside a scope (opened by
``master_excel_db_builder.workbook_session``); outside one, both writers keep
their original read-modify-write-per-entry behaviour.
"""

import json
from pathlib import Path

from colorama import Fore

_entries = {}   # str(path) -> {"path", "data", "uuids", "dirty"}
_depth = 0


def active() -> bool:
    """True while a build scope is open (writes are batched)."""
    return _depth > 0


def open_scope():
    global _depth
    _depth += 1


def close_scope():
    """Leave one scope level; the outermost one flushes and clears."""
    global _depth
    _depth = max(0, _depth - 1)
    if _depth == 0:
        try:
            flush()
        finally:
            _entries.clear()


def get(json_path):
    """The shared image of one JSON DB: {"data": list, "uuids": set, ...}."""
    key = str(json_path)
    entry = _entries.get(key)
    if entry is None:
        data = []
        p = Path(key)
        if p.exists() and p.stat().st_size > 0:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                data = loaded if isinstance(loaded, list) else []
            except (json.JSONDecodeError, OSError):
                data = []
        entry = {
            "path": p, "data": data,
            "uuids": {str(i.get("UUID")) for i in data if isinstance(i, dict)},
            "dirty": False,
        }
        _entries[key] = entry
    return entry


def add(json_path, data_entry) -> bool:
    """Append ``data_entry`` unless its UUID is already registered.
    Returns True when it was added."""
    entry = get(json_path)
    uuid = str(data_entry.get("UUID"))
    if uuid in entry["uuids"]:
        return False
    entry["data"].append(data_entry)
    entry["uuids"].add(uuid)
    entry["dirty"] = True
    return True


def has(json_path, uuid) -> bool:
    return str(uuid) in get(json_path)["uuids"]


def flush():
    """Write every dirty JSON DB out. Safe to call repeatedly."""
    for entry in _entries.values():
        if not entry["dirty"]:
            continue
        try:
            entry["path"].parent.mkdir(parents=True, exist_ok=True)
            with open(entry["path"], "w", encoding="utf-8") as f:
                json.dump(entry["data"], f, indent=4)
            entry["dirty"] = False
        except Exception as e:  # noqa: BLE001 - report, keep the rest going
            print(Fore.RED + f"❌ Failed to save JSON {entry['path']}: {e}")
