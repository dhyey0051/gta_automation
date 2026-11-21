@echo off
title YouTube Studio Authentication - NeonClipping
cd /d "%~dp0"
echo ======================================================================
echo   NEONCLIPPING - YOUTUBE STUDIO AUTHENTICATION
echo ======================================================================
echo Launching Chrome with your persistent YouTube profile...
echo Sign in to your channel if needed, then press ENTER in this window.
echo.
call .\.venv\Scripts\python.exe -m uploader.auth --youtube
pause
