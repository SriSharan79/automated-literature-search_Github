"""
alr.common.sql_store
====================

A lightweight SQLite store for analyzed-document *summaries*, kept alongside the
existing file-based storage (see :mod:`alr.common.file_manager`). One row per
analyzed document, holding its metadata, processing status, and the seven
analyzed abstract sections plus the intro/references payloads.

The database lives at a single, constant location so results from any storage
folder are consolidated in one queryable place:

    ~/Automated Literature Review/alr_analyzed_data.db

Only the Python standard library ``sqlite3`` is used - no extra dependencies.
Section names come from :data:`alr.common.sections.ALR_SECTIONS`, the single
source of truth, so the SQL columns can never drift from the JSON keys.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from alr.common.file_manager import ALR_main_folder, DataAnalyzeManager
from alr.common.sections import ALR_SECTIONS

# Constant, app-wide database location.
DB_PATH = Path(ALR_main_folder) / "alr_analyzed_data.db"


def _slug(section_key: str) -> str:
    """Turn a section key ("Research Problem") into a column name ("research_problem")."""
    return section_key.strip().lower().replace(" ", "_")


# Section key -> column name, derived from the canonical registry.
SECTION_COLUMNS = {spec.key: _slug(spec.key) for spec in ALR_SECTIONS}
# Sections whose analyzed value is a list (stored as a JSON string).
LIST_SECTIONS = {"Results", "Research Areas", "Key Concepts"}

# The key store_to_json_with_text writes for the raw abstract text.
ABSTRACT_TEXT_KEY = "Abstract Text identified:"

# Full column order for the documents table.
COLUMNS = (
    ["uuid", "title", "filename", "relative_path", "timestamp", "time_taken",
     "status_sectioning", "status_references", "status_abstract", "status_introduction"]
    + list(SECTION_COLUMNS.values())
    + ["abstract_text", "introduction_json", "references_json",
       "source_folder", "created_at", "updated_at"]
)


def _read_json(path):
    """Load a JSON file, returning (dict_or_None, raw_text_or_None)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        return json.loads(raw), raw
    except (OSError, json.JSONDecodeError):
        return None, None


class AnalyzedDataStore:
    """CRUD access to the analyzed-document SQLite database."""

    def __init__(self, db_path=DB_PATH):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        cols_sql = ",\n            ".join(
            f"{c} TEXT PRIMARY KEY" if c == "uuid" else f"{c} TEXT" for c in COLUMNS
        )
        with self._connect() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS documents (\n            {cols_sql}\n        )")

    # -- writes -------------------------------------------------------------
    def upsert_document(self, record: dict):
        """
        Insert or update a document by uuid. ``created_at`` is set only on the
        first insert; ``updated_at`` is refreshed every time.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = {c: record.get(c) for c in COLUMNS}
        data["created_at"] = now
        data["updated_at"] = now

        placeholders = ", ".join("?" for _ in COLUMNS)
        col_list = ", ".join(COLUMNS)
        # On conflict, update everything except uuid and created_at.
        update_cols = [c for c in COLUMNS if c not in ("uuid", "created_at")]
        update_sql = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
        sql = (
            f"INSERT INTO documents ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT(uuid) DO UPDATE SET {update_sql}"
        )
        with self._connect() as conn:
            conn.execute(sql, [data[c] for c in COLUMNS])

    def update_document(self, uuid: str, fields: dict):
        """Update selected columns for a document (used by the editable review view)."""
        editable = {k: v for k, v in fields.items() if k in COLUMNS and k != "uuid"}
        if not editable:
            return
        editable["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_sql = ", ".join(f"{c}=?" for c in editable)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE documents SET {set_sql} WHERE uuid=?",
                list(editable.values()) + [uuid],
            )

    def delete_document(self, uuid: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM documents WHERE uuid=?", (uuid,))

    # -- reads --------------------------------------------------------------
    def list_documents(self, search: str = None) -> list:
        """Return documents (as dicts) ordered by most recent, optionally filtered."""
        sql = "SELECT * FROM documents"
        params = []
        if search:
            like = f"%{search}%"
            sql += " WHERE title LIKE ? OR filename LIKE ? OR uuid LIKE ?"
            params = [like, like, like]
        sql += " ORDER BY COALESCE(updated_at, timestamp) DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_document(self, uuid: str):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE uuid=?", (uuid,)).fetchone()
            return dict(row) if row else None

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]


# ---------------------------------------------------------------------------
# Syncing file-based analysis output into the database
# ---------------------------------------------------------------------------
def _record_from_registry_row(row: dict, manager: DataAnalyzeManager) -> dict:
    """Build a documents record from one registry row + the on-disk JSON files."""
    uuid = str(row.get("UUID") or "").strip()
    record = {
        "uuid": uuid,
        "title": row.get("title"),
        "filename": row.get("filename"),
        "relative_path": str(row.get("relative_path") or ""),
        "timestamp": row.get("timestamp"),
        "time_taken": str(row.get("time_taken") or ""),
        "status_sectioning": str(row.get("sectioning") or ""),
        "status_references": str(row.get("references") or ""),
        "status_abstract": str(row.get("abstract") or ""),
        "status_introduction": str(row.get("Introduction") or ""),
        "source_folder": str(manager.folder),
    }

    # Abstract JSON -> section columns + abstract text.
    abstract_path = os.path.join(manager.AD_Abstract, f"{uuid}_Abstract.json")
    abstract_data, _ = _read_json(abstract_path)
    if abstract_data:
        for spec in ALR_SECTIONS:
            value = abstract_data.get(spec.key)
            col = SECTION_COLUMNS[spec.key]
            if spec.key in LIST_SECTIONS:
                record[col] = json.dumps(value) if value is not None else None
            else:
                record[col] = value if value is None else str(value)
        record["abstract_text"] = abstract_data.get(ABSTRACT_TEXT_KEY)

    # Intro / references stored as raw JSON payloads.
    _, intro_raw = _read_json(os.path.join(manager.AD_Intro, f"{uuid}_Intro.json"))
    record["introduction_json"] = intro_raw
    _, refs_raw = _read_json(os.path.join(manager.references_subfolder, f"{uuid}_References.json"))
    record["references_json"] = refs_raw

    return record


def sync_storage_to_sql(manager_or_folder, db_path=DB_PATH) -> int:
    """
    Import every processed document from a DataAnalyzeManager storage folder into
    the SQLite store. Accepts either a DataAnalyzeManager or a folder path.
    Returns the number of documents synced.
    """
    import pandas as pd  # light dependency, already required

    if isinstance(manager_or_folder, DataAnalyzeManager):
        manager = manager_or_folder
    else:
        manager = DataAnalyzeManager(manager_or_folder)

    registry = manager.excel_success
    if not os.path.exists(registry):
        print(f"No processed-file registry found at {registry}; nothing to sync.")
        return 0

    try:
        df = pd.read_excel(registry)
    except Exception as e:
        print(f"Could not read registry {registry}: {e}")
        return 0

    store = AnalyzedDataStore(db_path)
    synced = 0
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        if not str(row_dict.get("UUID") or "").strip():
            continue
        store.upsert_document(_record_from_registry_row(row_dict, manager))
        synced += 1

    print(f"Synced {synced} document(s) into {store.db_path}")
    return synced
