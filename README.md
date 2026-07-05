# Automated Literature Review (`alr`)

A local, LLM-assisted pipeline for reviewing scientific literature:
**Collect → Analyze → Build & Query → Review & Report**. Everything is stored
locally under `~/Automated Literature Review/`.

## Quick start

```bash
# 1. Create an environment and install dependencies
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .                   # editable install of the `alr` package

# 2. Launch the main desktop app (all 4 workflow tabs)
python src/gui_main.py

# 3. Or launch the standalone Review tool
python src/review_main.py

# 4. Or use the terminal pipeline (Collect / Analyze / Visualize)
python -m alr.ui.cli.pipeline
```

First run: click **API Keys…** in the top bar to enter your Blablador and/or
DLR-Ollama keys (they are persisted and reloaded automatically). The heavy ML
stack (torch, transformers, docling, faiss) is imported lazily, so the UIs start
without loading any models.

### What each app does

- **Main app** (`gui_main.py`) — four tabs: *Collect Literature*, *Analyze
  Literature*, *Visualize & Query*, *Section Editor*, plus top-bar buttons for
  API keys and launching the Review tool.
- **Review tool** (`review_main.py`) — four tabs: *Storage Spaces*, *Documents*,
  *Database* (stats + raw browser + read-only SQL), *Overviews* (field-picker,
  filters, grouped charts, saved templates, natural-language requests).

### Data locations

```
~/Automated Literature Review/
├── 01_Collection/          keyword & search-phrase logs
├── 02_Analyzed_Data/       analyzed storage spaces
├── 10_Vector_DBs/          FAISS + text DBs, query results
├── 20_Overviews/           exported overviews & charts
├── alr_analyzed_data.db    shared SQLite database
└── API_keys_config.json    persisted API keys
```

## Documentation

- [Capability & usage report](docs/USAGE_REPORT.md) — full walkthrough of what
  the codebase can execute and how each UI tab is used.
- [Building the Windows executables](docs/BUILD_EXE.md).
- [Functionality audit](docs/FUNCTIONALITY_AUDIT.md) — UI coverage vs. backend
  capabilities.

## Building a Windows `.exe`

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm UI_pipeline.spec
# -> dist/AutomatedLiteratureReview/  (AutomatedLiteratureReview.exe + ReviewTool.exe)
```

See [docs/BUILD_EXE.md](docs/BUILD_EXE.md) for details (OCR models, CI workflow).
