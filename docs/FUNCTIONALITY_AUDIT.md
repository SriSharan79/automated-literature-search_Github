# Functionality Audit — Desktop UI vs. Backend Capabilities

_Last updated: 2026-07-05._

This document compares what the desktop app (`alr.ui.desktop.main_window`) currently
exposes against the capabilities that already exist in the codebase but are **not**
reachable from the UI. It is meant as a backlog for surfacing existing value.

## 1. Currently in the desktop UI

| Tab / area | Functionality | Backend entry points |
|---|---|---|
| **1. Collect Literature** | Enter Research Area / Question; derive scope via LLM; suggest keywords via LLM; process keywords with scope; rank search phrases (4 strategies: RA, RQ, RA+RQ, total); run Scholarly search; save ranking to Excel | `collection_ui`, `search_phrase_generator_utils`, `run_scholarly`, `Keywords_Processing_with_scope` |
| **2. Analyze Literature** | Analyze a PDF or a folder: Docling sectioning, abstract + intro analysis, reference extraction, table/image extraction; **choose which components to extract** (Sections incl. tables/images [required], Abstract, Introduction, References, DOI/metadata, Classification); choose LLM provider + **model**; **skip fuzzy-title duplicates** before batch processing (logs skipped filenames); records **page count** in the registry (no page limit); **reuses one Docling converter across the batch**; runs **data evaluation immediately after each abstract** (section-vs-abstract grounding → SQL `evaluation_score`/`evaluation_json`); classifies by **title and by abstract text** (SQL `classification` / `abstract_classification`); auto-enriches metadata from **all download logs**; **prunes empty files/folders** the manager pre-created but nothing wrote to; runs on a **background thread with progress + cancel** | `Pdf_File_processor.process_pdf_mode_file` (`components=`), `batch_dedup`, `data_evaluator.evaluate_document/evaluate_space`, `classify_runner.classify_space/classify_abstract_space`, `download_log_enrich.enrich_from_download_logs`, `artifact_cleanup.prune_empty_artifacts` |
| **3. Visualize & Query** | Build vector DBs from analyzed data; run a RAG query report; **query scope toggle** (all sections vs. Research-Area & Key-Concept only) | `rag_builders.db_manager.generate_databases`, `query_executor.generate_query_report`, `query_executor.generate_query_report_RA_KC` |
| **4. Section Editor** | Restructure and edit section JSON (embedded + pop-out) | `section_rewriter_view.JSONRestructurerUI` |
| **5. Evaluate & Enrich** *(new)* | Re-run cheaper passes on an analyzed storage (abstract-only, references-only); build analysis-evaluation DBs (**also synced to SQL**); **classify abstracts** for a space on demand (→ SQL `abstract_classification`); build the consolidated master Excel workbook; classify a publication title on demand; run question-scored classification on demand (registry `title` / download-log `Publication Name`); enrich metadata from all download logs on demand; compute text-comparison metrics (Jaccard / ROUGE / BLEU / Levenshtein / WER) | `Folder_Data_Analyzer.process_abstract/process_references`, `data_evaluator.evaluate_space`, `classify_runner.classify_abstract_space`, `master_excel_db_builder.build_master_excel_db`, `download_log_enrich.enrich_from_download_logs`, `publication_classification.title_classifier.classify_title/classify_abstract`, `Lexical_Overlap_Metrics`, `Distance_w_Structural _Alignment` |
| Top bar | **API Keys** manager; per-provider **model chooser** (live list); **Open Review Tool** (standalone Review app) | `LLM_Config.set_api_key/get_stored_api_key`, `llm_utils.select_model_interactive`, `review_app.open_review_app` |

The standalone **Review tool** (`alr.ui.desktop.review_app`, launched from the top bar or
`review_main.py`) additionally covers: storage-space detection (complete/partial), DB linking,
DOI + classification enrichment, a raw DB browser, read-only SQL, cross-space stats, and custom
overviews (field-picker, filters, grouped charts, saved templates, natural-language requests).

