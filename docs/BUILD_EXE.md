# Building the Windows `.exe`

The desktop app (`alr.ui.desktop.main_window`) is packaged with **PyInstaller**.

> **A `.exe` must be built on Windows.** PyInstaller does not cross-compile — running
> it on macOS/Linux produces a native binary for *that* OS, not a Windows `.exe`.

## Files involved
- `src/gui_main.py` — main app entry point (launches the Tk app).
- `src/review_main.py` — standalone **Review tool** entry point (built as a second exe).
- `UI_pipeline.spec` — the PyInstaller build recipe (collects the lazily-imported
  torch / transformers / docling / faiss stacks and their data files). It produces **two**
  executables in one folder: `AutomatedLiteratureReview.exe` and `ReviewTool.exe`.
- `hook-tiktoken.py` — bundles the dynamically-loaded `tiktoken_ext` plugins.
- `build_exe.bat` — one-command Windows build.
- `build_app.sh` — local macOS/Linux validation build (native binary, **not** `.exe`).

## Build on Windows

```bat
git clone <repo> && cd automated-literature-search
build_exe.bat
```

or manually:

```bat
python -m venv build_venv
build_venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pip install -e .
pyinstaller --clean --noconfirm UI_pipeline.spec
```

Result: `dist\AutomatedLiteratureReview\` containing **two** executables —
`AutomatedLiteratureReview.exe` (main app) and `ReviewTool.exe` (standalone Review tool,
also reachable via the main app's "Open Review Tool" button).

**Distribute the whole `dist\AutomatedLiteratureReview\` folder**, not just the `.exe`
(this is a one-folder build; the `.exe` needs the sibling DLLs and data files).

## Notes / expectations
- **Size:** the bundle is large (~2–5 GB) because it includes torch + transformers +
  docling. This is expected for this ML stack.
- **console window:** `UI_pipeline.spec` sets `console=True` so the app's log output is
  visible and errors are easy to diagnose. For a pure windowed app set `console=False`
  in the spec (the app already redirects stdout to its in-window log).
- **UPX is disabled** on purpose — compressing torch/onnx DLLs can corrupt them.
- **Models are not bundled.** The embedding model (`Qwen/Qwen3-Embedding-8B`) and any
  local HF model are loaded from disk at runtime from the paths in
  `alr/common/LLM_Config.py` / `alr/rag_builders/vector_db_updater.py`. Ship/point those
  paths appropriately, or the analysis/RAG steps that need them will fail at run time
  (the app still starts and the LLM-API features work without them).
- **OCR is included.** Docling OCR (`do_ocr=True`) is wired to **RapidOCR** on the
  ONNX Runtime backend (`rapidocr` + `onnxruntime`, both pinned in `requirements.txt`).
  The spec bundles the engine, its native DLLs, config YAMLs and the small default ONNX
  models. RapidOCR fetches its detection/recognition models from ModelScope **on first
  use**, so the first OCR run needs internet; afterwards they are cached locally.
- **First launch** creates `~/Automated Literature Review/` (config, storage, and the
  `alr_analyzed_data.db` review database).

## Cross-building without a Windows machine (GitHub Actions)
No Windows machine needed — `.github/workflows/build-exe.yml` builds the `.exe` on a
GitHub-hosted `windows-latest` runner and uploads it as an artifact.

- **Run it:** push a tag like `v1.0.0`, or trigger it manually from the repo's
  **Actions** tab → *Build Windows EXE* → *Run workflow*.
- **Get the `.exe`:** open the finished run and download the
  **`AutomatedLiteratureReview-windows`** artifact (a zip of the one-folder build).

The runner installs `requirements.txt` + PyInstaller, runs `UI_pipeline.spec`, and
publishes `dist/AutomatedLiteratureReview`. The build is heavy (torch/transformers/
docling), so expect it to take a while.
