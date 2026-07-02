# Functionality Audit — Desktop UI vs. Backend Capabilities

_Last updated: 2026-07-02._

This document compares what the desktop app (`alr.ui.desktop.main_window`) currently
exposes against the capabilities that already exist in the codebase but are **not**
reachable from the UI. It is meant as a backlog for surfacing existing value.

## 1. Currently in the desktop UI

| Tab / area | Functionality | Backend entry points |
|---|---|---|
| **1. Collect Literature** | Enter Research Area / Question; derive scope via LLM; suggest keywords via LLM; process keywords with scope; rank search phrases (4 strategies: RA, RQ, RA+RQ, total); run Scholarly search; save ranking to Excel | `collection_ui`, `search_phrase_generator_utils`, `run_scholarly`, `Keywords_Processing_with_scope` |
| **2. Analyze Literature** | Analyze a PDF or a folder: Docling sectioning, abstract + intro analysis, reference extraction, table/image extraction; choose LLM provider + **model** | `Pdf_File_processor.process_pdf_mode_file`, `Folder_Data_Analyzer.process_folder` |
| **3. Visualize & Query** | Build vector DBs from analyzed data; run a RAG query report | `rag_builders.db_manager.generate_databases`, `query_executor.generate_query_report` |
| **4. Review Data** *(new)* | Browse/search analyzed documents; edit & save section fields; open source PDF / abstract JSON; sync a storage folder into the DB | `common.sql_store`, `review_view.ReviewDataView` |
| Top bar | **API Keys** manager; per-provider **model chooser** (live list) | `LLM_Config.set_api_key/get_stored_api_key`, `llm_utils.select_model_interactive` |

The CLI (`alr.ui.cli.pipeline`) exposes roughly the same Collect / Analyze / Visualize
surface (now with model selection), plus it is the only place the console API-key prompt runs.

## 2. Backend capabilities NOT yet in the UI

Ordered roughly by user value / low effort to expose.

| Capability | What it does | Where it lives | Effort to surface |
|---|---|---|---|
| **JSON section rewriter/editor** | A full Tkinter editor for restructuring section JSON — already written but never added to the notebook | `ui/desktop/section_rewriter_view.py` (`JSONRestructurerUI`) | **Low** — add as a tab or button (it's already a Tk view) |
| **Analysis evaluation** | Score analyzed output against source text: vector-similarity + structural alignment eval DBs | `analysis_evaluation/data_evaluator.py` (`generate_databases`, `generate_combined_databases`) | Medium — add an "Evaluate" action + results view |
| **Lexical / overlap metrics** | Jaccard, ROUGE, BLEU between texts | `analysis_evaluation/Lexical_Overlap_Metrics.py` | Low — small metrics panel |
| **Edit-distance / structural alignment** | Edit-distance based similarity metrics | `analysis_evaluation/Distance_w_Structural _Alignment.py` | Low |
| **Publication classification** | Classify a paper title / publication against a question set and score it into sheets | `analysis_evaluation/publication_classification/` (`title_classifier.classify_title`, `Classification_logic_with_Q's`) | Medium — needs an input form + results table |
| **Abstract-only / references-only analysis** | Run just abstract analysis or just reference extraction (cheaper passes) | `Folder_Data_Analyzer.process_abstract`, `process_references` | Low — extra radio options in the Analyze tab |
| **Master Excel DB builder** | Consolidate the per-section DBs into one master workbook | `rag_builders/master_excel_db_builder.py` (`save_to_db`) | Low — one button |
| **RA/KC-scoped query** | Query restricted to Research-Area / Key-Concept sections | `query_executor.generate_query_report_RA_KC` | Low — a mode toggle on the Query tab |
| **Query-result harvesting** | Copy matching PDFs/JSONs for a query; enrich overview with abstracts; batch enrich reports | `query_executor.harvest_query_resources`, `enrich_overview_with_abstracts`, `batch_enrich_reports` | Medium — post-query "collect results" step |
| **SQL-backed review over all storages** *(new store)* | The new `alr_analyzed_data.db` consolidates every storage folder; could power dashboards/exports (CSV/Excel) | `common.sql_store.AnalyzedDataStore` | Low–Medium — add export + summary charts |

## 3. Notable observations

- **Orphaned UI:** `section_rewriter_view.py` is a complete, working editor that no code
  imports — the quickest win is to wire it in.
- **Provider selectors** exist on Collect and Analyze tabs, but the Visualize/Query and
  Review tabs are pure vector/DB work and need no LLM key (verified: no `llm_call` in the
  `rag_builders` query path).
- **Two entry points** (desktop + CLI) call the same backend; new backend features should be
  exposed through shared helpers so both stay in parity.
- Several backend scripts still contain **hard-coded absolute paths** in `__main__` blocks
  (e.g. `/remotedata/...`, `U:\...`); these are dev drivers, not used by the UI, but worth
  parameterizing if promoted into the app.
