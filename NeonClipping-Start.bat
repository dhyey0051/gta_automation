@echo off
setlocal
title NeonClipping
cd /d "%~dp0"

echo ===================================================
echo             NEONCLIPPING LOCAL AI CLIPPER
echo ===================================================
echo.
echo Checking environment...
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment not found! Run NeonClipping-Setup.bat first.
    pause
    exit /b 1
)

set "PORT=8770"
netstat -ano | findstr ":8770 " >nul 2>&1
if not errorlevel 1 (
    echo Port 8770 is busy, using 8771...
    set "PORT=8771"
)
set "NEONCLIPPING_PORT=%PORT%"

echo Starting Web UI on http://127.0.0.1:%PORT%...
start "" "http://127.0.0.1:%PORT%"

:: Auto-upload setting (1 to automatically publish newly rendered clips to YouTube Shorts & Instagram Reels)
if not defined NEONCLIPPING_AUTO_UPLOAD (
    set "NEONCLIPPING_AUTO_UPLOAD=1"
)

:: Model priority: DeepSeek before Gemini
if not defined NEONCLIPPING_MODEL (
    set "NEONCLIPPING_MODEL=deepseek"
)

echo.
echo [LLM Priority] DeepSeek (freemodel/deepseek-v4-pro-max) -> Fallback: Gemini
echo [Auto-Upload] Mode: %NEONCLIPPING_AUTO_UPLOAD% (YouTube: adg-cliping, Instagram: ADG)
echo NeonClipping running. Press Ctrl+C to stop.
echo.
".venv\Scripts\python.exe" app.py
pause
