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
from alr.common.sections import (
    ALR_SECTIONS, INTRO_RAG_SECTIONS, INTRO_TEXT_KEY, RESCON_RAG_SECTIONS,
    RESCON_TEXT_KEY,
)

# Constant, app-wide database location.
DB_PATH = Path(ALR_main_folder) / "alr_analyzed_data.db"


def _slug(section_key: str) -> str:
    """Turn a section key ("Research Problem") into a column name ("research_problem")."""
    return section_key.strip().lower().replace(" ", "_")


# Section key -> column name, derived from the canonical registry.
SECTION_COLUMNS = {spec.key: _slug(spec.key) for spec in ALR_SECTIONS}


def _prefixed_slug(prefix: str, section_key: str) -> str:
    """Column name for a non-abstract attribute ("Gaps & Limitations" ->
    "intro_gaps_limitations"). Prefixed so intro/rescon keys can never collide
    with an abstract section column, and sanitized because these keys contain
    characters ('&') that are not valid in a column name."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "_", str(section_key).strip().lower()).strip("_")
    return f"{prefix}_{slug}"


# The analyzed Introduction / Results & Conclusion attributes get their own
# columns, filled from {uuid}_Intro.json / {uuid}_Results_Conclusion.json the
# same way the abstract attributes are filled from {uuid}_Abstract.json.
INTRO_SECTION_COLUMNS = {spec.key: _prefixed_slug("intro", spec.key)
                         for spec in INTRO_RAG_SECTIONS}
RESCON_SECTION_COLUMNS = {spec.key: _prefixed_slug("rescon", spec.key)
                          for spec in RESCON_RAG_SECTIONS}
# Every analyzed attribute column, whatever JSON it comes from.
ALL_SECTION_COLUMNS = {**SECTION_COLUMNS, **INTRO_SECTION_COLUMNS, **RESCON_SECTION_COLUMNS}

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
    "intro_evaluation_json", "intro_evaluation_score",
    "rescon_evaluation_json", "rescon_evaluation_score", "metrics_json",
    "link",
]

# Analyzed Introduction / Results & Conclusion columns are filled from their
# analysis JSONs, which do not always live in the space being synced (a common
# DB holds the DB files but no analysis folders). So a sync that finds no such
# JSON must leave what is already stored alone instead of clearing it — these
# columns are written when there IS data and preserved when there is not.
_PRESERVE_IF_NULL = frozenset(
    list(INTRO_SECTION_COLUMNS.values()) + list(RESCON_SECTION_COLUMNS.values())
    + ["introduction_text", "rescon_text"]
)

# Full column order for the documents table.
COLUMNS = (
    ["uuid", "title", "filename", "relative_path", "timestamp", "time_taken",
     "status_sectioning", "status_references", "status_abstract", "status_introduction"]
    + list(SECTION_COLUMNS.values())
    + ["abstract_text"]
    + list(INTRO_SECTION_COLUMNS.values()) + ["introduction_text"]
    + list(RESCON_SECTION_COLUMNS.values()) + ["rescon_text"]
    + ["introduction_json", "references_json"]
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


def _truthy_classification(val) -> bool:
    """
    Interpret one classification cell as a boolean. Classification workbooks
    store per-topic True/False, but after a round-trip through Excel/pandas a
    cell may come back as a numpy bool, ``1``/``0``, or a ``"True"``/``"False"``
    string (blank/NaN for unscored). Treat only affirmative values as true.
    """
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    text = str(val).strip().lower()
    return text in ("true", "1", "1.0", "yes", "y", "t")


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
        preserve = set(ENRICHMENT_COLUMNS) | _PRESERVE_IF_NULL
        update_sql = ", ".join(
            f"{c}=COALESCE(excluded.{c}, {c})" if c in preserve else f"{c}=excluded.{c}"
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

    def find_document(self, filename, source_folder=None):
        """
        Return the newest document row for ``filename`` (optionally restricted to
        one storage space), or None.

        The per-document pipeline needs this after every file; doing it with
        ``list_documents()`` pulls the whole table across and filters in Python,
        which grows with the database instead of with the run.
        """
        sql = "SELECT * FROM documents WHERE filename=?"
        params = [str(filename)]
        if source_folder is not None:
            sql += " AND source_folder=?"
            params.append(str(source_folder))
        sql += " ORDER BY COALESCE(updated_at, timestamp) DESC LIMIT 1"
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
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
def describe_record_data(record: dict) -> str:
    """
    Which analyzed data one record actually carries, as a short human phrase
    ("abstract + introduction + results & conclusion attributes"). Used for the
    link progress text so the user sees WHAT of a space is going into the
    database, not just a counter.
    """
    def any_filled(columns):
        return any(str(record.get(c) or "").strip() for c in columns)

    parts = []
    if any_filled(SECTION_COLUMNS.values()):
        parts.append("abstract")
    if any_filled(INTRO_SECTION_COLUMNS.values()):
        parts.append("introduction")
    if any_filled(RESCON_SECTION_COLUMNS.values()):
        parts.append("results & conclusion")
    extras = []
    if str(record.get("references_json") or "").strip():
        extras.append("references")
    if parts:
        text = " + ".join(parts) + " attributes"
        return f"{text} (+ {', '.join(extras)})" if extras else text
    return ", ".join(extras) if extras else "registry entry only (no analyzed data found)"


def _apply_section_values(record: dict, data: dict, columns: dict) -> None:
    """Copy one analysis JSON's attribute values into their record columns.

    List-valued attributes are stored as a JSON string, everything else as
    text — the same encoding the abstract sections use, so readers (the RAG
    builders, the Review app, exports) can treat every attribute alike. A
    missing/unreadable JSON leaves the columns untouched, so a document whose
    Introduction has not been analyzed keeps whatever is already stored.
    """
    if not data:
        return
    for key, col in columns.items():
        value = data.get(key)
        if value is None:
            record[col] = None
        elif isinstance(value, (list, tuple)):
            record[col] = json.dumps(list(value))
        else:
            record[col] = str(value)


# Optional search root used to locate analysis JSONs that are not in the space
# being synced (a common/combined DB holds the built databases but no analysis
# folders). The UI sets this from its remembered "Enrichment JSON search root"
# so every sync — Tab 1's finalization, the Tab 5 passes, per-document syncs —
# picks it up without threading the argument through every call site.
_DEFAULT_SEARCH_ROOT = None


def set_default_search_root(path) -> None:
    """Set (or clear, with None/'') the fallback root for analysis JSONs."""
    global _DEFAULT_SEARCH_ROOT
    _DEFAULT_SEARCH_ROOT = str(path).strip() or None if path else None


def get_default_search_root():
    return _DEFAULT_SEARCH_ROOT


def _analysis_json_names(uuid: str) -> dict:
    """``{source: filename}`` of the three analysis JSONs of one document."""
    return {"abstract": f"{uuid}_Abstract.json",
            "intro": f"{uuid}_Intro.json",
            "rescon": f"{uuid}_Results_Conclusion.json"}


def _locate_missing_analysis_jsons(uuids, manager, search_root) -> dict:
    """
    ``{filename: path}`` for the analysis JSONs the space itself does not hold,
    found under ``search_root``. ONE recursive pass for the whole sync, and
    nothing at all when the space is complete or no root is configured.
    """
    root = search_root or _DEFAULT_SEARCH_ROOT
    if not root:
        return {}
    wanted = set()
    for uuid in uuids:
        names = _analysis_json_names(uuid)
        for folder, name in ((manager.AD_Abstract, names["abstract"]),
                             (manager.AD_Intro, names["intro"]),
                             (manager.AD_ResCon, names["rescon"])):
            if not os.path.exists(os.path.join(folder, name)):
                wanted.add(name)
    if not wanted:
        return {}
    from alr.common.file_handlers import index_jsons_under_root
    found = index_jsons_under_root(wanted, root)
    if found:
        print(f"Located {len(found)} analysis JSON(s) missing from the space under {root}.")
    return found


def _analysis_json_path(manager, folder, uuid, source, located) -> str:
    """The document's analysis JSON in the space, else the located fallback."""
    name = _analysis_json_names(uuid)[source]
    in_space = os.path.join(folder, name)
    if os.path.exists(in_space):
        return in_space
    return str(located.get(name, in_space))


