@echo off
title YouTube Channel Verification - NeonClipping
cd /d "%~dp0"
echo ======================================================================
echo   YOUTUBE STUDIO 1-TIME PHONE VERIFICATION
echo ======================================================================
echo Opening YouTube Studio Feature Eligibility in Chrome...
echo.
echo 1. Check "Intermediate features"
echo 2. Click "Verify phone number" and enter SMS OTP
echo 3. Once verified, your daily upload limit unlocks from 2 to 100+ videos!
echo.
call .\.venv\Scripts\python.exe -c "from playwright.sync_api import sync_playwright; p = sync_playwright().start(); b = p.chromium.launch_persistent_context('G:/dhyey/automation/clipops/data/profiles/youtube__adg-cliping', headless=False, channel='chrome', args=['--start-maximized']); page = b.new_page(); page.goto('https://studio.youtube.com?approve_browser_access=true'); input('>>> Press ENTER here after phone/ID verification is complete...'); b.close(); p.stop()"
pause
