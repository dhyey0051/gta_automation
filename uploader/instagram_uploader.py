"""
uploader/instagram_uploader.py — Resilient Instagram Reels Uploader
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Completely new, robust implementation addressing previous failure modes:
1. Clicks 'Create' -> Specifically selects 'Post' from dropdown menu.
2. Directly feeds file to input[type='file'] without relying on brittle OS dialogs.
3. Automatically sets 9:16 vertical aspect crop.
4. Walks through the two-step wizard (Crop -> Edit -> Details).
5. Inserts caption with viral tags into lexical contenteditable textbox.
6. Clicks Share and waits for "Your reel has been shared" confirmation.
7. Captures debug screenshots on any failure.
"""

from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright
from uploader.browser_utils import clean_profile_locks

if sys.platform == "win32":
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

DEFAULT_PROFILE_DIR = Path("G:/dhyey/automation/clipops/data/profiles/instagram__adg")
INSTAGRAM_URL = "https://www.instagram.com"
DEBUG_DIR = Path("G:/dhyey/automation/neonclipping-main/data/uploader_debug")
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"


@dataclass
class InstagramPublishResult:
    ok: bool
    remote_id: Optional[str] = None
    remote_url: Optional[str] = None
    status: str = "failed"
    error: Optional[str] = None
    needs_auth: bool = False


def check_auth(profile_path: Optional[Path | str] = None) -> tuple[bool, str]:
    """Check if Instagram profile is authenticated and active."""
    profile_dir = Path(profile_path) if profile_path else DEFAULT_PROFILE_DIR
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
            page.goto(INSTAGRAM_URL, wait_until="domcontentloaded")
            time.sleep(3)

            if "accounts/login" in page.url.lower():
                return False, f"Redirected to login: {page.url}"

            # Check for Create icon or Profile indicator
            create_icon = page.locator("svg[aria-label='New post'], svg[aria-label='Create']").first
            if create_icon.count() > 0:
                return True, "Authenticated (Instagram feed & Create button active)"

            return True, "Authenticated (Instagram session active)"
        except Exception as e:
            return False, f"Instagram auth check failed: {e}"
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass


