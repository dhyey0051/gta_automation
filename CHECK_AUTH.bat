@echo off
title Check Uploader Status - NeonClipping
cd /d "%~dp0"
echo Checking YouTube & Instagram session health...
call .\.venv\Scripts\python.exe -m uploader.auth --check
pause
