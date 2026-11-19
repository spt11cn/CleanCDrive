@echo off
setlocal
rem ------------------------------------------------------------
rem  C Drive Cleaner - double click to launch
rem  Starts CleanCDrive.pyw with pythonw so no console stays open
rem ------------------------------------------------------------

set "DIR=%~dp0"
set "PYW=%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"

start "" "%PYW%" "%DIR%CleanCDrive.pyw"

if errorlevel 1 (
    echo Failed to launch. Try running:  python "%DIR%CleanCDrive.pyw"
    pause
)
endlocal
