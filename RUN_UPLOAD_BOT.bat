@echo off
title NeonClipping 12-Minute Upload Bot
cd /d "%~dp0"
echo ======================================================================
echo   NEONCLIPPING AUTONOMOUS UPLOADER BOT (12-MINUTE CADENCE)
echo ======================================================================
echo   - Checks data/ folder every 12 minutes (720 seconds)
echo   - Uploads next unuploaded clip to YouTube Shorts & Instagram Reels
echo   - SQLite ledger prevents duplicate uploads (SHA-256 idempotency)
echo   - Press Ctrl+C anytime to stop gracefully
echo ======================================================================
echo.
call .\.venv\Scripts\python.exe -m uploader.bot --interval 720
pause
