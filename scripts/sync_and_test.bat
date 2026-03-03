@echo off
REM ============================================
REM   Sync and Test on Remote Win7 VM
REM ============================================
REM
REM Copia los archivos fuente a la VM y ejecuta
REM Para desarrollo rapido sin compilar
REM
REM ============================================

set VM_USER=usuario
set VM_HOST=localhost
set VM_PATH=C:\opcda2ua
set LOCAL_PATH=c:\Users\justino\Downloads\github\opcda2ua\openopc2

echo ============================================
echo   Syncing to VM...
echo ============================================
scp -r "%LOCAL_PATH%" %VM_USER%@%VM_HOST%:%VM_PATH%/

if %errorlevel% neq 0 (
    echo ERROR: Failed to sync files
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Files synced. Running on VM...
echo ============================================
echo.

REM Si se pasa un argumento, usarlo como servidor OPC
if "%1"=="" (
    ssh %VM_USER%@%VM_HOST% "cd %VM_PATH% && C:\python38\python.exe -m openopc2.ua_server --help"
) else (
    ssh %VM_USER%@%VM_HOST% "cd %VM_PATH% && C:\python38\python.exe -m openopc2.ua_server --opc-server %1 %2 %3 %4 %5"
)
