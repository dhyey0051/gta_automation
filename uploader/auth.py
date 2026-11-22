"""
uploader/auth.py — Interactive Browser Authentication Helper
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Launches a headed browser with the persistent session profile so the user
can sign in to YouTube Studio or Instagram Reels without manual profile setup.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from loguru import logger
from playwright.sync_api import sync_playwright

from uploader import instagram_uploader, youtube_uploader

YOUTUBE_STUDIO_URL = "https://studio.youtube.com"
INSTAGRAM_URL = "https://www.instagram.com"

BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
    "--start-maximized",
]


def launch_youtube_auth(profile_path: Path = youtube_uploader.DEFAULT_PROFILE_DIR) -> None:
    """Launch headed browser for YouTube Studio login."""
    profile_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Launching YouTube Studio auth session in: {profile_path}")
    print("\n" + "=" * 60)
    print("  YOUTUBE STUDIO AUTHENTICATION HELPER")
    print("=" * 60)
    print("1. A Chrome browser window will now open.")
    print("2. Sign in to your YouTube channel (e.g. adg-cliping).")
    print("3. Once YouTube Studio is loaded and ready, come back here.")
    print("4. Press ENTER in this window to save and exit.")
    print("=" * 60 + "\n")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            args=BROWSER_ARGS,
            no_viewport=True,
        )
        page = browser.new_page()
        page.set_default_timeout(120000)
        try:
            page.goto(YOUTUBE_STUDIO_URL, wait_until="domcontentloaded")
        except Exception:
            pass

        try:
            input(">>> Press ENTER here when you have completed logging in to YouTube Studio: ")
        except (KeyboardInterrupt, EOFError):
            pass

        print("\nVerifying YouTube Studio session...")
        time.sleep(2)
        try:
            if "studio.youtube.com" in page.url:
                print("SUCCESS: Logged in to YouTube Studio successfully!")
            else:
                print(f"Current page URL: {page.url}")
        except Exception:
            pass

        browser.close()
        print("Browser closed. Profile session is safely preserved.")


def launch_instagram_auth(profile_path: Path = instagram_uploader.DEFAULT_PROFILE_DIR) -> None:
    """Launch headed browser for Instagram login."""
    profile_path.mkdir(parents=True, exist_ok=True)
    logger.info(f"Launching Instagram auth session in: {profile_path}")
    print("\n" + "=" * 60)
    print("  INSTAGRAM AUTHENTICATION HELPER")
    print("=" * 60)
    print("1. A Chrome browser window will now open.")
    print("2. Log in to your Instagram account (e.g. ADG).")
    print("3. Save login credentials / dismiss 'Not Now' if prompted.")
    print("4. Once your Instagram feed is visible, come back here.")
    print("5. Press ENTER in this window to save and exit.")
    print("=" * 60 + "\n")

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_path),
            headless=False,
            args=BROWSER_ARGS,
            no_viewport=True,
        )
        page = browser.new_page()
        page.set_default_timeout(120000)
        try:
            page.goto(INSTAGRAM_URL, wait_until="domcontentloaded")
        except Exception:
            pass

        try:
            input(">>> Press ENTER here when you have completed logging in to Instagram: ")
        except (KeyboardInterrupt, EOFError):
            pass

        print("\nVerifying Instagram session...")
        time.sleep(2)
        try:
            if "accounts/login" not in page.url:
                print("SUCCESS: Logged in to Instagram successfully!")
            else:
                print(f"Current page URL: {page.url}")
        except Exception:
            pass

        browser.close()
        print("Browser closed. Profile session is safely preserved.")


def check_all() -> None:
    print("\n" + "=" * 50)
    print("  CHECKING UPLOADER AUTHENTICATION STATUS")
    print("=" * 50)
    yt_ok, yt_msg = youtube_uploader.check_auth()
    print(f"YouTube:   {'[OK]' if yt_ok else '[FAILED]'} -> {yt_msg}")

    ig_ok, ig_msg = instagram_uploader.check_auth()
    print(f"Instagram: {'[OK]' if ig_ok else '[FAILED]'} -> {ig_msg}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Uploader authentication helper")
    parser.add_argument("--youtube", action="store_true", help="Launch interactive YouTube login")
    parser.add_argument("--instagram", action="store_true", help="Launch interactive Instagram login")
    parser.add_argument("--check", action="store_true", help="Check headless auth status for all platforms")
    args = parser.parse_args()

    if args.youtube:
        launch_youtube_auth()
    elif args.instagram:
        launch_instagram_auth()
    elif args.check:
        check_all()
    else:
        check_all()
