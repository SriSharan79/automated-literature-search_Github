"""
alr.analysis_evaluation.metric_evaluator
========================================

Batch metric evaluation over a storage space -- the batch counterpart of the
manual two-text comparison panel, following the same space-wide pattern as
:mod:`data_evaluator` (which handles the substring/grounding check).

For every analyzed document the identified reference text (abstract,
introduction or results & conclusion) is split into individual sentences and
each extracted section item is measured against EVERY sentence: the complete
sentence-level record goes to a per-document JSON detail file
(``Metric_Sentence_Details/{uuid}_{target}_Sentence_Metrics.json``), the
workbooks keep the best value per item and metric, and SQL keeps only the
workbook-level summary. The selected metric kinds:

* ``"lexical"``  -- Jaccard, ROUGE-1/2/L, BLEU (:mod:`Lexical_Overlap_Metrics`).
* ``"distance"`` -- Levenshtein distance/ratio + word error rate
  (``Distance_w_Structural _Alignment`` -- imported via importlib because of the
  space in the module filename).
* ``"cosine"``   -- embedding cosine similarity, reusing the same functions as
  :mod:`alr.rag_builders.query_executor`: existing per-section FAISS ``.bin``
  indexes are reused (vectors reconstructed by row), created from the section
  text-DB JSON when missing, and only when neither exists (e.g. the intro
  target, which has no vector DBs) is the item embedded directly.

Results are recorded in the storage space as **one dated workbook per metric
kind** (e.g. ``{date}_Abstract_Lexical_Metrics.xlsx`` /
``_Distance_Metrics.xlsx`` / ``_Cosine_Metrics.xlsx``) **plus a combined
overview workbook** holding all metric data together
(``{date}_Abstract_Metrics_Overview.xlsx`` — and the ``Introduction_*``
counterparts). Every workbook has one sheet per section plus an ``Overview``
sheet of per-document averages. The per-document averages are also merged into
the SQLite ``metrics_json`` column so the Review tool's overviews can use them.
Workbook locations come from :func:`alr.common.sections.build_metric_workbooks_map`.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

from colorama import Fore

from alr.common.file_manager import DataAnalyzeManager, Vec_DB_Manager
from alr.common.sections import (
    ALR_SECTIONS, INTRO_SECTIONS, RESCON_SECTIONS,
    ABSTRACT_TEXT_KEY, INTRO_TEXT_KEY, RESCON_TEXT_KEY,
    build_sections_map_vdb, build_metric_workbooks_map,
)
from alr.analysis_evaluation.data_evaluator import (
    _write_section_sheet_flat, _fetch_metadata,
    _load_abstract_json, _load_intro_json, _load_rescon_json,
    _load_recorded_abstracts, _load_recorded_intros, _load_recorded_rescons,
)

METRIC_KINDS = ("lexical", "distance", "cosine")

# Metric columns produced per kind (order preserved in the workbooks).
_KIND_COLUMNS = {
    "lexical": ("jaccard", "rouge1", "rouge2", "rougeL", "bleu"),
    "distance": ("levenshtein_distance", "similarity_ratio", "word_error_rate"),
    "cosine": ("cosine_similarity",),
}

# For these metrics a SMALLER value means a better match; every other metric
# column is a similarity where bigger is better. Used to pick the per-item
# best value across the reference sentences.
_LOWER_IS_BETTER = {"levenshtein_distance", "word_error_rate"}


def _split_sentences(text) -> list[str]:
    """
    Split a reference text into individual sentences (regex-based on ./!/?
    boundaries — deliberately not nltk, whose punkt data may be unavailable).
    Returns [] for empty input; a text without any boundary comes back as one
    "sentence", so every caller can treat the result uniformly.
    """
    parts = re.split(r"(?<=[.!?])\s+", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _best_value(col, values):
    """Best non-None value of one metric column across the sentences (min for
    distance-like columns, max otherwise). Returns (value, sentence_number)
    with 1-based sentence numbering, or (None, None) when nothing usable."""
    scored = [(v, i) for i, v in enumerate(values, 1) if isinstance(v, (int, float))]
    if not scored:
        return None, None
    pick = min(scored) if col in _LOWER_IS_BETTER else max(scored)
    return pick[0], pick[1]

_punkt_ready = None  # tri-state: None = unchecked, True/False = usable


def _ensure_punkt() -> bool:
    """BLEU needs nltk punkt data; try a one-time quiet download if missing."""
    global _punkt_ready
    if _punkt_ready is None:
        try:
            from nltk.tokenize import word_tokenize
            word_tokenize("probe sentence")
            _punkt_ready = True
        except Exception:
            try:
                import nltk
                nltk.download("punkt", quiet=True)
                nltk.download("punkt_tab", quiet=True)
                from nltk.tokenize import word_tokenize
                word_tokenize("probe sentence")
                _punkt_ready = True
            except Exception as e:
                print(Fore.YELLOW + f"⚠️ BLEU disabled (nltk punkt unavailable): {e}")
                _punkt_ready = False
    return _punkt_ready


def _lexical_metrics(reference, candidate) -> dict:
    """Jaccard/ROUGE/BLEU; each guarded so one failure records None, not an abort."""
    from alr.analysis_evaluation import Lexical_Overlap_Metrics as lom

    out = {c: None for c in _KIND_COLUMNS["lexical"]}
    try:
        out["jaccard"] = round(lom.calculate_jaccard_similarity(reference, candidate), 4)
    except Exception as e:
        print(Fore.YELLOW + f"⚠️ Jaccard failed: {e}")
    try:
        rouge = lom.calculate_rouge_scores(reference, candidate)
        out["rouge1"] = round(rouge.get("ROUGE-1", 0.0), 4)
        out["rouge2"] = round(rouge.get("ROUGE-2", 0.0), 4)
        out["rougeL"] = round(rouge.get("ROUGE-L", 0.0), 4)
    except Exception as e:
        print(Fore.YELLOW + f"⚠️ ROUGE failed: {e}")
    if _ensure_punkt():
        try:
            out["bleu"] = round(lom.calculate_bleu_score(reference, candidate), 4)
        except Exception as e:
            print(Fore.YELLOW + f"⚠️ BLEU failed: {e}")
    return out


def _distance_metrics(reference, candidate) -> dict:
    """Levenshtein + WER from the structural-alignment module (space in filename)."""
    out = {c: None for c in _KIND_COLUMNS["distance"]}
    try:
        mod = importlib.import_module("alr.analysis_evaluation.Distance_w_Structural _Alignment")
        res = mod.calculate_edit_distance_metrics(reference, candidate)
        out["levenshtein_distance"] = res["character_level"]["levenshtein_distance"]
        out["similarity_ratio"] = round(res["character_level"]["similarity_ratio"], 4)
        out["word_error_rate"] = round(res["word_level"]["word_error_rate"], 4)
    except Exception as e:
        print(Fore.YELLOW + f"⚠️ Distance metrics failed: {e}")
    return out


class _CosineContext:
    """
    Embedding cosine similarity against a per-document reference vector,
    reusing the storage space's per-section FAISS indexes where possible
    (same functions and index/metadata handling as query_executor).
    """

    def __init__(self, vdb, section_keys):
        # {section_key: (bin_path, json_path)} for the target's sections —
        # every target (abstract, intro, rescon) has RAG vector DB paths now;
        # sections whose DBs were never built simply fall back to direct
        # embedding inside _section_index.
        try:
            self.section_paths = build_sections_map_vdb(vdb, only=section_keys)
        except Exception:
            self.section_paths = {}
        self._cache = {}  # section_key -> (index, contents) or None

    @staticmethod
    def _normalize(vec):
        import numpy as np
        vec = np.asarray(vec, dtype="float32").reshape(-1)
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm else vec

    def embed(self, text):
        from alr.rag_builders.vector_db_updater import vectorize_strings
        return self._normalize(vectorize_strings([str(text)])[0])

    def embed_many(self, texts):
        """Embed several texts in ONE call; returns a (n, dim) row-normalized matrix."""
        import numpy as np
        from alr.rag_builders.vector_db_updater import vectorize_strings
        mat = np.asarray(vectorize_strings([str(t) for t in texts]), dtype="float32")
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return mat / norms

    def _section_index(self, section_key):
        """Load (index, contents) for a section; create the index if missing."""
        if section_key in self._cache:
            return self._cache[section_key]
        result = None
        paths = self.section_paths.get(section_key)
        if paths:
            bin_path, json_path = str(paths[0]), str(paths[1])
            try:
                from alr.common.json_utils import get_key_from_file
                from alr.rag_builders.vector_db_updater import (
                    load_index_file, vectorize_strings, create_faiss_index_cosine, save_index_file,
                )
                contents = get_key_from_file(json_path, "Content") if Path(json_path).exists() else None
                if contents:
                    index = load_index_file(bin_path)
                    if index is None:
                        # Vector DB missing -> create it from the text DB, then use it.
                        print(Fore.CYAN + f"🆕 Building vector index for '{section_key}'…")
                        embeds = vectorize_strings([str(c) for c in contents])
                        index = create_faiss_index_cosine(embeds)
                        save_index_file(index, bin_path)
                    if index is not None and getattr(index, "ntotal", 0):
                        result = (index, [str(c) for c in contents])
            except Exception as e:
                print(Fore.YELLOW + f"⚠️ Vector index unavailable for '{section_key}': {e}")
        self._cache[section_key] = result
        return result

    def item_vector(self, section_key, item_text):
        """Reconstruct the item's stored vector from the section index, else embed it."""
        entry = self._section_index(section_key)
        if entry:
            index, contents = entry
            try:
                pos = contents.index(str(item_text))
                if pos < index.ntotal:
                    return self._normalize(index.reconstruct(pos))
            except ValueError:
                pass  # item not in the text DB (e.g. never synced) -> embed directly
        return self.embed(item_text)

    def sentence_similarities(self, section_key, item_text, sent_mat):
        """Cosine of one item against EVERY reference sentence (one value per
        sentence, in order); the item vector is looked up/reconstructed once."""
        import numpy as np
        try:
            item_vec = self.item_vector(section_key, item_text)
            return [round(float(v), 4) for v in np.dot(sent_mat, item_vec)]
        except Exception as e:
            print(Fore.YELLOW + f"⚠️ Cosine failed for '{section_key}': {e}")
            return [None] * len(sent_mat)


