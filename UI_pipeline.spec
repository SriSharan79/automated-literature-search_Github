# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, copy_metadata
from PyInstaller.building.datastruct import Tree
from pathlib import Path
import rapidocr

datas = []
binaries = []
hiddenimports = []

# ---- DOC LING STACK (CRITICAL) ----
for pkg in [
    "docling",
    "docling_parse",
    "docling-parse",
    "docling-ibm-models",
]:
    try:
        datas += copy_metadata(pkg)
    except Exception:
        pass

for mod in [
    "docling",
    "docling_parse",
]:
    try:
        b, d, h = collect_all(mod)
        binaries += b
        datas += d
        hiddenimports += h
    except Exception:
        pass
# ---- MPIRE (templates needed for dashboard) ----
    try:
        b, d, h = collect_all("mpire")
        binaries += b
        datas += d
        hiddenimports += h
    except Exception:
        pass
# ---- RAPIDOCR (needs default_models.yaml and other assets) ----
    try:
        datas += copy_metadata("rapidocr")
    except Exception:
        pass

    try:
        b, d, h = collect_all("rapidocr")
        binaries += b
        datas += d
        hiddenimports += h
    except Exception:
        pass

# ----------------------------------

a = Analysis(
    ['src\\UI_pipeline.py'],
    pathex=['src'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
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
    a.binaries,
    a.datas,
    [],
    name='UI_pipeline',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