def upload_to_instagram(
    video_path: Path | str,
    caption: str,
    *,
    profile_dir: Optional[Path | str] = None,
    headless: bool = True,
    retry_count: int = 1,
) -> InstagramPublishResult:
    """
    Upload a vertical 9:16 clip to Instagram Reels.
    Uses resilient direct file binding, menu traversal, and 9:16 aspect ratio selection.
    """
    path = Path(video_path)
    if not path.is_file():
        logger.error(f"[IG] Video file not found: {path}")
        return InstagramPublishResult(ok=False, error=f"File not found: {path}", status="file_missing")

    active_profile = Path(profile_dir) if profile_dir else DEFAULT_PROFILE_DIR
    if not active_profile.is_dir():
        logger.error(f"[IG] Profile directory not found: {active_profile}")
        return InstagramPublishResult(
            ok=False,
            error=f"Profile directory not found: {active_profile}. Run AUTH_INSTAGRAM.bat to sign in.",
            status="needs_auth",
            needs_auth=True,
        )

    # Format Caption: ensure hashtags
    formatted_caption = (caption or "").strip()
    if not "#reels" in formatted_caption.lower():
        formatted_caption = f"{formatted_caption}\n.\n.\n#reels #gaming #viral #shorts #trending #explore #fyp"
    formatted_caption = formatted_caption[:2200]

    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    for attempt in range(1, retry_count + 2):
        logger.info(f"[IG] Reel upload attempt {attempt}/{retry_count+1} for {path.name}")
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

                # 1. Navigate to Instagram (with retry for network hiccups)
                logger.info("[IG] Navigating to Instagram...")
                for nav_try in range(3):
                    try:
                        page.goto(INSTAGRAM_URL, wait_until="domcontentloaded", timeout=25000)
                        break
                    except Exception as nav_err:
                        logger.warning(f"[IG] Navigation retry {nav_try+1}: {nav_err}")
                        time.sleep(2)

                time.sleep(3)

                if "accounts/login" in page.url.lower():
                    logger.error("[IG] Login redirect detected. Session expired!")
                    browser.close()
                    return InstagramPublishResult(
                        ok=False,
                        error="Instagram session expired. Run AUTH_INSTAGRAM.bat to log in again.",
                        status="needs_auth",
                        needs_auth=True,
                    )

                # Dismiss notification / save login popups
                for not_now in page.locator("button:has-text('Not Now'), div[role='button']:has-text('Not Now')").all():
                    try:
                        if not_now.is_visible():
                            not_now.click()
                            time.sleep(1)
                    except Exception:
                        pass

                # 2. Click Create button
                logger.info("[IG] Locating Create button...")
                create_btn = page.locator(
                    "svg[aria-label='New post'], svg[aria-label='Create']"
                ).locator("xpath=ancestor::a | ancestor::div[@role='button']").first
                create_btn.wait_for(state="visible", timeout=15000)
                create_btn.click()
                time.sleep(1.5)

                # 3. Click 'Post' from the dropdown popover menu
                logger.info("[IG] Selecting 'Post' option from menu...")
                post_item = page.locator("text='Post'").first
                post_item.wait_for(state="visible", timeout=10000)
                post_item.click()
                time.sleep(2)

                # 4. Attach video file directly to input[type='file']
                logger.info(f"[IG] Uploading video file: {path.name}...")
                file_input = page.locator("input[type='file']").first
                file_input.wait_for(state="attached", timeout=15000)
                file_input.set_input_files(str(path.resolve()))
                logger.info("[IG] File attached, waiting for preview...")
                time.sleep(4)

                # Handle "Video posts are now shared as reels" notice if shown
                ok_notice = page.locator("button:has-text('OK'), div[role='button']:has-text('OK')").first
                if ok_notice.count() > 0 and ok_notice.is_visible():
                    try:
                        logger.info("[IG] Dismissing Reels notice...")
                        ok_notice.click()
                        time.sleep(1)
                    except Exception:
                        pass

                # 5. Aspect Ratio: Switch to 9:16 vertical crop
                logger.info("[IG] Setting 9:16 vertical aspect ratio...")
                crop_btn = page.locator("svg[aria-label='Select crop'], button:has(svg[aria-label='Select crop'])").first
                if crop_btn.count() > 0 and crop_btn.is_visible():
                    try:
                        crop_btn.click(force=True)
                        time.sleep(0.5)
                        ratio_9_16 = page.locator("button:has-text('9:16'), div[role='button']:has-text('9:16'), span:has-text('9:16')").first
                        if ratio_9_16.count() > 0:
                            ratio_9_16.click(force=True)
                            logger.info("[IG] 9:16 aspect ratio selected.")
                            time.sleep(0.5)
                    except Exception as crop_err:
                        logger.debug(f"[IG] Crop button adjustment skipped: {crop_err}")

                # 6. Click Next (Step 1: Crop -> Edit/Cover)
                logger.info("[IG] Wizard step 1: Clicking Next (Crop -> Edit)...")
                next_btn1 = page.locator("div[role='dialog'] button:has-text('Next'), div[role='dialog'] div[role='button']:has-text('Next')").first
                next_btn1.wait_for(state="visible", timeout=15000)
                try:
                    next_btn1.click(force=True, timeout=5000)
                except Exception:
                    page.evaluate("() => { const b = document.querySelector(\"div[role='dialog'] button:has-text('Next'), div[role='dialog'] div[role='button']:has-text('Next')\"); if(b) b.click(); }")
                time.sleep(3)

                # 7. Click Next (Step 2: Edit/Cover -> Details/Caption)
                logger.info("[IG] Wizard step 2: Clicking Next (Edit -> Details)...")
                next_btn2 = page.locator("div[role='dialog'] button:has-text('Next'), div[role='dialog'] div[role='button']:has-text('Next')").first
                next_btn2.wait_for(state="visible", timeout=15000)
                try:
                    next_btn2.click(force=True, timeout=5000)
                except Exception:
                    page.evaluate("() => { const b = document.querySelector(\"div[role='dialog'] button:has-text('Next'), div[role='dialog'] div[role='button']:has-text('Next')\"); if(b) b.click(); }")
                time.sleep(3)

                # 8. Insert Caption
                logger.info(f"[IG] Typing caption ({len(formatted_caption)} chars)...")
                caption_box = page.locator(
                    "div[aria-label='Write a caption...'], div[role='textbox'][contenteditable='true']"
                ).first
                caption_box.wait_for(state="visible", timeout=15000)
                try:
                    caption_box.click(force=True, timeout=4000)
                except Exception:
                    pass
                time.sleep(0.5)
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                try:
                    caption_box.fill(formatted_caption)
                except Exception:
                    try:
                        caption_box.press_sequentially(formatted_caption, delay=2)
                    except Exception:
                        page.keyboard.insert_text(formatted_caption)
                time.sleep(1.5)

                # 9. Click Share
                logger.info("[IG] Clicking Share button...")
                share_btn = page.locator(
                    "div[role='dialog'] button:has-text('Share'), div[role='dialog'] div[role='button']:has-text('Share')"
                ).first
                share_btn.wait_for(state="visible", timeout=15000)
                try:
                    share_btn.click(force=True, timeout=5000)
                except Exception:
                    page.evaluate("() => { const b = document.querySelector(\"div[role='dialog'] button:has-text('Share'), div[role='dialog'] div[role='button']:has-text('Share')\"); if(b) b.click(); }")

                # 10. Wait for confirmation ("Your reel has been shared" or modal close)
                logger.info("[IG] Waiting for upload completion confirmation (up to 360s)...")
                start_wait = time.monotonic()
                share_confirmed = False
                while time.monotonic() - start_wait < 360:
                    # Check for confirmation indicators
                    success_msg = page.locator(
                        "span:has-text('Your reel has been shared'), "
                        "span:has-text('Your post has been shared'), "
                        "div:has-text('Your reel has been shared'), "
                        "div:has-text('Your post has been shared'), "
                        "h2:has-text('Your reel has been shared'), "
                        "h2:has-text('Your post has been shared')"
                    ).first
                    if success_msg.count() > 0 and success_msg.is_visible():
                        logger.success(f"[IG] Reel successfully published for {path.name}!")
                        share_confirmed = True
                        break

                    # If the dialog disappeared completely, upload completed and dialog was closed
                    dialogs = page.locator("div[role='dialog']").all()
                    if len(dialogs) == 0:
                        logger.success(f"[IG] Upload dialog closed; Reel published for {path.name}!")
                        share_confirmed = True
                        break

                    time.sleep(2)

                if not share_confirmed:
                    raise PlaywrightTimeoutError("Timeout waiting for Instagram upload completion (360s exceeded)")

                # Try to extract URL or post link
                remote_url = None
                remote_id = None
                try:
                    view_link = page.locator("a:has-text('View post'), a[href*='/reel/'], a[href*='/p/']").first
                    if view_link.count() > 0:
                        href = view_link.get_attribute("href")
                        if href:
                            remote_url = href if href.startswith("http") else f"{INSTAGRAM_URL}{href}"
                            m = re.search(r"/(?:reel|p)/([a-zA-Z0-9_-]+)", remote_url)
                            if m:
                                remote_id = m.group(1)
                except Exception as link_err:
                    logger.debug(f"[IG] Could not extract post URL: {link_err}")

                browser.close()
                return InstagramPublishResult(
                    ok=True,
                    remote_url=remote_url,
                    remote_id=remote_id,
                    status="published",
                )

            except PlaywrightTimeoutError as e:
                logger.error(f"[IG] Timeout on attempt {attempt}: {e}")
                # Save screenshot for debugging
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                err_img = DEBUG_DIR / f"ig_error_timeout_{timestamp}.png"
                if browser:
                    try:
                        page.screenshot(path=str(err_img))
                        logger.info(f"[IG] Saved error screenshot: {err_img}")
                        browser.close()
                    except Exception:
                        pass
                if attempt <= retry_count:
                    time.sleep(5)
                    continue
                return InstagramPublishResult(ok=False, error=f"Timeout: {e}", status="timeout")

            except Exception as e:
                logger.error(f"[IG] Unexpected error on attempt {attempt}: {e}")
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                err_img = DEBUG_DIR / f"ig_error_{timestamp}.png"
                if browser:
                    try:
                        page.screenshot(path=str(err_img))
                        logger.info(f"[IG] Saved error screenshot: {err_img}")
                        browser.close()
                    except Exception:
                        pass
                if attempt <= retry_count:
                    time.sleep(5)
                    continue
                return InstagramPublishResult(ok=False, error=str(e), status="error")

    return InstagramPublishResult(ok=False, error=f"Failed after {retry_count} retries", status="failed")


if __name__ == "__main__":
    print("Checking Instagram auth status...")
    ok, msg = check_auth()
    print(f"Auth result: {ok} -> {msg}")
