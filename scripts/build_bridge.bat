@echo off
REM ============================================
REM   Build OPC DA-UA Bridge Executable (32-bit)
REM ============================================
REM
REM IMPORTANT: Builds 32-bit executable to be
REM compatible with gbda_aut.dll (32-bit COM)
REM
REM Requirements:
REM   - Python 3.11 32-bit
REM   - pyinstaller
REM   - asyncua
REM   - pywin32
REM
REM Output: dist\opcda2ua.exe
REM ============================================

echo ============================================
echo   Building OPC DA-UA Bridge (32-bit)
echo ============================================
echo.

REM Save current directory
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..

REM Python 32-bit paths (check common locations)
set PYTHON32=
if exist "%LOCALAPPDATA%\Programs\Python\Python311-32\python.exe" (
    set PYTHON32=%LOCALAPPDATA%\Programs\Python\Python311-32\python.exe
) else if exist "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe" (
    set PYTHON32=%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe
) else if exist "C:\Python311-32\python.exe" (
    set PYTHON32=C:\Python311-32\python.exe
) else if exist "C:\Python38-32\python.exe" (
    set PYTHON32=C:\Python38-32\python.exe
)

if "%PYTHON32%"=="" (
    echo ERROR: Python 32-bit not found!
    echo.
    echo Please install Python 3.11 32-bit from:
    echo https://www.python.org/ftp/python/3.11.9/python-3.11.9.exe
    echo.
    echo Make sure to select "32-bit" version during download.
    pause
    exit /b 1
)

echo Using Python 32-bit: %PYTHON32%
"%PYTHON32%" --version

REM Change to project root
cd /d "%PROJECT_ROOT%"

REM Install build dependencies
echo.
echo Installing build dependencies...
"%PYTHON32%" -m pip install pyinstaller asyncua pywin32

if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

REM Change to scripts directory and build
cd /d "%SCRIPT_DIR%"
echo.
echo Building executable...
"%PYTHON32%" -m PyInstaller --clean opcda2ua.spec

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
echo   Usage:
echo     opcda2ua.exe --opc-server "ServerName"
echo.
echo   For help:
echo     opcda2ua.exe --help
echo.
echo ============================================

REM Return to original directory
cd /d "%PROJECT_ROOT%"

pause
