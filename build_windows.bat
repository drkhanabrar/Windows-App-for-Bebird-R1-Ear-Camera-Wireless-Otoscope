@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP=CARE ENT Scope Camera"
set "PY=py"

where %PY% >nul 2>nul
if errorlevel 1 (
    echo.
    echo Python Launcher ^(py^) was not found.
    echo Install Python from python.org and enable the Python Launcher.
    pause
    exit /b 1
)

echo ================================================================
echo CARE ENT Scope Camera - Premium Windows EXE Builder
echo ================================================================
echo.

%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail

%PY% -m pip install --upgrade pyinstaller opencv-python numpy Pillow
if errorlevel 1 goto :fail

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP%.spec" del /q "%APP%.spec"

echo.
echo PyInstaller version:
%PY% -m PyInstaller --version
if errorlevel 1 goto :fail

echo.
echo Building single-file Windows application...
%PY% -m PyInstaller --noconfirm --clean --windowed --onefile --icon=care_ent_scope.ico --version-file=version_info.txt --name "%APP%" care_ent_scope_camera.py
if errorlevel 1 goto :fail

echo.
echo ================================================================
echo BUILD COMPLETE
echo ================================================================
echo EXE:
echo %CD%\dist\%APP%.exe
if exist "%CD%\dist\%APP%.exe" echo.
if exist "%CD%\dist\%APP%.exe" echo Double-click the EXE to launch CARE ENT Scope Camera.
echo.
pause
exit /b 0

:fail
echo.
echo ================================================================
echo BUILD FAILED
echo ================================================================
echo Check the message above.
pause
exit /b 1
