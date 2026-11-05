@echo off
chcp 65001 >nul
cd /d "%~dp0"
setlocal
set "PY=D:\Python313\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo  Activate Pico custom HID + verify link
echo  CDC serial port must be FREE - close Thonny / serial monitors
echo ============================================================
echo Python: %PY%
echo.

"%PY%" tools\activate.py %*
if errorlevel 1 goto :fail

echo.
echo -------- Verifying HID link --------
"%PY%" host\check_link.py
if errorlevel 1 goto :verifyfail
echo.
echo [OK] Activation and link check passed.
pause
exit /b 0

:fail
echo.
echo [FAILED] Activation incomplete. Read the message above.
echo   Common cause: CDC serial port busy.
echo   Close Thonny, or switch its interpreter to Local Python 3.
echo   Then double-click this file again.
echo.
pause
exit /b 1

:verifyfail
echo.
echo [WARN] HID activated but check_link.py failed. See output above.
pause
exit /b 1
