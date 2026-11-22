@echo off
title YouTube Channel 1-Time Verification - NeonClipping
cd /d "%~dp0"
echo ======================================================================
echo   YOUTUBE STUDIO 1-TIME ADVANCED VERIFICATION (VIDEO / ID)
echo ======================================================================
echo Opening YouTube Studio Feature Eligibility in Chrome...
echo.
echo STEPS TO UNLOCK DAILY UPLOADS:
echo 1. In the Chrome window that opens, expand "Advanced features".
echo 2. Click "ACCESS FEATURES" or "VERIFY".
echo 3. Choose "Use video verification" (30s selfie video on phone)
echo    OR "Use your valid ID" (photo of driver's license/ID).
echo 4. Scan the QR code or click the email link on your phone.
echo 5. Once submitted, YouTube unlocks the daily upload limit permanently!
echo.
echo Launching Chrome...
call .\.venv\Scripts\python.exe -c "import sys; from pathlib import Path; from uploader.browser_utils import clean_profile_locks; clean_profile_locks(Path('G:/dhyey/automation/clipops/data/profiles/youtube__adg-cliping')); from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch_persistent_context('G:/dhyey/automation/clipops/data/profiles/youtube__adg-cliping', headless=False, channel='chrome', args=['--start-maximized']); page = b.new_page(); page.goto('https://studio.youtube.com/channel/UCL8TpyqceY9uPhL_atdUw_w/settings/channel/eligibility?approve_browser_access=true'); input('>>> Press ENTER in this terminal when you have completed verification...'); b.close(); p.stop()"
pause
