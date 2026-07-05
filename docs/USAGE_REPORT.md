# Automated Literature Review — Capability & Usage Report

This report describes what the `alr` codebase can execute and how the interactive
UIs are used to execute it.

## 1. What the system does end-to-end

A four-stage literature-review pipeline, all stored locally under
`~/Automated Literature Review/`:

**Collect** → find relevant literature from a research area/question →
**Analyze** → extract & LLM-analyze PDFs (sections, abstract, intro, references,
tables/images, DOI, classification) → **Build & Query** → vector/RAG databases
over the analyzed data → **Review & Report** → browse, curate, and build
overviews of everything in a shared SQLite database.

## 2. How to launch it

| Entry point | Launches | Command |
|---|---|---|
| `src/gui_main.py` | Main desktop app (all 4 workflow tabs) | `python src/gui_main.py` |
| `src/review_main.py` | Standalone Review tool | `python src/review_main.py` |
| `src/alr/ui/cli/pipeline.py` | Terminal (Collect / Analyze / Visualize menu) | `python -m alr.ui.cli.pipeline` |
| `src/Main.py` | Dev driver (batch folder analysis) | script |

Packaged as **two Windows executables** in one folder:
`AutomatedLiteratureReview.exe` and `ReviewTool.exe` (built via
`UI_pipeline.spec` / the GitHub Actions workflow).

## 3. Main app — tab by tab (what it executes + how)

**Top bar (always available):** `API Keys…` (enter/persist Blablador &
DLR-Ollama keys), `Open Review Tool` (launch the Review window).

### Tab 1 — Collect Literature
- Enter **Research Area** + **Research Question** → **Generate Scope via LLM**.
- **Suggest keywords** (LLM) or type your own → **Process Keywords** with scope.
- Pick a **ranking strategy** (RA / RQ / RA+RQ / total) → **Run Scholarly
  Search** or **Save ranking to Excel**.
- Per-tab **LLM provider** (O/B) + **Choose Model…** (live model list).

### Tab 2 — Analyze Literature
- Select a **PDF file or folder** → **Execute Document Extraction & Analysis**.
- Runs: Docling sectioning + **OCR (RapidOCR)**, abstract & introduction
  analysis, reference extraction, table/image extraction.
- Automatically: **syncs results to the SQLite DB** and runs **DOI-metadata +
  publication-classification enrichment**.
- Choose provider + model; results go to `02_Analyzed_Data/` (fixed managed
  storage space).

### Tab 3 — Visualize & Query
- Point to an analyzed-data folder → **Generate DB Framework & Query Report**.
- Builds **FAISS vector DBs** (Qwen embeddings) + text DBs, then runs a **RAG
  query** and writes a report. (No LLM key needed — pure vector search.)

### Tab 4 — Section Editor
- Load a section-JSON file → restructure/edit sections → save.

## 4. Review tool — tab by tab

Launched from the main app's **Open Review Tool** button or standalone
(`ReviewTool.exe`). All long operations run on a **background thread with a
progress bar + Cancel**.

### Storage Spaces
- **Select folder…** → auto-detects every `DataAnalyzeManager` storage space in
  the hierarchy, marked **complete** or **partial**, plus any
  `*_download_log.xlsx`.
- Actions on a selected space: **Link to database** (sync into SQLite),
  **Extract DOI/metadata**, **Classify publications**, **Import bibliographic
  data** (join a download log by filename). **Link ALL** for the whole set.

### Documents
- Review analyzed documents from the SQLite DB: **search**, **edit** any section
  field, **Save**, **Open PDF / Open Abstract JSON**, **Export CSV/Excel**.

### Database
- **Statistics panel** — totals + abstract/DOI/classification coverage, distinct
  years/types, per-storage-space counts.
- **Raw table browser** — the full `documents` table (all columns) with a quick
  filter.
- **SQL query box** — type a read-only `SELECT` (writes/DDL rejected), get a
  results grid, export it.

### Overviews
- **Field-picker + filters** (folder / year / type / research area) →
  **Preview** → **Export Excel/CSV** to `20_Overviews/`.
- **Group by** any column → aggregated counts + **bar/pie chart** (matplotlib) →
  **Export chart PNG**.
- **Saved templates** — name/save/load/delete overview definitions.
- **Describe in words** → LLM turns a plain-English request into the
  field/filter/group selection.

## 5. Where everything lives

```
~/Automated Literature Review/
├── 01_Collection/          keyword & search-phrase logs
├── 02_Analyzed_Data/       storage spaces (registry, sections, abstracts,
│                           references, tables/images, DOI, classification)
├── 10_Vector_DBs/          FAISS + text DBs, query results
├── 20_Overviews/           exported overviews & charts
├── alr_analyzed_data.db    shared SQLite (documents + overview_templates)
└── API_keys_config.json    persisted keys (loaded into env on launch)
```

## 6. LLM services & keys

Three backends: **Blablador** (`B`), **DLR Ollama** (`O`), and a **local Hugging
Face** model path (`L`). Models are selectable live per provider. Keys are
entered once in the UI and persist. The LLM is needed for collection, analysis,
classification, and the NL overview builder; the RAG/query and all
Database/Overview browsing work offline.

## 7. Backend capabilities that exist but aren't in the UI yet

From the audit (`docs/FUNCTIONALITY_AUDIT.md`): analysis-evaluation metrics
(ROUGE/BLEU/Jaccard, edit-distance/structural alignment), the question-based
publication scoring (`Classification_logic_with_Q's`), the Master-Excel DB
builder, RA/KC-scoped queries, and query-result harvesting
(`harvest_query_resources`). These run from code/scripts but have no button yet.
