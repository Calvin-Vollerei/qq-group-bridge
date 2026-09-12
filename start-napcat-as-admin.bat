@echo off
rem ============================================================
rem  Start the QQ component (NapCat) - MUST run as Administrator
rem
rem  HOW TO USE:
rem    RIGHT-CLICK this file  ->  "Run as administrator"
rem    (Self-elevation is intentionally NOT used: it depends on
rem     passing this file's path to PowerShell, which is fragile
rem     when the path contains non-ASCII characters.)
rem
rem  This script ALWAYS pauses and ALWAYS writes a log file, so the
rem  window can never "flash and disappear" without a trace.
rem  Log: napcat-start.log (next to this file)
rem
rem  ASCII-only on purpose: cmd.exe reads .bat using the console
rem  code page (GBK on Chinese Windows), not UTF-8.
rem ============================================================
setlocal
cd /d "%~dp0"

set "LOG=%~dp0napcat-start.log"
echo ==== run started %DATE% %TIME% ==== > "%LOG%"
echo cwd = %CD% >> "%LOG%"

net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo   [NOT ELEVATED]
    echo.
    echo   Please RIGHT-CLICK this file and choose:
    echo       "Run as administrator"
    echo.
    echo NOT_ELEVATED >> "%LOG%"
    echo.
    pause
    exit /b 1
)

echo ELEVATED_OK >> "%LOG%"
chcp 65001 >nul
title QQ Group File Bridge - NapCat (Administrator)

echo.
echo  ============================================================
echo   Administrator rights OK - starting the QQ component
echo   (full log: napcat-start.log)
echo  ============================================================
echo.

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] python not found on PATH.
    echo          Set up the dev environment first: pip install -r requirements.txt
    echo PYTHON_NOT_FOUND >> "%LOG%"
    echo.
    pause
    exit /b 1
)

echo python found: >> "%LOG%"
python --version >> "%LOG%" 2>&1

python -u scripts\start_napcat_hook.py --release --wait 120 >> "%LOG%" 2>&1
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
    echo   FAILED - exit code %RC%
    echo   Full log: %LOG%
)
echo.
pause
endlocal
