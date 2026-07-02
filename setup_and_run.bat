@echo off

:: Step 1: Create virtual environment (if it doesn't exist)
if not exist venv (
    python -m venv venv
)

:: Step 2: Activate virtual environment
call venv\Scripts\activate.bat

:: Step 3: Install requirements + the app package
pip install -r requirements.txt
pip install -e .

:: Step 4: Launch the desktop app from source
python src\gui_main.py %*

:: Optional: Deactivate the virtual environment
deactivate
