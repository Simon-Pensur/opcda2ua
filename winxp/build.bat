@echo off
REM ============================================================
REM Build script for OPC DA to OPC UA Bridge - Legacy Edition
REM Requires Python 2.7 32-bit installed in C:\Python27
REM ============================================================

setlocal

set PYTHON27=C:\Python27\python.exe
set PIP27=C:\Python27\Scripts\pip.exe

REM Check Python 2.7
echo [1/5] Checking Python 2.7...
%PYTHON27% --version 2>nul
if errorlevel 1 (
    echo ERROR: Python 2.7 not found at %PYTHON27%
    echo Download from: https://www.python.org/downloads/release/python-2717/
    exit /b 1
)

REM Check architecture (must be 32-bit)
%PYTHON27% -c "import struct; bits=struct.calcsize('P')*8; print('%d-bit' % bits); exit(0 if bits==32 else 1)"
if errorlevel 1 (
    echo ERROR: Python 2.7 must be 32-bit for OPC DA compatibility
    exit /b 1
)

REM Install dependencies
echo.
echo [2/5] Installing dependencies...
%PIP27% install --upgrade pip setuptools 2>nul
%PIP27% install opcua==0.98.13 enum34 trollius futures
if errorlevel 1 (
    echo ERROR: Failed to install python-opcua dependencies
    exit /b 1
)

REM Install PyInstaller 3.6 (last version supporting Python 2.7)
echo.
echo [3/5] Installing PyInstaller 3.6...
%PIP27% install pyinstaller==3.6
if errorlevel 1 (
    echo ERROR: Failed to install PyInstaller
    exit /b 1
)

REM Check if OpenOPC is installed
echo.
echo [4/5] Checking OpenOPC...
%PYTHON27% -c "import OpenOPC; print('OpenOPC OK')" 2>nul
if errorlevel 1 (
    echo WARNING: OpenOPC not found. The exe will be built but may not find OpenOPC at runtime.
    echo Install OpenOPC 1.3.1 from: https://sourceforge.net/projects/openopc/files/openopc/1.3.1/
    echo.
)

REM Build executable
echo.
echo [5/5] Building executable...
%PYTHON27% -m PyInstaller --clean --noconfirm opcda2ua_legacy.spec
if errorlevel 1 (
    echo ERROR: Build failed
    exit /b 1
)

echo.
echo ============================================================
echo BUILD COMPLETE
echo Executable: dist\opcda2ua_legacy.exe
echo.
echo Copy this file to your Windows XP / Server 2003 machine.
echo Usage: opcda2ua_legacy.exe -s "YourOpcServer"
echo ============================================================

endlocal
