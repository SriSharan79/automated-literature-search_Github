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

# Enrichment columns populated by DOI/metadata extraction, publication
# classification, abstract classification, data evaluation, and download-log
# bibliographic data. These are populated *after* the plain registry sync, so
# they use COALESCE-preserve on re-sync (see upsert_document) to avoid being wiped.
ENRICHMENT_COLUMNS = [
    "doi_link", "publisher", "container", "publication_year",
    "authors", "first_author", "publication_type", "classification",
    "abstract_classification", "evaluation_json", "evaluation_score",
    "intro_evaluation_json", "intro_evaluation_score", "metrics_json",
    "link",
]

# Full column order for the documents table.
COLUMNS = (
    ["uuid", "title", "filename", "relative_path", "timestamp", "time_taken",
     "status_sectioning", "status_references", "status_abstract", "status_introduction"]
    + list(SECTION_COLUMNS.values())
    + ["abstract_text", "introduction_json", "references_json"]
    + ENRICHMENT_COLUMNS
    + ["source_folder", "created_at", "updated_at"]
)

# Snapshot of the built-in schema. User-defined topic columns (custom
# classification) may not collide with these; anything found in a database
# beyond this set is adopted as a custom enrichment column.
_BASE_COLUMNS = frozenset(COLUMNS)

# Columns deliberately dropped from the schema. Old database files still
# carry them (migrations only ever ADD columns), but they must not be
# re-adopted as custom columns.
_LEGACY_COLUMNS = {"pub_name"}


def sanitize_column_name(name: str) -> str:
    """
    Turn a user-supplied topic name into a safe SQLite column name:
    lower-cased, non-alphanumerics collapsed to underscores. Raises
    ValueError if nothing usable remains.
    """
    import re
    col = re.sub(r"[^a-z0-9_]+", "_", str(name).strip().lower()).strip("_")
    if not col:
        raise ValueError(f"'{name}' cannot be used as a column name.")
    if col[0].isdigit():
        col = "c_" + col
    return col


