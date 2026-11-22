"""
uploader/youtube_uploader.py — Synchronous YouTube Shorts Uploader
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ported directly from the verified pipeline in YouTube-Shorts-Automation-main
with enhanced resilient fallback handling and remote URL extraction.
Uses persistent Playwright profile: data/profiles/youtube__adg-cliping.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from uploader.browser_utils import clean_profile_locks, disable_scrims

if sys.platform == "win32":
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# Default persistent profile paths
DEFAULT_PROFILE_DIR = Path("G:/dhyey/automation/clipops/data/profiles/youtube__adg-cliping")
ALT_PROFILE_DIR = Path("G:/dhyey/automation/clipops/data/profiles/youtube__adg-clip")
STUDIO_URL = "https://studio.youtube.com?approve_browser_access=true"
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"


def _save_debug_screenshot(page, prefix: str) -> Path:
    """Save debug screenshot for uploader troubleshooting."""
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    err_img = Path("G:/dhyey/automation/neonclipping-main/data/uploader_debug") / f"{prefix}_{timestamp}.png"
    err_img.parent.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(err_img))
        logger.info(f"[YT] Saved debug screenshot to {err_img.name}")
    except Exception:
        pass
    return err_img


def _is_daily_limit_reached(page) -> bool:
    """Check if YouTube has blocked uploads with 'Daily upload limit reached'."""
    try:
        if page.get_by_text("Daily upload limit reached").count() > 0:
            return True
        if page.locator("text=Daily upload limit reached").count() > 0:
            return True
        content = page.content().lower()
        if "daily upload limit" in content or "upload more videos daily after a one-time verification" in content:
            return True
    except Exception:
        pass
    return False


def _dismiss_interstitials(page) -> None:
    """Dismiss YouTube Studio 'Improve your experience' interstitial if present."""
    try:
        if "Improve your experience" in page.content():
            skip_link = page.locator("a:has-text('SKIP TO YOUTUBE STUDIO'), a:has-text('Skip to YouTube Studio')").first
            if skip_link.count() > 0 and skip_link.is_visible():
                logger.info("[YT] Dismissing 'Improve your experience' interstitial...")
                skip_link.click()
                page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass


@dataclass
class YouTubePublishResult:
    ok: bool
    remote_id: Optional[str] = None
    remote_url: Optional[str] = None
    status: str = "failed"
    error: Optional[str] = None
    needs_auth: bool = False


def _detect_login_redirect(url: str) -> bool:
    login_patterns = ("accounts.google.com", "ServiceLogin", "CheckCookie", "youtube.com/signin", "oauth")
    return any(p in url for p in login_patterns)


def _get_active_profile(profile_path: Optional[Path | str] = None) -> Path:
    if profile_path and Path(profile_path).is_dir():
        return Path(profile_path)
    if DEFAULT_PROFILE_DIR.is_dir():
        return DEFAULT_PROFILE_DIR
    if ALT_PROFILE_DIR.is_dir():
        return ALT_PROFILE_DIR
    return DEFAULT_PROFILE_DIR


def check_auth(profile_path: Optional[Path | str] = None) -> tuple[bool, str]:
    """Check if YouTube Studio is authenticated without manual login."""
    profile_dir = _get_active_profile(profile_path)
    if not profile_dir.is_dir():
        return False, f"Profile directory not found: {profile_dir}"

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                user_agent=DESKTOP_UA,
                channel="chrome",
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-popup-blocking",
                    "--mute-audio",
                ],
            )
            page = browser.new_page()
            page.set_default_timeout(25000)
            page.goto(STUDIO_URL, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            _dismiss_interstitials(page)

            if _detect_login_redirect(page.url) or "accounts.google.com" in page.url or "signin" in page.url:
                return False, f"Not signed in; redirected to Google login: {page.url}"

            # Check for YouTube Studio presence
            if "studio.youtube.com" in page.url:
                create_icon = page.locator("#create-icon, [aria-label*='Create'], ytcp-button:has-text('Create')").first
                if create_icon.count() > 0 or page.locator("#avatar-btn").count() > 0:
                    return True, "Authenticated (YouTube Studio active)"
                return True, "Authenticated (YouTube Studio loaded)"
            return False, f"Unexpected landing URL: {page.url}"
        except Exception as e:
            return False, f"YouTube auth check failed: {e}"
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


def upload_to_youtube(
    video_path: Path | str,
    title: str,
    description: str = "",
    *,
    profile_dir: Optional[Path | str] = None,
    schedule_date: str = "",
    schedule_time: str = "",
    headless: bool = True,
    retry_count: int = 2,
) -> YouTubePublishResult:
    """
    Uploads a video to YouTube Studio synchronously using the proven persistent Playwright profile.
    Adapted from YouTube-Shorts-Automation-main.
    """
    path = Path(video_path)
    if not path.is_file():
        logger.error(f"[YT] Video file not found: {path}")
        return YouTubePublishResult(ok=False, error=f"File not found: {path}", status="file_missing")

    active_profile = _get_active_profile(profile_dir)
    if not active_profile.is_dir():
        logger.error(f"[YT] Profile directory not found: {active_profile}")
        return YouTubePublishResult(
            ok=False,
            error=f"Profile directory not found: {active_profile}. Run AUTH_YOUTUBE.bat to login.",
            status="needs_auth",
            needs_auth=True,
        )

    # Format Title: Ensure #Shorts is present and keep under 100 characters
    formatted_title = title.strip()
    if not "#shorts" in formatted_title.lower():
        formatted_title = f"{formatted_title[:88].strip()} #Shorts"
    formatted_title = formatted_title[:100]

    # Format Description: Ensure relevant hashtags
    formatted_desc = description.strip()
    if not formatted_desc:
        formatted_desc = f"{formatted_title}\n\n#Shorts #Gaming #GTA #Viral #Trending #Clips"
    elif not "#shorts" in formatted_desc.lower():
        formatted_desc = f"{formatted_desc}\n\n#Shorts #Gaming #Viral"
    formatted_desc = formatted_desc[:5000]

    for attempt in range(1, retry_count + 2):
        logger.info(f"[YT] Upload attempt {attempt}/{retry_count+1} for {path.name}")
        clean_profile_locks(active_profile)
        browser = None
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=str(active_profile),
                    user_agent=DESKTOP_UA,
                    channel="chrome",
                    headless=headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-popup-blocking",
                        "--mute-audio",
                    ],
                )

                page = browser.new_page()
                page.set_default_timeout(35000)

                logger.info("[YT] Navigating to YouTube Studio...")
                page.goto(STUDIO_URL, wait_until="networkidle", timeout=35000)
                time.sleep(3)
                _dismiss_interstitials(page)

                if _detect_login_redirect(page.url) or "accounts.google.com" in page.url or "signin" in page.url:
                    logger.error("[YT] Session expired or not signed in! Manual re-auth required via AUTH_YOUTUBE.bat.")
                    browser.close()
                    return YouTubePublishResult(
                        ok=False,
                        error="YouTube session expired. Please run AUTH_YOUTUBE.bat to sign in.",
                        status="needs_auth",
                        needs_auth=True,
                    )

                # 1. Click Create -> Upload Videos
                logger.info("[YT] Opening upload modal...")
                modal_opened = False
                try:
                    create_btn = page.locator("#create-icon, ytcp-button#create-icon, [aria-label='Create']").first
                    create_btn.wait_for(state="visible", timeout=12000)
                    create_btn.click()
                    time.sleep(1)

                    upload_item = page.locator("#text-item-0, tp-yt-paper-item:has-text('Upload videos')").first
                    upload_item.wait_for(state="visible", timeout=10000)
                    upload_item.click()
                    modal_opened = True
                except PlaywrightTimeoutError:
                    logger.warning("[YT] Could not click Create button. Trying direct upload URL fallback.")

                if not modal_opened:
                    current_url = page.url.split("?")[0].rstrip("/")
                    if "/channel/" in current_url:
                        upload_url = f"{current_url}/videos/upload?d=sq"
                        logger.info(f"[YT] Navigating directly to {upload_url}")
                        page.goto(upload_url, wait_until="domcontentloaded")
                    else:
                        page.goto("https://studio.youtube.com/videos/upload?d=sq", wait_until="domcontentloaded")
                    time.sleep(3)

                # 2. Select file
                logger.info(f"[YT] Selecting video file: {path.name}")
                file_set = False
                # Try direct file input first (fast & reliable)
                file_input = page.locator("input[type='file']").first
                if file_input.count() > 0:
                    try:
                        file_input.set_input_files(str(path.resolve()))
                        file_set = True
                    except Exception as e:
                        logger.debug(f"[YT] Direct input set failed, using file_chooser: {e}")

                if not file_set:
                    with page.expect_file_chooser(timeout=25000) as fc_info:
                        select_btn = page.locator("ytcp-button#select-files-button, #select-files-button").first
                        select_btn.wait_for(state="visible", timeout=15000)
                        select_btn.click()
                    file_chooser = fc_info.value
                    file_chooser.set_files(str(path.resolve()))

                # Wait for upload modal to transition to details phase
                logger.info("[YT] Waiting for details modal...")
                page.wait_for_selector("ytcp-video-description, #title-textarea", state="visible", timeout=60000)
                time.sleep(2)
                disable_scrims(page)

                # 3. Details Step
                logger.info(f"[YT] Setting title: {formatted_title}")
                disable_scrims(page)
                title_box = page.locator("#title-textarea #textbox").first
                title_box.wait_for(state="visible", timeout=10000)
                try:
                    title_box.click(force=True, timeout=5000)
                except Exception:
                    pass
                # Direct JS set for instantaneous reliability
                try:
                    page.evaluate("""([val]) => {
                        const el = document.querySelector("#title-textarea #textbox");
                        if (el) {
                            el.focus();
                            el.textContent = val;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""", [formatted_title])
                except Exception:
                    pass
                # Also keyboard type to satisfy YouTube Studio event listeners
                try:
                    title_box.click(force=True, timeout=3000)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    title_box.type(formatted_title, delay=10)
                except Exception as title_err:
                    logger.debug(f"[YT] Title keyboard type notice: {title_err}")
                time.sleep(1)

                logger.info("[YT] Setting description...")
                disable_scrims(page)
                desc_box = page.locator("#description-textarea #textbox").first
                if desc_box.count() > 0:
                    try:
                        page.evaluate("""([val]) => {
                            const el = document.querySelector("#description-textarea #textbox");
                            if (el) {
                                el.focus();
                                el.textContent = val;
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                            }
                        }""", [formatted_desc])
                    except Exception:
                        pass
                    try:
                        desc_box.click(force=True, timeout=4000)
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        desc_box.type(formatted_desc, delay=5)
                    except Exception as desc_err:
                        logger.debug(f"[YT] Description keyboard type notice: {desc_err}")
                    time.sleep(1)

                # Audience: Not made for kids
                disable_scrims(page)
                kids_radio = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']").first
                if kids_radio.count() > 0:
                    is_already_checked = False
                    try:
                        is_already_checked = (
                            kids_radio.get_attribute("aria-checked") == "true" or
                            kids_radio.get_attribute("checked") is not None or
                            "iron-selected" in (kids_radio.get_attribute("class") or "")
                        )
                    except Exception:
                        pass
                    if not is_already_checked:
                        logger.info("[YT] Setting audience: Not made for kids...")
                        try:
                            kids_radio.scroll_into_view_if_needed()
                            kids_radio.click(force=True, timeout=5000)
                        except Exception:
                            page.evaluate("() => { const el = document.querySelector(\"tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']\"); if(el) el.click(); }")
                    else:
                        logger.info("[YT] Audience already set to Not made for kids.")
                time.sleep(1)

                # Check for daily upload limit right after details modal appears
                if _is_daily_limit_reached(page):
                    logger.error("[YT] YouTube Daily Upload Limit reached on this channel!")
                    _save_debug_screenshot(page, "yt_daily_limit")
                    browser.close()
                    return YouTubePublishResult(
                        ok=False,
                        status="daily_limit",
                        error="Daily upload limit reached on YouTube channel. Complete 1-time phone verification in YouTube Studio, or wait 24h.",
                    )

                # Next steps until Visibility (3 steps)
                logger.info("[YT] Advancing wizard steps...")
                advanced_all_steps = True
                for step_num in range(1, 4):
                    disable_scrims(page)
                    time.sleep(1)

                    if _is_daily_limit_reached(page):
                        logger.error(f"[YT] YouTube Daily Upload Limit reached at step {step_num}!")
                        _save_debug_screenshot(page, "yt_daily_limit")
                        browser.close()
                        return YouTubePublishResult(
                            ok=False,
                            status="daily_limit",
                            error="Daily upload limit reached on YouTube channel. Complete 1-time phone verification in YouTube Studio, or wait 24h.",
                        )

                    # Next button inside ytcp-uploads-dialog with 'Next' text
                    next_btn = page.locator("ytcp-uploads-dialog #next-button, #next-button").filter(has_text="Next").first
                    if next_btn.count() == 0 or not next_btn.is_visible():
                        next_btn = page.get_by_role("button", name="Next").first

                    if next_btn.count() > 0 and next_btn.is_visible():
                        try:
                            # Check if disabled
                            is_disabled = (
                                next_btn.get_attribute("aria-disabled") == "true"
                                or next_btn.get_attribute("disabled") is not None
                            )
                            if is_disabled:
                                logger.warning(f"[YT] Next button is disabled at step {step_num}")
                                if _is_daily_limit_reached(page):
                                    logger.error("[YT] Daily upload limit banner is blocking Next button!")
                                    _save_debug_screenshot(page, "yt_daily_limit")
                                    browser.close()
                                    return YouTubePublishResult(
                                        ok=False,
                                        status="daily_limit",
                                        error="Daily upload limit reached on YouTube channel. Complete 1-time phone verification in YouTube Studio, or wait 24h.",
                                    )
                                advanced_all_steps = False
                                break

                            next_btn.click(force=True, timeout=5000)
                            logger.info(f"[YT] Clicked Next step {step_num}")
                        except Exception as click_err:
                            logger.warning(f"[YT] Failed clicking Next button: {click_err}")
                            page.evaluate("() => { const b = document.querySelector('ytcp-uploads-dialog #next-button'); if(b) b.click(); }")
                    else:
                        logger.warning(f"[YT] Next button not visible at step {step_num}")
                        if _is_daily_limit_reached(page):
                            logger.error("[YT] Daily upload limit banner detected!")
                            _save_debug_screenshot(page, "yt_daily_limit")
                            browser.close()
                            return YouTubePublishResult(
                                ok=False,
                                status="daily_limit",
                                error="Daily upload limit reached on YouTube channel. Complete 1-time phone verification in YouTube Studio, or wait 24h.",
                            )
                        advanced_all_steps = False
                        break
                    time.sleep(2)

                if not advanced_all_steps:
                    _save_debug_screenshot(page, "yt_wizard_step_failed")
                    browser.close()
                    return YouTubePublishResult(
                        ok=False,
                        status="failed",
                        error="Could not advance to Visibility step. Next button was disabled or hidden.",
                    )

                # 4. Visibility / Schedule Step
                disable_scrims(page)
                if schedule_date and schedule_time:
                    logger.info(f"[YT] Scheduling for {schedule_date} at {schedule_time}...")
                    schedule_radio = page.locator("tp-yt-paper-radio-button[name='SCHEDULE']").first
                    schedule_radio.wait_for(state="visible", timeout=15000)
                    try:
                        schedule_radio.click(force=True, timeout=5000)
                    except Exception:
                        page.evaluate("() => { const el = document.querySelector(\"tp-yt-paper-radio-button[name='SCHEDULE']\"); if(el) el.click(); }")
                    time.sleep(1)

                    date_input = page.locator("ytcp-text-dropdown-trigger#datepicker-trigger input").first
                    date_input.click(force=True)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    date_input.type(schedule_date, delay=40)
                    page.keyboard.press("Enter")
                    time.sleep(1)

                    time_input = page.locator("ytcp-text-dropdown-trigger#time-of-day-trigger input").first
                    time_input.click(force=True)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    time_input.type(schedule_time, delay=40)
                    page.keyboard.press("Enter")
                    time.sleep(1)

                    logger.info("[YT] Clicking Schedule button...")
                    publish_btn = page.locator("ytcp-uploads-dialog #done-button, #done-button").first
                    try:
                        publish_btn.click(force=True, timeout=5000)
                    except Exception:
                        page.evaluate("() => { const b = document.querySelector('ytcp-uploads-dialog #done-button, #done-button'); if(b) b.click(); }")
                else:
                    logger.info("[YT] Setting visibility to Public...")
                    public_radio = page.locator("tp-yt-paper-radio-button[name='PUBLIC']").first
                    if public_radio.count() == 0 or not public_radio.is_visible():
                        public_radio = page.get_by_role("radio", name="Public").first
                    if public_radio.count() == 0 or not public_radio.is_visible():
                        public_radio = page.locator("[name='PUBLIC'], [aria-label*='Public']").first

                    public_radio.wait_for(state="visible", timeout=15000)
                    try:
                        public_radio.click(force=True, timeout=5000)
                    except Exception:
                        page.evaluate("() => { const el = document.querySelector(\"tp-yt-paper-radio-button[name='PUBLIC']\"); if(el) el.click(); }")
                    time.sleep(1)

                    logger.info("[YT] Clicking Publish button...")
                    disable_scrims(page)
                    publish_btn = page.locator("ytcp-uploads-dialog #done-button, #done-button").filter(has_text="Publish").first
                    if publish_btn.count() == 0 or not publish_btn.is_visible():
                        publish_btn = page.locator("ytcp-uploads-dialog #done-button, #done-button").first
                    if publish_btn.count() == 0 or not publish_btn.is_visible():
                        publish_btn = page.get_by_role("button", name="Publish").first

                    try:
                        publish_btn.click(force=True, timeout=5000)
                    except Exception:
                        page.evaluate("() => { const b = document.querySelector('ytcp-uploads-dialog #done-button, #done-button'); if(b) b.click(); }")

                # 5. Wait for REAL upload confirmation (share dialog or video published indicator)
                logger.info("[YT] Waiting for upload success confirmation (up to 360s)...")
                start_wait = time.monotonic()
                confirmed = False
                remote_url = None
                remote_id = None

                while time.monotonic() - start_wait < 360:
                    # Check for share dialog
                    share_dialog = page.locator("ytcp-video-share-dialog").first
                    if share_dialog.count() > 0 and share_dialog.is_visible():
                        logger.success("[YT] Share dialog appeared! Video published successfully.")
                        confirmed = True
                        try:
                            share_link = share_dialog.locator("a[href*='youtu.be'], a[href*='youtube.com/watch'], a.ytcp-video-info").first
                            if share_link.count() > 0:
                                remote_url = share_link.get_attribute("href")
                        except Exception:
                            pass
                        # Close share dialog
                        close_btn = share_dialog.locator("ytcp-button#close-button, button:has-text('Close')").first
                        if close_btn.count() > 0 and close_btn.is_visible():
                            try:
                                close_btn.click()
                            except Exception:
                                pass
                        break

                    # Check for "Video published" or "Short published" confirmation text
                    if page.get_by_text("Video published").count() > 0 or page.get_by_text("Short published").count() > 0:
                        logger.success("[YT] Video/Short published confirmation detected!")
                        confirmed = True
                        break

                    # Check if the upload dialog completely disappeared after publish was clicked
                    upload_dialog = page.locator("ytcp-uploads-dialog").first
                    if upload_dialog.count() == 0 or not upload_dialog.is_visible():
                        logger.success("[YT] Upload dialog dismissed after publishing!")
                        confirmed = True
                        break

                    time.sleep(3)

                if not confirmed:
                    err_img = _save_debug_screenshot(page, "yt_upload_timeout")
                    raise PlaywrightTimeoutError(f"Timeout waiting for YouTube upload confirmation (360s exceeded). Debug saved to {err_img.name}")

                # Extract video URL if not already captured
                if not remote_url:
                    try:
                        share_link = page.locator("a[href*='youtu.be'], a[href*='youtube.com/watch'], a.ytcp-video-info").first
                        if share_link.count() > 0:
                            remote_url = share_link.get_attribute("href")
                    except Exception as e:
                        logger.debug(f"[YT] Could not extract link: {e}")

                if remote_url:
                    m = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", remote_url) or re.search(r"v=([a-zA-Z0-9_-]+)", remote_url)
                    if m:
                        remote_id = m.group(1)

                logger.success(f"[YT] Successfully uploaded to YouTube Shorts: {remote_url or path.name}")
                browser.close()
                return YouTubePublishResult(
                    ok=True,
                    remote_url=remote_url,
                    remote_id=remote_id,
                    status="published",
                )

            except PlaywrightTimeoutError as e:
                logger.error(f"[YT] Timeout on attempt {attempt}: {e}")
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
                if attempt <= retry_count:
                    time.sleep(5)
                    continue
                return YouTubePublishResult(ok=False, error=f"Timeout: {e}", status="timeout")

            except Exception as e:
                logger.error(f"[YT] Error on attempt {attempt}: {e}")
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
                if attempt <= retry_count:
                    time.sleep(5)
                    continue
                return YouTubePublishResult(ok=False, error=str(e), status="error")

    return YouTubePublishResult(ok=False, error=f"Failed after {retry_count} retries", status="failed")


if __name__ == "__main__":
    print("Testing YouTube Studio auth...")
    ok, msg = check_auth()
    print(f"Auth result: {ok} -> {msg}")