def _record_from_registry_row(row: dict, manager: DataAnalyzeManager,
                              enrichment_data: dict = None,
                              located_jsons: dict = None) -> dict:
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

    located = located_jsons or {}

    # Abstract JSON -> section columns + abstract text.
    abstract_path = _analysis_json_path(manager, manager.AD_Abstract, uuid, "abstract", located)
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

    # Introduction analysis JSON -> its own attribute columns + identified text
    # (mirrors the abstract mapping above; query_executor's enrichment reads the
    # very same keys out of the very same file).
    intro_path = _analysis_json_path(manager, manager.AD_Intro, uuid, "intro", located)
    intro_data, intro_raw = _read_json(intro_path)
    _apply_section_values(record, intro_data, INTRO_SECTION_COLUMNS)
    if intro_data:
        record["introduction_text"] = intro_data.get(INTRO_TEXT_KEY)

    # Results & Conclusion analysis JSON -> its own attribute columns + text.
    rescon_data, _ = _read_json(
        _analysis_json_path(manager, manager.AD_ResCon, uuid, "rescon", located))
    _apply_section_values(record, rescon_data, RESCON_SECTION_COLUMNS)
    if rescon_data:
        record["rescon_text"] = rescon_data.get(RESCON_TEXT_KEY)

    # Intro / references also kept as raw JSON payloads.
    record["introduction_json"] = intro_raw
    _, refs_raw = _read_json(os.path.join(manager.references_subfolder, f"{uuid}_References.json"))
    record["references_json"] = refs_raw
    
    # Append any pre-loaded enrichment data (DOI, Evaluation, Classification)
    if enrichment_data and uuid in enrichment_data:
        for col, value in enrichment_data[uuid].items():
            if col in ENRICHMENT_COLUMNS:
                record[col] = value

    return record