def register_custom_column(name: str, db_path=None) -> str:
    """
    Register a user-defined enrichment column (e.g. a custom classification
    topic) and make sure it exists in the database. Returns the sanitized
    column name. The column behaves like ``classification`` /
    ``abstract_classification``: preserved on re-sync (COALESCE), editable,
    exportable. Raises ValueError if the name collides with a built-in column.
    """
    col = sanitize_column_name(name)
    if col in _BASE_COLUMNS:
        raise ValueError(
            f"'{col}' is a built-in database column; choose a different topic name.")
    if col not in ENRICHMENT_COLUMNS:
        ENRICHMENT_COLUMNS.append(col)
    if col not in COLUMNS:
        COLUMNS.append(col)
    # init_db migrates the table (ALTER TABLE ADD COLUMN for anything missing).
    AnalyzedDataStore(db_path or DB_PATH)
    return col


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
            # Lightweight migration: add any columns missing from an older DB.
            existing = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
            for col in COLUMNS:
                if col not in existing:
                    conn.execute(f"ALTER TABLE documents ADD COLUMN {col} TEXT")
            # Reverse direction: adopt user-defined topic columns created by a
            # previous session (custom classification) so they stay visible,
            # COALESCE-preserved and exportable after a restart.
            for col in existing:
                if col not in COLUMNS and col not in _LEGACY_COLUMNS:
                    ENRICHMENT_COLUMNS.append(col)
                    COLUMNS.append(col)
            # Saved overview definitions (field/filter/grouping/chart specs).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS overview_templates ("
                "name TEXT PRIMARY KEY, spec_json TEXT, created_at TEXT)"
            )

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
        # On conflict, update everything except uuid and created_at. Enrichment
        # columns are preserved when the incoming record does not supply them
        # (e.g. a plain re-sync), so DOI/classification/biblio data is not wiped.
        update_cols = [c for c in COLUMNS if c not in ("uuid", "created_at")]
        update_sql = ", ".join(
            f"{c}=COALESCE(excluded.{c}, {c})" if c in ENRICHMENT_COLUMNS else f"{c}=excluded.{c}"
            for c in update_cols
        )
        sql = (
            f"INSERT INTO documents ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT(uuid) DO UPDATE SET {update_sql}"
        )
        try:
            with self._connect() as conn:
                conn.execute(sql, [data[c] for c in COLUMNS])
        except sqlite3.OperationalError as e:
            # A custom column registered after this store's init (e.g. adopted
            # from another database in the same process) may be missing here:
            # migrate and retry once.
            if "no column" not in str(e).lower():
                raise
            self.init_db()
            with self._connect() as conn:
                conn.execute(sql, [data[c] for c in COLUMNS])

    def update_document(self, uuid: str, fields: dict):
        """Update selected columns for a document (used by the editable review view)."""
        editable = {k: v for k, v in fields.items() if k in COLUMNS and k != "uuid"}
        if not editable:
            return
        editable["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_sql = ", ".join(f"{c}=?" for c in editable)
        try:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE documents SET {set_sql} WHERE uuid=?",
                    list(editable.values()) + [uuid],
                )
        except sqlite3.OperationalError as e:
            if "no column" not in str(e).lower():
                raise
            self.init_db()  # late-registered custom column: migrate and retry
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

    # -- overviews ----------------------------------------------------------
    @staticmethod
    def available_fields() -> list:
        """All selectable columns for a custom overview (uuid always available)."""
        return list(COLUMNS)

    def list_source_folders(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source_folder FROM documents "
                "WHERE source_folder IS NOT NULL ORDER BY source_folder"
            ).fetchall()
        return [r[0] for r in rows]

    @staticmethod
    def _filter_clause(filters: dict):
        """Build a WHERE clause (without the WHERE keyword) + params from filters."""
        where, params = [], []
        for col in ("source_folder", "publication_year", "publication_type"):
            val = (filters or {}).get(col)
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        if (filters or {}).get("research_area"):
            where.append("LOWER(research_areas) LIKE ?")
            params.append(f"%{filters['research_area'].lower()}%")
        # Classification topic: substring match across either classification column
        # (title-based or abstract-based tags), both comma-joined topic lists.
        if (filters or {}).get("topic"):
            like = f"%{filters['topic'].lower()}%"
            where.append("(LOWER(classification) LIKE ? OR LOWER(abstract_classification) LIKE ?)")
            params.extend([like, like])
        return (" AND ".join(where), params)

    def topic_counts(self, column: str = "classification", filters: dict = None) -> list:
        """
        Count documents per *individual* classification topic by splitting the
        comma-joined ``classification`` / ``abstract_classification`` column.

        Unlike :meth:`grouped_overview` (which groups by the whole tag string),
        this yields one row per topic. Returns ``[{"topic": t, "count": n}, …]``
        ordered by count desc, honouring the same filters as the other overviews.
        """
        if column not in ("classification", "abstract_classification"):
            raise ValueError(f"topic_counts only supports classification columns, not {column!r}")
        from collections import Counter

        where, params = self._filter_clause(filters or {})
        sql = f"SELECT {column} AS val FROM documents"
        if where:
            sql += " WHERE " + where
        counter = Counter()
        with self._connect() as conn:
            for row in conn.execute(sql, params).fetchall():
                val = row[0]
                if not val:
                    continue
                for topic in str(val).split(","):
                    t = topic.strip()
                    if t:
                        counter[t] += 1
        return [{"topic": t, "count": c} for t, c in counter.most_common()]

    def build_overview(self, fields: list = None, filters: dict = None) -> list:
        """
        Return a custom overview: the chosen ``fields`` (defaults to all),
        filtered by an optional ``filters`` dict with any of:
          source_folder (exact), publication_year (exact),
          publication_type (exact), research_area (substring, case-insensitive).
        """
        fields = [f for f in (fields or COLUMNS) if f in COLUMNS] or ["uuid"]
        where, params = self._filter_clause(filters or {})
        sql = f"SELECT {', '.join(fields)} FROM documents"
        if where:
            sql += " WHERE " + where
        sql += " ORDER BY COALESCE(updated_at, timestamp) DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def grouped_overview(self, group_by: str, filters: dict = None) -> list:
        """
        Aggregated overview: count of documents grouped by ``group_by`` (a column),
        honouring the same filters as build_overview. Returns
        [{group_by: value, "count": n}, …] ordered by count desc.
        """
        if group_by not in COLUMNS:
            raise ValueError(f"Invalid group-by column: {group_by}")
        where, params = self._filter_clause(filters or {})
        sql = (f"SELECT COALESCE(NULLIF({group_by}, ''), '(none)') AS {group_by}, "
               f"COUNT(*) AS count FROM documents")
        if where:
            sql += " WHERE " + where
        sql += " GROUP BY 1 ORDER BY count DESC, 1 ASC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def stats(self) -> dict:
        """High-level cross-space statistics over the whole database."""
        def _nonempty(col):
            return f"SELECT COUNT(*) FROM documents WHERE {col} IS NOT NULL AND {col} <> ''"
        with self._connect() as conn:
            def one(q):
                return conn.execute(q).fetchone()[0]
            per_space = [dict(r) for r in conn.execute(
                "SELECT COALESCE(NULLIF(source_folder, ''), '(unknown)') AS source_folder, "
                "COUNT(*) AS count FROM documents GROUP BY 1 ORDER BY count DESC"
            ).fetchall()]
            return {
                "total": one("SELECT COUNT(*) FROM documents"),
                "with_abstract": one(_nonempty("abstract_text")),
                "with_doi": one(_nonempty("doi_link")),
                "with_classification": one(_nonempty("classification")),
                "with_abstract_classification": one(_nonempty("abstract_classification")),
                "with_evaluation": one(_nonempty("evaluation_score")),
                "distinct_years": one(_nonempty("publication_year").replace("COUNT(*)", "COUNT(DISTINCT publication_year)")),
                "distinct_types": one(_nonempty("publication_type").replace("COUNT(*)", "COUNT(DISTINCT publication_type)")),
                "per_space": per_space,
            }

    # -- safe ad-hoc query --------------------------------------------------
    def run_select(self, sql: str, max_rows: int = 5000):
        """
        Run a single read-only SELECT/WITH query and return (columns, rows).
        Rejects anything that is not a lone SELECT, and runs with query_only so
        no write can occur even if validation is bypassed.
        """
        stmt = (sql or "").strip().rstrip(";").strip()
        if not stmt:
            raise ValueError("Empty query.")
        low = stmt.lower()
        if not (low.startswith("select") or low.startswith("with")):
            raise ValueError("Only SELECT queries are allowed.")
        if ";" in stmt:
            raise ValueError("Only a single statement is allowed.")
        for kw in ("attach ", "pragma", "insert ", "update ", "delete ", "drop ",
                   "alter ", "create ", "replace ", "vacuum"):
            if kw in low:
                raise ValueError(f"Disallowed keyword in query: '{kw.strip()}'")
        with self._connect() as conn:
            conn.execute("PRAGMA query_only = ON")
            cur = conn.execute(stmt)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(r) for r in cur.fetchmany(max_rows)]
        return cols, rows

    # -- saved overview templates -------------------------------------------
    def save_template(self, name: str, spec: dict):
        name = (name or "").strip()
        if not name:
            raise ValueError("Template name is required.")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO overview_templates (name, spec_json, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET spec_json=excluded.spec_json",
                (name, json.dumps(spec), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )

    def list_templates(self) -> list:
        with self._connect() as conn:
            return [r[0] for r in conn.execute(
                "SELECT name FROM overview_templates ORDER BY name").fetchall()]

    def get_template(self, name: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT spec_json FROM overview_templates WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else None

    def delete_template(self, name: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM overview_templates WHERE name=?", (name,))

    # -- download-log bibliographic merge -----------------------------------
    # Metadata-workbook column -> documents column. Covers the managed
    # ``*_DOI_Metadata.xlsx`` layout (doi_metadata.DOI_EXCEL_COLUMNS), the
    # ``publications_metadata.xlsx`` exports and download-log spellings.
    METADATA_TO_DB = {
        "DOI_Link": "doi_link",
        "DOI": "doi_link",
        "Publisher": "publisher",
        "Container": "container",
        "Publication Year": "publication_year",
        "Year": "publication_year",
        "DOI_Authors": "authors",
        "Authors": "authors",
        "DOI_First_Author": "first_author",
        "First_Author": "first_author",
        "First Author": "first_author",
        "Publication Type": "publication_type",
        "Link": "link",
    }

    @staticmethod
    def _real_value(val) -> str | None:
        """A usable metadata cell value, or None for empty/placeholder cells."""
        if val is None:
            return None
        text = str(val).strip()
        if not text or text.lower() in ("nan", "n/a", "none", "not found"):
            return None
        return text

    def merge_metadata_workbook(self, df) -> int:
        """
        Merge DOI/publication metadata from a workbook DataFrame into existing
        documents — same fill-if-empty semantics as :meth:`merge_download_log`,
        but covering the ``*_DOI_Metadata.xlsx`` / ``publications_metadata.xlsx``
        column layouts (see ``METADATA_TO_DB``). Rows are matched by ``UUID``
        when the workbook has one, else by ``File_Name`` -> ``filename``.
        Returns the number of rows updated.
        """
        has_uuid = "UUID" in df.columns
        updated = 0
        with self._connect() as conn:
            for _, row in df.iterrows():
                doc = None
                if has_uuid:
                    uid = self._real_value(row.get("UUID"))
                    if uid:
                        doc = conn.execute(
                            "SELECT uuid FROM documents WHERE uuid = ?", (uid,)).fetchone()
                if doc is None:
                    fname = self._real_value(row.get("File_Name"))
                    if not fname:
                        continue
                    doc = conn.execute(
                        "SELECT uuid FROM documents WHERE filename = ?", (fname,)).fetchone()
                if not doc:
                    continue
                sets, params = [], []
                for wb_col, db_col in self.METADATA_TO_DB.items():
                    val = self._real_value(row.get(wb_col)) if wb_col in df.columns else None
                    if val is not None:
                        if db_col == "publication_year":
                            val = val.split(".")[0]  # 2024.0 -> 2024
                        sets.append(f"{db_col} = COALESCE(NULLIF({db_col}, ''), ?)")
                        params.append(val)
                if sets:
                    params.append(doc[0])
                    conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE uuid = ?", params)
                    updated += 1
        return updated

    def merge_download_log(self, df) -> int:
        """
        Merge bibliographic data from a download-log DataFrame into existing
        documents, matching the log's ``File_Name`` to a document ``filename``.
        Fills link/authors/publication_year/first_author (only where the
        document currently has no value). Returns the number of rows updated.
        """
        # log column -> document column
        mapping = {
            "Link": "link",
            "Authors": "authors",
            "Publication Year": "publication_year",
            "First_Author": "first_author",
        }
        updated = 0
        with self._connect() as conn:
            for _, row in df.iterrows():
                fname = row.get("File_Name")
                if not fname or (isinstance(fname, float)):
                    continue
                doc = conn.execute(
                    "SELECT uuid FROM documents WHERE filename = ?", (str(fname),)
                ).fetchone()
                if not doc:
                    continue
                sets, params = [], []
                for log_col, db_col in mapping.items():
                    val = row.get(log_col)
                    if val is not None and str(val).strip() and str(val) != "nan":
                        sets.append(f"{db_col} = COALESCE(NULLIF({db_col}, ''), ?)")
                        params.append(str(val))
                if sets:
                    params.append(doc[0])
                    conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE uuid = ?", params)
                    updated += 1
        return updated


# ---------------------------------------------------------------------------
# Syncing file-based analysis output into the database
# ---------------------------------------------------------------------------
def _record_from_registry_row(row: dict, manager: DataAnalyzeManager,enrichment_data: dict = None) -> dict:
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
    
    # Append any pre-loaded enrichment data (DOI, Evaluation, Classification)
    if enrichment_data and uuid in enrichment_data:
        for col, value in enrichment_data[uuid].items():
            if col in ENRICHMENT_COLUMNS:
                record[col] = value

    return record


def sync_storage_to_sql(manager_or_folder, db_path=DB_PATH,progress_callback=None) -> int:
    import pandas as pd
    import glob
    import os

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
    total_docs = len(df) # Get total number of documents to sync

    for index, row in df.iterrows():
        row_dict = row.to_dict()
        uuid = str(row_dict.get("UUID") or "").strip()

        if not uuid:
            continue

        store.upsert_document(_record_from_registry_row(row_dict, manager))
        synced += 1

        if progress_callback:
            progress_callback(synced, total_docs, uuid)

    # After the rows exist, pull DOI/publication metadata and evaluation
    # summaries recorded in the space's workbooks into SQL (fill-if-empty,
    # so values already in the database are never overwritten). This is what
    # populates enrichment when a space is linked into a fresh database.
    _merge_space_metadata_files(store, manager)
    _merge_space_evaluation_overviews(store, manager)

    return synced


def _merge_space_metadata_files(store, manager) -> int:
    """
    Merge every ``*_DOI_Metadata.xlsx`` (managed DOI workbook folder) and
    ``*publications_metadata.xlsx`` (anywhere in the space) into the store —
    newest file first, fill-if-empty — so the freshest recorded metadata wins
    but nothing already in SQL is overwritten. Returns rows updated.
    """
    import glob
    import pandas as pd

    files = glob.glob(os.path.join(manager.doi_metadata_subfolder, "*_DOI_Metadata.xlsx"))
    files += glob.glob(os.path.join(manager.folder, "**", "*publications_metadata.xlsx"),
                       recursive=True)
    updated = 0
    for path in sorted(set(files), key=os.path.getmtime, reverse=True):
        try:
            updated += store.merge_metadata_workbook(pd.read_excel(path))
        except Exception as e:
            print(f"Could not merge metadata file {path}: {e}")
    if updated:
        print(f"Merged DOI/publication metadata from {len(files)} workbook(s): "
              f"{updated} row update(s).")
    return updated


def _merge_space_evaluation_overviews(store, manager) -> int:
    """
    Rebuild evaluation summaries from the newest evaluation-overview workbook
    (``{space}/Abstract_DB/Abstract_Overview_folder/*_Abstract_Eval_Overview.xlsx``
    and the Introduction counterpart) and store them fill-if-empty into the
    ``evaluation_json``/``evaluation_score`` (resp. ``intro_*``) columns. The
    Overview sheet carries per-section ``{Section}_True_Count``/``_False_Count``
    columns; the score is recomputed the same way data_evaluator does
    (true / (true+false) * 100, one decimal). Returns rows updated.
    """
    import glob
    import pandas as pd

    targets = [
        (os.path.join(manager.folder, "Abstract_DB", "Abstract_Overview_folder",
                      "*_Abstract_Eval_Overview.xlsx"),
         "evaluation_json", "evaluation_score"),
        (os.path.join(manager.folder, "Introduction_DB",
                      "*_Introduction_Eval_Overview.xlsx"),
         "intro_evaluation_json", "intro_evaluation_score"),
    ]
    updated = 0
    with store._connect() as conn:
        for pattern, json_col, score_col in targets:
            files = glob.glob(pattern)
            if not files:
                continue
            latest = max(files, key=os.path.getmtime)
            try:
                ov = pd.read_excel(latest, sheet_name="Overview")
            except Exception as e:
                print(f"Could not read evaluation overview {latest}: {e}")
                continue
            sections = [c[: -len("_True_Count")] for c in ov.columns if c.endswith("_True_Count")]
            for _, row in ov.iterrows():
                uuid = str(row.get("UUID") or "").strip()
                if not uuid or uuid == "nan":
                    continue
                stats = {}
                for sec in sections:
                    t, f = row.get(f"{sec}_True_Count"), row.get(f"{sec}_False_Count")
                    if pd.notna(t) or pd.notna(f):
                        stats[sec] = {"true": int(t or 0), "false": int(f or 0)}
                if not stats:
                    continue
                total_true = sum(v["true"] for v in stats.values())
                total = total_true + sum(v["false"] for v in stats.values())
                percent = round(100.0 * total_true / total, 1) if total else None
                cur = conn.execute(
                    f"UPDATE documents SET "
                    f"{json_col} = COALESCE(NULLIF({json_col}, ''), ?), "
                    f"{score_col} = COALESCE(NULLIF({score_col}, ''), ?) "
                    f"WHERE uuid = ?",
                    (json.dumps(stats), "" if percent is None else str(percent), uuid))
                updated += cur.rowcount if cur.rowcount > 0 else 0
    if updated:
        print(f"Merged evaluation summaries from overview workbook(s): {updated} row update(s).")
    return updated


def sync_one_document(manager_or_folder, filename, db_path=DB_PATH) -> bool:
    """
    Sync a single document (matched by ``filename``) from a storage space's
    registry into the SQLite store. Used by the per-document batch pipeline to
    copy analysis results into SQL immediately after a file is processed --
    without re-reading/re-upserting the whole registry each iteration.

    Returns True if a matching registry row was found and upserted. Enrichment
    columns already in SQL are preserved (COALESCE) because the registry-only
    record does not supply them.
    """
    import pandas as pd

    if isinstance(manager_or_folder, DataAnalyzeManager):
        manager = manager_or_folder
    else:
        manager = DataAnalyzeManager(manager_or_folder)

    registry = manager.excel_success
    if not os.path.exists(registry):
        return False

    try:
        df = pd.read_excel(registry)
    except Exception as e:
        print(f"Could not read registry {registry}: {e}")
        return False

    if "filename" not in df.columns:
        return False

    target = str(filename).strip()
    match = df[df["filename"].astype(str).str.strip() == target]
    if match.empty:
        return False

    store = AnalyzedDataStore(db_path)
    # Newest matching row wins if a filename somehow appears more than once.
    row_dict = match.iloc[-1].to_dict()
    if not str(row_dict.get("UUID") or "").strip():
        return False
    store.upsert_document(_record_from_registry_row(row_dict, manager))
    return True
