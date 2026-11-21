"""
uploader/browser_utils.py — Chrome Profile and Playwright Helpers
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Provides process cleanup, lockfile clearing, and DOM scrim neutralization.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from loguru import logger


def clean_profile_locks(profile_dir: Path | str) -> None:
    """
    Safely terminates any orphaned Chrome instances using this profile
    and removes stale Singleton lockfiles so Playwright launch never fails
    with ProcessSingleton error.
    """
    try:
        p_path = Path(profile_dir).resolve()
        p_name = p_path.name

        # 1. Kill any chrome process on Windows holding this user-data-dir
        if os.name == "nt":
            try:
                ps_cmd = (
                    f"Get-CimInstance Win32_Process -Filter \"name = 'chrome.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -like '*{p_name}*' }} | "
                    f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}"
                )
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True,
                    timeout=8,
                )
            except Exception as ps_err:
                logger.debug(f"[BrowserUtils] PowerShell process kill warning: {ps_err}")

        # 2. Delete Singleton lock files if present
        for lock_name in ["SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile"]:
            lf = p_path / lock_name
            if lf.exists():
                try:
                    lf.unlink()
                    logger.debug(f"[BrowserUtils] Removed stale lockfile: {lf}")
                except Exception:
                    pass
        time.sleep(0.5)
    except Exception as e:
        logger.debug(f"[BrowserUtils] Lock cleanup exception: {e}")


def disable_scrims(page) -> None:
    """
    Neutralizes pointer-intercepting dialog scrims, backdrops, and overlays in YouTube / Instagram.
    Allows clicks to reach contenteditable textboxes and buttons without 35s actionability timeouts.
    """
    try:
        page.evaluate("""() => {
            const selectors = [
                '.dialog-scrim',
                'tp-yt-iron-overlay-backdrop',
                'iron-overlay-backdrop',
                '.scrim',
                '.ytcp-uploads-dialog .dialog-scrim',
                'div[class*="dialog-scrim"]'
            ];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    el.style.pointerEvents = 'none';
                });
            });
        }""")
    except Exception:
        pass


GLOBAL_LOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "uploader_global.lock"


def acquire_global_upload_lock(timeout: float = 240.0) -> bool:
    """
    Acquire cross-process lock so only one browser upload runs at a time across all processes.
    """
    start_time = time.monotonic()
    GLOBAL_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    while time.monotonic() - start_time < timeout:
        try:
            fd = os.open(str(GLOBAL_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            logger.debug(f"[BrowserUtils] Acquired global lock (PID {os.getpid()})")
            return True
        except FileExistsError:
            # Check if existing lock is stale (process no longer alive)
            try:
                content = GLOBAL_LOCK_FILE.read_text(encoding="utf-8").strip()
                lock_pid = int(content) if content.isdigit() else None
                if lock_pid:
                    import psutil
                    if not psutil.pid_exists(lock_pid):
                        logger.warning(f"[BrowserUtils] Stale lock from dead PID {lock_pid} detected; removing.")
                        GLOBAL_LOCK_FILE.unlink(missing_ok=True)
                        continue
            except Exception:
                # If cannot read or parse, consider stale if > 5 minutes old
                try:
                    mtime = GLOBAL_LOCK_FILE.stat().st_mtime
                    if time.time() - mtime > 300:
                        GLOBAL_LOCK_FILE.unlink(missing_ok=True)
                        continue
                except Exception:
                    pass
            time.sleep(1.5)
    return False


def release_global_upload_lock() -> None:
    """Release cross-process upload lock."""
    try:
        GLOBAL_LOCK_FILE.unlink(missing_ok=True)
        logger.debug("[BrowserUtils] Released global upload lock")
    except Exception:
        pass

