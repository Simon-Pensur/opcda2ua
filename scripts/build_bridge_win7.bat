@echo off
REM ============================================
REM   Build OPC DA-UA Bridge for Windows 7
REM ============================================
REM
REM IMPORTANT: Run this script ON Windows 7!
REM
REM Windows 7 requires Python 3.8 (last supported version)
REM and may need Visual C++ Redistributable installed.
REM
REM Requirements:
REM   - Windows 7 SP1
REM   - Python 3.8.x (py -3.8)
REM   - Visual C++ Redistributable 2015-2019
REM
REM ============================================

echo ============================================
echo   Building for Windows 7
echo   IMPORTANT: Run this ON Windows 7!
echo ============================================
echo.

REM Save current directory
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

REM Change to project root
cd /d "%PROJECT_ROOT%"

REM Check for Python 3.8 specifically
echo Checking for Python 3.8...
py -3.8 --version
if %errorlevel% neq 0 (
    echo ERROR: Python 3.8 not found!
    echo.
    echo Windows 7 requires Python 3.8 ^(the last version with Win7 support^)
    echo Download from: https://www.python.org/downloads/release/python-3810/
    echo.
    pause
    exit /b 1
)

REM Install dependencies with Python 3.8
echo.
echo Installing dependencies with Python 3.8...
py -3.8 -m pip install --upgrade pip
py -3.8 -m pip install pyinstaller asyncua pywin32

if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

REM Build with Python 3.8
cd /d "%SCRIPT_DIR%"
echo.
echo Building executable with Python 3.8...
py -3.8 -m PyInstaller --clean opcda2ua.spec

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Build failed!
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build completed successfully!
echo ============================================
echo.
echo   Executable: %SCRIPT_DIR%dist\opcda2ua.exe
echo.
echo   IMPORTANT for Windows 7 deployment:
echo   Include vc_redist.x64.exe with the executable
echo   for systems without Visual C++ Runtime installed.
echo.
echo   Download VC++ Redistributable from:
echo   https://aka.ms/vs/16/release/vc_redist.x64.exe
echo.
echo ============================================

cd /d "%PROJECT_ROOT%"
pause
