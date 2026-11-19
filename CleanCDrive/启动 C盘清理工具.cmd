@echo off
setlocal EnableExtensions
rem ============================================================
rem  C Drive Cleaner - launcher
rem  Finds a Python that actually has tkinter, then starts the GUI.
rem  A bare "pythonw" on PATH is NOT trusted: some Python builds
rem  ship without tkinter, and the app would die silently.
rem ============================================================

set "DIR=%~dp0"
set "SCRIPT=%DIR%CleanCDrive.pyw"
set "PYW="
set "TMPL=%TEMP%\cdc_cand_%RANDOM%%RANDOM%.txt"

if not exist "%SCRIPT%" (
    echo [ERROR] CleanCDrive.pyw not found next to this launcher.
    pause
    exit /b 1
)

rem ---------- build the candidate list ----------
> "%TMPL%" echo pyw
>> "%TMPL%" echo py
>> "%TMPL%" echo pythonw

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\pythonw.exe" >> "%TMPL%" echo %%D\pythonw.exe
)
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.*") do (
    if exist "%%D\pythonw.exe" >> "%TMPL%" echo %%D\pythonw.exe
)
for /d %%D in ("C:\Program Files\Python3*") do (
    if exist "%%D\pythonw.exe" >> "%TMPL%" echo %%D\pythonw.exe
)
for /d %%D in ("C:\Python3*") do (
    if exist "%%D\pythonw.exe" >> "%TMPL%" echo %%D\pythonw.exe
)

rem ---------- registry-registered installs ----------
rem  Deliberately deferred: spawning reg.exe costs time and some
rem  locked-down machines block it outright. Only needed when the
rem  common install locations above found nothing.
rem  (see :try_registry below)

rem ---------- pick the first candidate that can import tkinter ----------
for /f "usebackq delims=" %%P in ("%TMPL%") do (
    if not defined PYW call :probe "%%P"
)

if not defined PYW call :try_registry
del "%TMPL%" >nul 2>nul
del "%TMPL%.reg" >nul 2>nul

if not defined PYW goto :nopython

start "" "%PYW%" "%SCRIPT%"
if errorlevel 1 goto :failed
exit /b 0


:try_registry
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i "REG_SZ"') do call :emit "%%B"
for /f "tokens=2,*" %%A in ('reg query "HKCU\SOFTWARE\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i "REG_SZ"') do call :emit "%%B"
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\WOW6432Node\Python\PythonCore" /s /v ExecutablePath 2^>nul ^| findstr /i "REG_SZ"') do call :emit "%%B"
if exist "%TMPL%.reg" (
    for /f "usebackq delims=" %%P in ("%TMPL%.reg") do (
        if not defined PYW call :probe "%%P"
    )
)
exit /b


:emit
set "P=%~1"
if "%P%"=="" exit /b
call set "P=%%P:python.exe=pythonw.exe%%"
if exist "%P%" >> "%TMPL%.reg" echo %P%
exit /b


:probe
rem %1 = one candidate. Keep it only if tkinter imports cleanly.
"%~1" -c "import tkinter" >nul 2>nul
if errorlevel 1 exit /b
set "PYW=%~1"
exit /b


:failed
echo.
echo [ERROR] Failed to start the application.
echo Try running this in a terminal to see the reason:
echo     python "%SCRIPT%"
echo.
pause
exit /b 1


:nopython
echo.
echo  ============================================================
echo   Python was not found on this computer
echo  ============================================================
echo.
echo   This launcher starts CleanCDrive from Python source, so it
echo   needs Python 3.8 or newer with tkinter. Plain Windows does
echo   not include Python.
echo.
echo   Two options:
echo.
echo     1^) Download the prebuilt CleanCDrive.exe - no Python
echo        required. See the "Releases" page of this project.
echo.
echo     2^) Install Python from:
echo        https://www.python.org/downloads/windows/
echo        During setup, tick "Add python.exe to PATH".
echo.
pause
exit /b 1
