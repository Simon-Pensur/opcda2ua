@echo off
REM ============================================
REM   Build OPC DA-UA Bridge Executable
REM ============================================
REM
REM This script builds a standalone executable
REM for the OPC DA to OPC UA bridge server.
REM
REM Requirements:
REM   - Python 3.8+
REM   - pyinstaller
REM   - asyncua
REM   - openopc2 with all dependencies
REM
REM Output: dist\OpcDaUaBridge.exe
REM ============================================

echo ============================================
echo   Building OPC DA-UA Bridge
echo ============================================
echo.

REM Save current directory
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

REM Change to project root
cd /d "%PROJECT_ROOT%"

REM Check for virtual environment and activate if exists
if exist .venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call .venv\Scripts\activate.bat
) else if exist venv\Scripts\activate.bat (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

REM Check Python version
python --version
if %errorlevel% neq 0 (
    echo ERROR: Python not found in PATH
    pause
    exit /b 1
)

REM Install build dependencies
echo.
echo Installing build dependencies...
pip install pyinstaller asyncua

if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

REM Change to scripts directory and build
cd /d "%SCRIPT_DIR%"
echo.
echo Building executable...
pyinstaller --clean OpcDaUaBridge.spec

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
echo   Executable: %SCRIPT_DIR%dist\OpcDaUaBridge.exe
echo.
echo   Usage:
echo     OpcDaUaBridge.exe --opc-server "ServerName" --opc-host localhost
echo.
echo   For help:
echo     OpcDaUaBridge.exe --help
echo.
echo ============================================

REM Return to original directory
cd /d "%PROJECT_ROOT%"

pause
