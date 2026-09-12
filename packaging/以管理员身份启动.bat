@echo off
rem ============================================================
rem  QQ Group File Bridge - Run AS ADMINISTRATOR
rem
rem  Why this exists:
rem  Logging in a QQ account requires NapCat to inject a hook DLL
rem  into the QQ NT process. Windows requires ADMIN rights for that,
rem  and without them the injection fails SILENTLY - the app looks
rem  completely unresponsive with no error message at all.
rem
rem  Use this launcher when "Get QR code" does nothing.
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist "QQGroupBridge.exe" (
  echo.
  echo  [ERROR] QQGroupBridge.exe not found in this folder.
  echo  Please keep the whole folder together.
  echo.
  pause
  exit /b 1
)

echo Requesting administrator privileges...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%~dp0QQGroupBridge.exe' -Verb RunAs"

if %ERRORLEVEL% neq 0 (
  echo.
  echo  [ERROR] Could not elevate. UAC was declined or blocked.
  echo.
  pause
)

endlocal
