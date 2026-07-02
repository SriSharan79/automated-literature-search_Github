@echo off

:: Step 1: Create virtual environment (if it doesn't exist)
if not exist venv (
    python -m venv venv
)

:: Step 2: Activate virtual environment
call venv\Scripts\activate.bat

:: Step 3: Install requirements
pip install -r requirements.txt

:: Step 4: Run the Python script
python todo.py %*

:: Optional: Deactivate the virtual environment
deactivate