def _target_config(MF, VDB, target):
    """Section keys, JSON loader, reference-text key, workbooks and UUID source per target."""
    workbooks = {kind: Path(p) for kind, p in build_metric_workbooks_map(VDB, target).items()}
    if target == "intro":
        return {
            "section_keys": [key for key, _ in INTRO_SECTIONS],
            "loader": _load_intro_json,
            "text_key": INTRO_TEXT_KEY,
            "workbooks": workbooks,
            "recorded": _load_recorded_intros(MF),
            "sql_label": "introduction",
        }
    if target == "rescon":
        return {
            "section_keys": [key for key, _ in RESCON_SECTIONS],
            "loader": _load_rescon_json,
            "text_key": RESCON_TEXT_KEY,
            "workbooks": workbooks,
            "recorded": _load_recorded_rescons(MF),
            "sql_label": "results_conclusion",
        }
    return {
        "section_keys": [spec.key for spec in ALR_SECTIONS],
        "loader": _load_abstract_json,
        "text_key": ABSTRACT_TEXT_KEY,
        "workbooks": workbooks,
        "recorded": _load_recorded_abstracts(MF),
        "sql_label": "abstract",
    }


def _sentence_metric_rows(kinds, cosine_ctx, section_key, sentences, candidate, sent_mat) -> list[dict]:
    """
    Compute the selected metric columns for one extracted item against EVERY
    reference sentence. Returns one dict per sentence (same order).
    """
    rows = [{} for _ in sentences]
    if "lexical" in kinds:
        for row, sentence in zip(rows, sentences):
            row.update(_lexical_metrics(sentence, candidate))
    if "distance" in kinds:
        for row, sentence in zip(rows, sentences):
            row.update(_distance_metrics(sentence, candidate))
    if "cosine" in kinds and cosine_ctx is not None and sent_mat is not None:
        sims = cosine_ctx.sentence_similarities(section_key, candidate, sent_mat)
        for row, sim in zip(rows, sims):
            row["cosine_similarity"] = sim
    return rows


