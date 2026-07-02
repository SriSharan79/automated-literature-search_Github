@echo off
REM ============================================================================
REM  Build the Automated Literature Review desktop app into a Windows .exe.
REM  Run this on Windows (PyInstaller cannot cross-compile from macOS/Linux).
REM  Output: dist\AutomatedLiteratureReview\AutomatedLiteratureReview.exe
REM ============================================================================

setlocal

REM 1. Create + activate a build virtual environment
if not exist build_venv (
    python -m venv build_venv
)
call build_venv\Scripts\activate.bat

REM 2. Install dependencies + the app package + PyInstaller
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller
pip install -e .

REM 3. Build using the spec (one-folder build)
pyinstaller --clean --noconfirm UI_pipeline.spec

echo.
echo ============================================================================
echo  Build finished.
echo  Executable: dist\AutomatedLiteratureReview\AutomatedLiteratureReview.exe
echo  Distribute the WHOLE dist\AutomatedLiteratureReview folder (not just the .exe).
echo ============================================================================

deactivate
endlocal
