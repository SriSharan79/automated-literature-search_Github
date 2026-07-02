#!/usr/bin/env bash
# ============================================================================
#  Build the app with PyInstaller on macOS/Linux.
#  NOTE: This produces a NATIVE binary (Mach-O / ELF), NOT a Windows .exe.
#  Use it only to validate the spec locally; build the real .exe on Windows
#  with build_exe.bat.
#  Output: dist/AutomatedLiteratureReview/AutomatedLiteratureReview
# ============================================================================
set -e

python3 -m venv build_venv
source build_venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
pip install -e .

pyinstaller --clean --noconfirm UI_pipeline.spec

echo
echo "Build finished -> dist/AutomatedLiteratureReview/"
