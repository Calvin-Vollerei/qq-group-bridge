@echo off
rem ============================================================
rem  QQ Group File Bridge - one-click launcher
rem  (ASCII-only on purpose: .bat files are read with the console
rem   codepage, so non-ASCII text here would be garbled.)
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist "QQGroupBridge.exe" (
  echo.
  echo  [ERROR] QQGroupBridge.exe not found in this folder.
  echo.
  echo  Please keep the WHOLE folder together.
  echo  Do not move the .exe out of it - it needs the _internal folder.
  echo.
  pause
  exit /b 1
)

start "" "QQGroupBridge.exe"
endlocal