def sync_storage_to_sql(manager_or_folder, db_path=DB_PATH, progress_callback=None,
                        search_root=None) -> int:
    """
    Sync a storage space's analysis output into the database.

    ``search_root``: optional folder searched (recursively, once per sync) for
    the analysis JSONs the space itself does not hold — the same fallback the
    query enrichment uses, so a common/combined DB or a space that was moved
    away from its analysis files still fills its attribute columns. Defaults to
    :func:`set_default_search_root`'s value; without either, only the space's
    own files are read.
    """
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
        from alr.common.excel_utils import read_excel_cached
        df = read_excel_cached(registry)
    except Exception as e:
        print(f"Could not read registry {registry}: {e}")
        return 0

    store = AnalyzedDataStore(db_path)
    synced = 0
    total_docs = len(df) # Get total number of documents to sync

    # One recursive pass for every analysis JSON this space is missing (no-op
    # when the space is complete or no search root is configured).
    uuids = [str(r.get("UUID") or "").strip() for _, r in df.iterrows()]
    located = _locate_missing_analysis_jsons([u for u in uuids if u], manager, search_root)

    def tick(done, total, text):
        if progress_callback:
            progress_callback(done, total, text)

    for index, row in df.iterrows():
        row_dict = row.to_dict()
        uuid = str(row_dict.get("UUID") or "").strip()

        if not uuid:
            continue

        record = _record_from_registry_row(row_dict, manager, located_jsons=located)
        store.upsert_document(record)
        synced += 1

        # Say WHAT is being linked, not just how far along we are: the document
        # and which of its analyzed data actually went into the row.
        label = str(record.get("filename") or uuid)
        tick(synced, total_docs, f"{label} — {describe_record_data(record)}")

    # After the rows exist, pull DOI/publication metadata and evaluation
    # summaries recorded in the space's workbooks into SQL (fill-if-empty,
    # so values already in the database are never overwritten). This is what
    # populates enrichment when a space is linked into a fresh database.
    tick(total_docs, total_docs, "Merging DOI / publication metadata workbooks…")
    _merge_space_metadata_files(store, manager)
    tick(total_docs, total_docs, "Merging evaluation overviews (abstract / introduction / results & conclusion)…")
    _merge_space_evaluation_overviews(store, manager)
    tick(total_docs, total_docs, "Merging classification workbooks…")
    _merge_space_classification_files(store, manager)

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
        (os.path.join(manager.folder, "Results_Conclusion_DB",
                      "*_Results_Conclusion_Eval_Overview.xlsx"),
         "rescon_evaluation_json", "rescon_evaluation_score"),
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


