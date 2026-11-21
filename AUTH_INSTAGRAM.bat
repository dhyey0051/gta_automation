@echo off
title Instagram Authentication - NeonClipping
cd /d "%~dp0"
echo ======================================================================
echo   NEONCLIPPING - INSTAGRAM AUTHENTICATION
echo ======================================================================
echo Launching Chrome with your persistent Instagram profile...
echo Sign in to your account if needed, then press ENTER in this window.
echo.
call .\.venv\Scripts\python.exe -m uploader.auth --instagram
pause