The CLI (`alr.ui.cli.pipeline`) exposes roughly the same Collect / Analyze / Visualize
surface (now with model selection), plus it is the only place the console API-key prompt runs.

## 2. Backend capabilities — surfaced vs. still pending

Most of the original backlog is now wired in. Status legend: ✅ surfaced, ⏳ pending.

| Capability | What it does | Where it lives | Status |
|---|---|---|---|
| **JSON section rewriter/editor** | Full Tkinter editor for restructuring section JSON | `ui/desktop/section_rewriter_view.py` (`JSONRestructurerUI`) | ✅ Tab 4 (Section Editor) + pop-out |
| **Analysis evaluation** | Build vector-similarity + structural alignment eval DBs | `analysis_evaluation/data_evaluator.py` (`generate_databases`) | ✅ Tab 5 → "Build Evaluation DBs" |
| **Lexical / overlap metrics** | Jaccard, ROUGE, BLEU between texts | `analysis_evaluation/Lexical_Overlap_Metrics.py` | ✅ Tab 5 → "Compute Metrics" |
| **Edit-distance / structural alignment** | Levenshtein + Word Error Rate metrics | `analysis_evaluation/Distance_w_Structural _Alignment.py` | ✅ Tab 5 → "Compute Metrics" |
| **Publication classification** | Classify a paper title against the topic/question set | `publication_classification/title_classifier.classify_title`, `classify_runner.classify_space` | ✅ Tab 5 (single title) + Review tool (per space) |
| **Abstract-only / references-only analysis** | Cheaper re-run passes on an analyzed storage | `Folder_Data_Analyzer.process_abstract`, `process_references` | ✅ Tab 5 → "Re-run" buttons |
| **RA/KC-scoped query** | Query restricted to Research-Area / Key-Concept sections | `query_executor.generate_query_report_RA_KC` | ✅ Tab 3 → query-scope toggle |
| **SQL-backed review over all storages** | `alr_analyzed_data.db` consolidates every storage; dashboards/exports | `common.sql_store.AnalyzedDataStore` | ✅ Review tool (Database + Overviews tabs) |
| **Master Excel DB builder** | Consolidate per-section DBs into one master workbook | `rag_builders/master_excel_db_builder.py` (`build_master_excel_db`) | ✅ Tab 5 → "Build Master Excel DB" (new `build_master_excel_db` orchestrator over `save_to_db`) |
| **Question-scored classification sheets** | Score a publication against the full question set into sheets | `publication_classification/classify_runner.question_score_space` → `Classification_logic_with_Q's.classify_excel_data_to_sheets` | ✅ Tab 5 → "Run Question-Scored Classification" (on-demand only; source = registry `title` or download-log `Publication Name`; managed output) |
| **Query-result harvesting** | Copy matching PDFs/JSONs for a query; enrich overview with abstracts; batch enrich reports | `query_executor.harvest_query_resources`, `enrich_overview_with_abstracts`, `batch_enrich_reports` | ⏳ intentionally not surfaced — the Query tab already generates a per-query overview Excel, which covers the current need |

## 3. Notable observations

- **Section editor** (`section_rewriter_view.py`) is now wired as Tab 4 (embedded + pop-out);
  it is no longer orphaned.
- **Provider selectors** exist on Collect and Analyze tabs, but the Visualize/Query and
  Review tabs are pure vector/DB work and need no LLM key (verified: no `llm_call` in the
  `rag_builders` query path).
- **Two entry points** (desktop + CLI) call the same backend; new backend features should be
  exposed through shared helpers so both stay in parity.
- Several backend scripts still contain **hard-coded absolute paths** in `__main__` blocks
  (e.g. `/remotedata/...`, `U:\...`); these are dev drivers, not used by the UI, but worth
  parameterizing if promoted into the app.
