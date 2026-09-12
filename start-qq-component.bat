@echo off
rem ============================================================
rem  Start the QQ component (embedded runtime) - NO admin needed
rem
rem  This package ships its own QQ NT runtime, so NapCat runs it
rem  directly with node.exe. That means:
rem    * no administrator rights required
rem    * your own QQ / TIM installation is NOT touched at all
rem    * it is not affected by your QQ version
rem
rem  (The older "hook" mode injected a DLL into your installed QQ,
rem   which DID require admin. Use start-napcat-as-admin.bat only
rem   if you deliberately want that mode.)
rem
rem  ASCII-only on purpose: cmd.exe reads .bat with the console code
rem  page, not UTF-8, so any non-ASCII here would be mangled.
rem  Log: napcat-start.log
rem ============================================================
setlocal
cd /d "%~dp0"

set "LOG=%~dp0napcat-start.log"
echo ==== run started %DATE% %TIME% ==== > "%LOG%"
echo cwd = %CD% >> "%LOG%"

chcp 65001 >nul
title QQ Group File Bridge - QQ component

echo.
echo  ============================================================
echo   Starting the QQ component (embedded runtime, no admin)
echo   Full log: napcat-start.log
echo  ============================================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] python not found on PATH.
    echo          Dev machine only - in the packaged release use the app GUI.
    echo PYTHON_NOT_FOUND >> "%LOG%"
    echo.
    pause
    exit /b 1
)

python -u scripts\start_napcat_hook.py --release --wait 150 >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo script_exit=%RC% >> "%LOG%"

echo.
echo  ---------------- output ----------------
type "%LOG%"
echo  ----------------------------------------
echo.

if "%RC%"=="0" (
    echo   SUCCESS - QR code is ready.
    echo   Next: open the app, go to "QQ login" tab, click "Get QR code".
) else (
    echo   FAILED - exit code %RC%   ^(see above^)
)
echo.
pause
endlocal
