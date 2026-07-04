# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Automated Literature Review desktop app.

Build (on Windows, for a .exe):
    pip install -r requirements.txt pyinstaller
    pyinstaller --clean --noconfirm UI_pipeline.spec
    # -> dist/AutomatedLiteratureReview/AutomatedLiteratureReview.exe

IMPORTANT: torch / transformers / docling / faiss are imported *lazily* (inside
functions) in the code. PyInstaller still detects the import names, but these
packages ship C-extensions, data files and dynamically-loaded submodules that
must be collected explicitly - that is what the collect_all loop below does.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

datas = []
binaries = []
hiddenimports = []


def _safe_collect_all(mod):
    try:
        b, d, h = collect_all(mod)
        binaries.extend(b)
        datas.extend(d)
        hiddenimports.extend(h)
    except Exception as e:  # package not installed / optional
        print(f"[spec] collect_all skipped for {mod}: {e}")


def _safe_copy_metadata(pkg, recursive=True):
    try:
        datas.extend(copy_metadata(pkg, recursive=recursive))
    except Exception as e:
        print(f"[spec] copy_metadata skipped for {pkg}: {e}")


# ---- Heavy / lazily-imported ML + document stack (must be collected) ----
for mod in [
    "torch",
    "transformers",
    "tokenizers",
    "safetensors",
    "huggingface_hub",
    "docling",
    "docling_core",
    "docling_parse",
    "docling_ibm_models",
    "faiss",
    "tiktoken",
    "tiktoken_ext",
    "langchain_core",
    "sklearn",
    "scholarly",
    "nltk",
    "rouge_score",
    "jiwer",
    "Levenshtein",
    "pdfplumber",
    "fitz",          # PyMuPDF
    "openpyxl",
    "pandas",
    "numpy",
    "matplotlib",    # Review-tool charts
]:
    _safe_collect_all(mod)

# ---- OCR engine used by Docling when do_ocr=True (RapidOCR on ONNX Runtime) ----
for mod in ["rapidocr", "onnxruntime"]:
    _safe_collect_all(mod)

# RapidOCR ships config YAMLs and bundled ONNX models as package data that
# collect_all misses; add them explicitly so OCR works inside the frozen app.
try:
    from PyInstaller.utils.hooks import collect_data_files
    datas += collect_data_files(
        "rapidocr",
        includes=["**/*.yaml", "**/*.onnx", "**/*.txt", "**/*.ttf", "**/*.gitkeep"],
    )
except Exception as e:
    print(f"[spec] rapidocr data collection skipped: {e}")

# ---- Runtime metadata some libs read via importlib.metadata ----
for pkg in [
    "torch", "transformers", "tokenizers", "safetensors", "huggingface-hub",
    "tqdm", "regex", "requests", "filelock", "pyyaml", "packaging",
    "docling", "docling-core", "tiktoken", "numpy",
    "rapidocr", "onnxruntime",
]:
    _safe_copy_metadata(pkg)

# ---- Our own package (all submodules, incl. lazily-imported ones) ----
hiddenimports += collect_submodules("alr")


a = Analysis(
    ['src/gui_main.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['.'],          # picks up hook-tiktoken.py
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AutomatedLiteratureReview',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # UPX can corrupt torch/onnx DLLs; keep off
    console=True,             # app streams logs to stdout; set False for a pure GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---- Second executable: the standalone Review tool (shares collected deps) ----
a_review = Analysis(
    ['src/review_main.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=['.'],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_review = PYZ(a_review.pure)
exe_review = EXE(
    pyz_review,
    a_review.scripts,
    [],
    exclude_binaries=True,
    name='ReviewTool',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# One-folder build (COLLECT) - far more reliable than one-file for torch/docling.
# Both executables live in the same folder and share the collected binaries/data.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    exe_review,
    a_review.binaries,
    a_review.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AutomatedLiteratureReview',
)