def _write_sentence_detail_json(details_dir, uuid, sql_label, payload) -> None:
    """
    Write the per-document sentence-level metric detail file
    (``{uuid}_{target}_Sentence_Metrics.json``). This is the full record of
    every metric value for every (reference sentence, attribute value) pair;
    the workbooks keep only the best value per pair, and SQL only the
    workbook-level summary.
    """
    import json

    try:
        details_dir = Path(details_dir)
        details_dir.mkdir(parents=True, exist_ok=True)
        path = details_dir / f"{uuid}_{sql_label}_Sentence_Metrics.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(Fore.YELLOW + f"⚠️ Could not write sentence-metric details for {uuid}: {e}")


def _push_metrics_to_sql(uuid, averages, sql_label, db_path=None) -> bool:
    """Merge per-document metric averages into the metrics_json column (if row exists)."""
    import json
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    store = AnalyzedDataStore(db_path or DB_PATH)
    doc = store.get_document(uuid)
    if not doc:
        return False
    try:
        merged = json.loads(doc.get("metrics_json") or "{}")
        if not isinstance(merged, dict):
            merged = {}
    except (ValueError, TypeError):
        merged = {}
    merged[sql_label] = averages
    store.update_document(uuid, {"metrics_json": json.dumps(merged)})
    return True


