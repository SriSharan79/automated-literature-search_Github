"""
alr.common.vector_snapshot
==========================

Create and query portable ZIP packages containing:
  - snapshot.db          : SQLite snapshot filtered by storage folder(s) & columns
  - vectors.bin          : FAISS IndexFlatIP cosine index (one vector per non-empty cell)
  - vectors.bin.meta.json: Embedding backend metadata written by vector_db_updater
  - vector_entries.json  : Index-row -> (uuid, title, column, text) mapping
  - manifest.json        : Package summary (folders, columns, counts, timestamp)
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from alr.common.sql_store import AnalyzedDataStore, COLUMNS, DB_PATH
from alr.rag_builders.vector_db_updater import (
    create_faiss_index_cosine,
    read_index_metadata,
    save_index_file,
    search_similar,
    vectorize_strings,
)


def _cell_to_text(value) -> str:
    """Convert a SQLite cell value (including JSON list strings) to clean text."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in ("none", "nan"):
        return ""
    if text.startswith("[") and text.endswith("]"):
        try:
            items = json.loads(text)
            if isinstance(items, list):
                return "; ".join(str(x).strip() for x in items if str(x).strip())
        except json.JSONDecodeError:
            pass
    return text


def build_vector_snapshot_zip(
    source_folders: list[str],
    snapshot_columns: list[str],
    vector_columns: list[str],
    zip_path: str | Path,
    db_path=DB_PATH,
    method: str = None,
    service: str = None,
    model: str = None,
    progress_callback=None,
    should_cancel=None,
) -> dict:
    """
    Build a filtered SQLite snapshot + per-cell FAISS vector DB and pack them
    into ``zip_path``. Returns the manifest dict.
    """
    if not source_folders:
        raise ValueError("Select at least one storage folder.")

    valid_cols = set(COLUMNS)
    snap_cols = ["uuid"] + [c for c in snapshot_columns if c in valid_cols and c != "uuid"]
    vec_cols = [c for c in vector_columns if c in valid_cols]
    if not vec_cols:
        raise ValueError("Select at least one column to vectorize.")

    # Ensure every vectorized column is also present in the snapshot.
    for c in vec_cols:
        if c not in snap_cols:
            snap_cols.append(c)

    def _tick(text, done=None, total=None):
        if progress_callback:
            progress_callback(done=done, total=total, text=text)

    _tick("Reading selected documents from SQLite…")
    store = AnalyzedDataStore(db_path)
    placeholders = ", ".join("?" for _ in source_folders)
    # Fetch title alongside snapshot columns so search results can display it
    # even if the user did not include 'title' in snapshot_columns.
    query_cols = list(snap_cols) if "title" in snap_cols else snap_cols + ["title"]
    col_sql = ", ".join(f'"{c}"' for c in query_cols)

    with store._connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                f"SELECT {col_sql} FROM documents WHERE source_folder IN ({placeholders})",
                list(source_folders),
            ).fetchall()
        ]

    if not rows:
        raise ValueError("No documents found for the selected storage folder(s).")
    if should_cancel and should_cancel():
        raise InterruptedError("Cancelled by user.")

    # Build per-cell (uuid, column) texts for vectorization.
    entries = []
    texts = []
    for row in rows:
        uid = str(row.get("uuid") or "")
        title = str(row.get("title") or "")
        for col in vec_cols:
            text = _cell_to_text(row.get(col))
            if not text:
                continue
            entries.append({
                "id": len(entries),
                "uuid": uid,
                "title": title,
                "column": col,
                "text": text,
            })
            texts.append(text)

    if not texts:
        raise ValueError("No non-empty text found in the selected columns to vectorize.")

    zip_path = Path(zip_path)
    if zip_path.suffix.lower() != ".zip":
        zip_path = zip_path.with_suffix(".zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # (a) Standalone SQLite snapshot
        _tick(f"Writing SQLite snapshot ({len(rows)} document(s))…")
        snap_db_path = tmp_dir / "snapshot.db"
        snap_cols_def = ", ".join(
            '"uuid" TEXT PRIMARY KEY' if c == "uuid" else f'"{c}" TEXT' for c in snap_cols
        )
        snap_col_names = ", ".join(f'"{c}"' for c in snap_cols)
        snap_ph = ", ".join("?" for _ in snap_cols)

        snap_conn = sqlite3.connect(str(snap_db_path))
        try:
            snap_conn.execute(f"CREATE TABLE documents ({snap_cols_def})")
            snap_conn.executemany(
                f"INSERT INTO documents ({snap_col_names}) VALUES ({snap_ph})",
                [[row.get(c) for c in snap_cols] for row in rows],
            )
            snap_conn.commit()
        finally:
            snap_conn.close()

        if should_cancel and should_cancel():
            raise InterruptedError("Cancelled by user.")

        # (b) Per-cell vectorization + FAISS index
        clean_method = (method or "").strip().lower() or None
        clean_service = (service or "").strip() or None if clean_method != "local" else None
        clean_model = (model or "").strip() or None if clean_method != "local" else None

        _tick(f"Vectorizing {len(texts)} cell(s) across {len(vec_cols)} column(s)…")
        vectors = vectorize_strings(
            texts,
            method=clean_method,
            service=clean_service,
            model=clean_model,
        )
        index = create_faiss_index_cosine(vectors)

        index_path = tmp_dir / "vectors.bin"
        save_index_file(
            index,
            index_path,
            method=clean_method,
            service=clean_service,
            model=clean_model,
        )
        meta_path = Path(str(index_path) + ".meta.json")

        entries_path = tmp_dir / "vector_entries.json"
        entries_path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

        manifest = {
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source_folders": list(source_folders),
            "snapshot_columns": snap_cols,
            "vector_columns": vec_cols,
            "doc_count": len(rows),
            "vector_count": len(entries),
            "embedding": read_index_metadata(index_path) or {},
        }
        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Pack everything into the user-named zip file
        _tick(f"Packing zip archive '{zip_path.name}'…")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in (snap_db_path, index_path, meta_path, entries_path, manifest_path):
                if p.exists():
                    zf.write(p, arcname=p.name)

    return manifest


def read_snapshot_manifest(zip_path: str | Path) -> dict:
    """Read manifest.json from a vector snapshot zip without extracting it."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        if "manifest.json" not in zf.namelist():
            raise ValueError("Selected zip does not contain a valid manifest.json.")
        return json.loads(zf.read("manifest.json").decode("utf-8"))


def query_vector_snapshot_zip(
    zip_path: str | Path,
    query: str,
    top_k: int = 10,
) -> list[dict]:
    """
    Run cosine similarity search against a packed vector snapshot zip using the
    exact embedding method, service, and model recorded when the zip was built.
    Returns up to ``top_k`` rows: [{score, uuid, title, column, content}, ...].
    """
    query = (query or "").strip()
    if not query:
        raise ValueError("Enter a query string first.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            required = {"vectors.bin", "vector_entries.json"}
            missing = required - names
            if missing:
                raise ValueError(f"Zip archive is missing required file(s): {', '.join(sorted(missing))}")
            for name in ("vectors.bin", "vectors.bin.meta.json", "vector_entries.json", "manifest.json"):
                if name in names:
                    zf.extract(name, tmp_dir)

        entries = json.loads((tmp_dir / "vector_entries.json").read_text(encoding="utf-8"))
        if not entries:
            return []

        index_path = tmp_dir / "vectors.bin"
        meta = read_index_metadata(index_path)
        if not meta and (tmp_dir / "manifest.json").exists():
            meta = json.loads((tmp_dir / "manifest.json").read_text(encoding="utf-8")).get("embedding") or {}
        meta = meta or {}

        method = meta.get("method")
        service = meta.get("service") if method == "api" else None
        model = meta.get("model") if method == "api" else None

        k = max(1, min(int(top_k), len(entries)))
        scores, ids = search_similar(
            str(index_path),
            query,
            top_k=k,
            method=method,
            service=service,
            model=model,
        )

        results = []
        for score, idx in zip(scores, ids):
            if idx < 0 or idx >= len(entries):
                continue
            item = entries[idx]
            results.append({
                "score": round(float(score), 4),
                "uuid": item.get("uuid", ""),
                "title": item.get("title", ""),
                "column": item.get("column", ""),
                "content": item.get("text", ""),
            })
        return results