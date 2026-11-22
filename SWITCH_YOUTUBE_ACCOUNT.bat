@echo off
title Switch YouTube Channel / Account - NeonClipping
cd /d "%~dp0"
echo ======================================================================
echo   NEONCLIPPING - SWITCH YOUTUBE CHANNEL / ACCOUNT
echo ======================================================================
echo 1. Chrome browser will open at your YouTube Channels / Google Login.
echo 2. Sign in to your new account OR select your new channel.
echo 3. Once your channel is selected and loaded, return here.
echo 4. Press ENTER to save and continue.
echo.
call .\.venv\Scripts\python.exe -m uploader.auth --switch
pause