def _merge_space_classification_files(store, manager) -> int:
    """
    Merge every managed publication-classification workbook in the space's
    ``Publication_Classification_Files`` folder into SQL -- newest file per
    target column first, fill-if-empty -- so classification tags recorded on
    disk populate the database when a space is linked into a fresh DB (mirrors
    :func:`_merge_space_metadata_files` / :func:`_merge_space_evaluation_overviews`).

    Each workbook row is ``{filename, title, <topic>: bool, ...}`` (written by
    ``classify_runner._append_classification_row``) and is matched by
    ``filename`` -> ``documents.filename``. The per-document summary stored in
    SQL is the comma-joined list of topics whose cell is true, exactly as the
    classification runner writes it live.

    Three workbook families are handled, by filename:
      * ``*_Title_Classification.xlsx``    -> ``classification``
      * ``*_Abstract_Classification.xlsx`` -> ``abstract_classification``
      * ``*_{Topic}_Classification.xlsx``  -> custom column (registered on demand)

    Question-scored workbooks (``*_Question_Scored_Classification.xlsx``) are
    skipped: they are multi-sheet per-question scores, not per-document topic
    tags. Returns the number of row updates applied.
    """
    import glob
    import re
    import pandas as pd

    folder = str(manager.classification_subfolder)
    files = glob.glob(os.path.join(folder, "*_Classification.xlsx"))
    if not files:
        return 0

    def _target_column(path):
        """Resolve a workbook path to the SQL column it feeds, or None to skip."""
        name = os.path.basename(path)
        if "Question_Scored_Classification" in name:
            return None
        if name.endswith("_Title_Classification.xlsx"):
            return "classification"
        if name.endswith("_Abstract_Classification.xlsx"):
            return "abstract_classification"
        # Custom: {date}_{Topic}_Classification.xlsx -> sanitized topic column.
        m = re.match(r"^\d{4}-\d{2}-\d{2}_(.+)_Classification\.xlsx$", name)
        if not m:
            return None
        try:
            # Registers + migrates the column so a fresh DB gains it; matches the
            # column classify_custom_space records under (sanitize_column_name).
            return register_custom_column(m.group(1), db_path=store.db_path)
        except ValueError:
            return None  # collides with a built-in column; skip.

    # Resolve targets first, newest file first. register_custom_column may ALTER
    # TABLE, so it must run before the merge connection below is opened.
    resolved = []
    for path in sorted(files, key=os.path.getmtime, reverse=True):
        col = _target_column(path)
        if col:
            resolved.append((path, col))
    if not resolved:
        return 0

    updated = 0
    with store._connect() as conn:
        for path, col in resolved:
            try:
                df = pd.read_excel(path)
            except Exception as e:
                print(f"Could not read classification file {path}: {e}")
                continue
            if "filename" not in df.columns:
                continue
            topic_cols = [c for c in df.columns if c not in ("filename", "title")]
            for _, row in df.iterrows():
                fname = store._real_value(row.get("filename"))
                if not fname:
                    continue
                doc = conn.execute(
                    "SELECT uuid FROM documents WHERE filename = ?", (fname,)).fetchone()
                if not doc:
                    continue
                true_topics = [c for c in topic_cols if _truthy_classification(row.get(c))]
                summary = ", ".join(true_topics)
                if not summary:
                    continue  # no true topics for this row; nothing to record.
                cur = conn.execute(
                    f"UPDATE documents SET {col} = COALESCE(NULLIF({col}, ''), ?) "
                    f"WHERE uuid = ?",
                    (summary, doc[0]))
                updated += cur.rowcount if cur.rowcount > 0 else 0
    if updated:
        print(f"Merged publication classification from workbook(s): {updated} row update(s).")
    return updated


def sync_one_document(manager_or_folder, filename, db_path=DB_PATH, search_root=None) -> bool:
    """
    Sync a single document (matched by ``filename``) from a storage space's
    registry into the SQLite store. Used by the per-document batch pipeline to
    copy analysis results into SQL immediately after a file is processed --
    without re-reading/re-upserting the whole registry each iteration.

    Returns True if a matching registry row was found and upserted. Enrichment
    columns already in SQL are preserved (COALESCE) because the registry-only
    record does not supply them.
    """
    from alr.common.excel_utils import read_excel_cached

    if isinstance(manager_or_folder, DataAnalyzeManager):
        manager = manager_or_folder
    else:
        manager = DataAnalyzeManager(manager_or_folder)

    registry = manager.excel_success
    if not os.path.exists(registry):
        return False

    try:
        # Cached while the registry is unchanged: this runs once per document.
        df = read_excel_cached(registry)
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
    uuid = str(row_dict.get("UUID") or "").strip()
    if not uuid:
        return False
    located = _locate_missing_analysis_jsons([uuid], manager, search_root)
    store.upsert_document(
        _record_from_registry_row(row_dict, manager, located_jsons=located))
    return True