@echo off
REM ===================================================================
REM  CARE ENT Scope Camera - build the Windows EXE on your own PC
REM
REM  1. Install Python 3.12 from python.org (tick "Add Python to PATH")
REM  2. Double-click this file
REM  3. The finished program appears in the  dist  folder
REM ===================================================================

echo.
echo  CARE ENT Scope Camera - Windows build
echo  =====================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python was not found.
    echo  Install Python 3.12 from https://www.python.org/downloads/
    echo  and tick "Add Python to PATH" during setup.
    echo.
    pause
    exit /b 1
)

echo  [1/3] Installing the libraries the app needs...
python -m pip install --upgrade pip
python -m pip install -r "%~dp0requirements.txt"
python -m pip install pyinstaller
if errorlevel 1 (
    echo  ERROR: Could not install the libraries.
    pause
    exit /b 1
)

echo.
echo  [2/3] Building the single-file application...
pyinstaller --noconfirm --clean --onefile --windowed ^
    --name CARE_ENT_Scope_Camera --icon "%~dp0app.ico" ^
    --exclude-module numpy --exclude-module cv2 ^
    "%~dp0care_ent_scope_camera.py"
if errorlevel 1 (
    echo  ERROR: The build failed.
    pause
    exit /b 1
)

echo.
echo  [3/3] Done.
echo  Your program is here:  dist\CARE_ENT_Scope_Camera.exe
echo.
pause