def _existing_metrics(uuid, metric_cols, sql_label, db_path=None):
    """
    Return the stored per-document metric averages for ``uuid`` if its
    ``metrics_json[sql_label]`` already covers every selected metric column,
    else ``None``. Used by ``mode="copy"`` (mirroring data_evaluator's
    ``_existing_evaluation``) to reuse prior metrics instead of recomputing.
    A document evaluated before with only SOME of the selected kinds (e.g.
    lexical done, cosine newly requested) does not count and is recomputed.
    """
    import json
    from alr.common.sql_store import AnalyzedDataStore, DB_PATH

    try:
        store = AnalyzedDataStore(db_path or DB_PATH)
        row = store.get_document(uuid)
    except Exception:
        return None
    if not row:
        return None
    try:
        merged = json.loads(row.get("metrics_json") or "{}")
    except (ValueError, TypeError):
        return None
    stored = merged.get(sql_label)
    if not isinstance(stored, dict):
        return None
    if all(col in stored for col in metric_cols):
        return stored
    return None


def evaluate_space_metrics(storage_path, kinds, target="abstract", db_path=None,
                           progress_callback=None, should_cancel=None, mode="generate") -> int:
    """
    Batch-compute the selected metric ``kinds`` (subset of :data:`METRIC_KINDS`)
    for every analyzed document in a storage space, against the ``target`` data
    ("abstract", "intro" or "rescon"). Returns the number of documents evaluated.

    The document's identified reference text is split into individual
    sentences and every extracted attribute value is measured against EACH
    sentence:

    * the full sentence-level record (every metric value for every
      sentence/attribute-value pair) goes to a per-document JSON file,
      ``Metric_Sentence_Details/{uuid}_{target}_Sentence_Metrics.json``;
    * the workbooks store only the BEST value per attribute value and metric
      (minimum for Levenshtein distance / word error rate, maximum for all
      similarity metrics), plus which sentence produced it in the JSON;
    * SQL (``metrics_json``) gets only the workbook-level summary (averages
      of the best values) — never the sentence-level detail.

    ``mode="copy"`` reuses prior metrics: documents whose SQL ``metrics_json``
    already covers every selected metric column are skipped (only new/partially
    evaluated documents are computed). ``mode="generate"`` (default, previous
    behaviour) recomputes every document and updates the workbook rows in place.

    Results go to the storage space as one dated workbook **per metric kind**
    plus a **combined overview workbook** with all metric data (each workbook:
    one sheet per section with per-item metric columns, plus an ``Overview``
    sheet of per-document averages), and into the SQLite ``metrics_json``
    column. Re-runs update rows in place (no duplicates).
    ``progress_callback(done, total)`` / ``should_cancel`` support progress
    reporting and cancellation.
    """
    kinds = {k for k in kinds if k in METRIC_KINDS}
    if not kinds:
        print("No metric kinds selected; nothing to do.")
        return 0

    MF = storage_path if isinstance(storage_path, DataAnalyzeManager) else DataAnalyzeManager(storage_path)
    VDB = Vec_DB_Manager(MF.folder)
    cfg = _target_config(MF, VDB, target)
    if not cfg["recorded"]:
        print(f"No recorded {target} analyses found; nothing to evaluate.")
        return 0

    cosine_ctx = _CosineContext(VDB, cfg["section_keys"]) if "cosine" in kinds else None
    metric_cols = [c for k in ("lexical", "distance", "cosine") if k in kinds for c in _KIND_COLUMNS[k]]

    count = 0
    total = len(cfg["recorded"])
    for i, uuid in enumerate(cfg["recorded"], 1):
        if should_cancel is not None and should_cancel():
            print("Metric evaluation cancelled by user.")
            break
        try:
            if mode == "copy":
                prior = _existing_metrics(uuid, metric_cols, cfg["sql_label"], db_path)
                if prior is not None:
                    print(Fore.YELLOW + f"⏭️ Reusing existing {target} metrics for {uuid} (copy mode).")
                    count += 1
                    if progress_callback:
                        progress_callback(i, total)
                    continue

            MF.update_id_files(uuid)
            title, file_name = _fetch_metadata(MF, uuid)
            json_data = cfg["loader"](MF, uuid)
            if not json_data:
                continue
            reference = str(json_data.get(cfg["text_key"], "") or "")
            if not reference.strip():
                continue

            # Sentence-level references: every attribute value is measured
            # against each sentence; the workbooks keep the best value.
            sentences = _split_sentences(reference) or [reference.strip()]

            sent_mat = None
            if cosine_ctx is not None:
                try:
                    sent_mat = cosine_ctx.embed_many(sentences)
                except Exception as e:
                    print(Fore.YELLOW + f"⚠️ Reference sentence embedding failed for {uuid}: {e}")

            # Per-metric running sums of the BEST values, for the
            # document-level averages.
            sums = {c: [0.0, 0] for c in metric_cols}  # col -> [total, n]
            workbooks = cfg["workbooks"]  # {kind: workbook, "overview": combined, "details": JSON folder}
            base = {"UUID": str(uuid), "Title": title, "Filename": file_name}
            detail_sections = {}

            for key in cfg["section_keys"]:
                value = json_data.get(key, None)
                if value is None or value == "Not Found":
                    continue
                items = [str(v) for v in value] if isinstance(value, list) else [str(value)]

                # One row per document: a per-kind entry for each metric's own
                # workbook, and a combined entry carrying all metric data.
                kind_entries = {k: dict(base) for k in kinds}
                combined_entry = dict(base)
                detail_items = []
                for n, item in enumerate(items, 1):
                    sentence_rows = _sentence_metric_rows(
                        kinds, cosine_ctx, key, sentences, item, sent_mat)
                    prefix = f"Item {n} " if len(items) > 1 else ""
                    combined_entry[f"{prefix}Content"] = item
                    best_detail = {}
                    for kind in kinds:
                        kind_entries[kind][f"{prefix}Content"] = item
                        for col in _KIND_COLUMNS[kind]:
                            val, sent_no = _best_value(col, [r.get(col) for r in sentence_rows])
                            best_detail[col] = {"value": val, "sentence": sent_no}
                            kind_entries[kind][f"{prefix}{col}"] = val
                            combined_entry[f"{prefix}{col}"] = val
                            if isinstance(val, (int, float)):
                                sums[col][0] += float(val)
                                sums[col][1] += 1
                    detail_items.append({
                        "Item": n,
                        "Content": item,
                        "Best": best_detail,
                        "Per sentence": [
                            {"Sentence": i, **row}
                            for i, row in enumerate(sentence_rows, 1)
                        ],
                    })
                for kind in kinds:
                    _write_section_sheet_flat(workbooks[kind], key, kind_entries[kind])
                _write_section_sheet_flat(workbooks["overview"], key, combined_entry)
                detail_sections[key] = detail_items

            _write_sentence_detail_json(workbooks["details"], uuid, cfg["sql_label"], {
                **base,
                "Target": target,
                "Metric kinds": sorted(kinds),
                "Reference sentences": [
                    {"Sentence": i, "Text": s} for i, s in enumerate(sentences, 1)
                ],
                "Sections": detail_sections,
            })

            averages = {col: (round(t / n, 4) if n else None) for col, (t, n) in sums.items()}
            # Per-kind Overview sheets carry that kind's averages; the combined
            # overview workbook's Overview sheet carries all of them.
            for kind in kinds:
                kind_avgs = {f"avg_{c}": averages.get(c) for c in _KIND_COLUMNS[kind]}
                _write_section_sheet_flat(workbooks[kind], "Overview", {**base, **kind_avgs})
            _write_section_sheet_flat(workbooks["overview"], "Overview",
                                      {**base, **{f"avg_{c}": v for c, v in averages.items()}})

            try:
                _push_metrics_to_sql(uuid, averages, cfg["sql_label"], db_path)
            except Exception as e:
                print(Fore.YELLOW + f"⚠️ Could not push metrics to SQL for {uuid}: {e}")

            count += 1
            print(Fore.GREEN + f"✅ Metrics ({', '.join(sorted(kinds))}) recorded for {uuid} ({target}).")
        except Exception as e:
            print(Fore.YELLOW + f"⚠️ Metric evaluation failed for {uuid}: {e}")
        if progress_callback:
            progress_callback(i, total)

    written = [str(cfg["workbooks"][k]) for k in sorted(kinds)] + [str(cfg["workbooks"]["overview"])]
    print(Fore.GREEN + f"✅ Metric evaluation finished: {count} document(s) ({target}).")
    for path in written:
        print(Fore.GREEN + f"   📄 {path}")
    return count