@echo off
rem ============================================================
rem  QQ Group File Bridge - environment self test
rem  Runs the app in headless mode and prints a diagnostic report.
rem  Use this when the GUI does not start, or after moving to a
rem  new computer.
rem ============================================================
setlocal
cd /d "%~dp0"
set "REPORT=%TEMP%\qgb-selftest.txt"

if not exist "QQGroupBridge.exe" (
  echo  [ERROR] QQGroupBridge.exe not found in this folder.
  pause
  exit /b 1
)

if exist "%REPORT%" del "%REPORT%" >nul 2>&1

echo Running self test, please wait...
start /wait "" "QQGroupBridge.exe" --selftest --out "%REPORT%"

echo.
echo ============================================================
if exist "%REPORT%" (
  type "%REPORT%"
) else (
  echo  [ERROR] Self test produced no report file.
  echo  The program may have failed to start at all.
)
echo ============================================================
echo.
pause
endlocal
