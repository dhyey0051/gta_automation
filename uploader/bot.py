"""
uploader/bot.py — NeonClipping Autonomous 12-Minute Upload Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Continuously monitors data/ for rendered clips.
Every 12 minutes (720s), uploads the next clip to YouTube Shorts and Instagram Reels.
Enforces strict idempotency via SQLite ledger so NO video is ever uploaded twice.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from uploader import browser_utils, instagram_uploader, ledger, youtube_uploader

BASE_DIR = Path("G:/dhyey/automation/neonclipping-main")
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "bot_state.json"

DEFAULT_INTERVAL_SECONDS = 12 * 60  # 12 minutes (720 seconds)

_BOT_RUNNING = False
_BOT_THREAD: Optional[threading.Thread] = None
_STOP_EVENT = threading.Event()

_UPLOAD_PROGRESS: Dict[str, Any] = {
    "active": False,
    "clip": None,
    "current_platform": None,
    "youtube": {
        "status": "idle",
        "percent": 0,
        "stage": "Waiting for next upload cycle",
        "url": "",
    },
    "instagram": {
        "status": "idle",
        "percent": 0,
        "stage": "Waiting for next upload cycle",
        "url": "",
    },
}

_LAST_RUN_INFO: Dict[str, Any] = {
    "status": "idle",
    "last_check": None,
    "last_upload": None,
    "current_clip": None,
    "pending_count": 0,
    "interval_seconds": DEFAULT_INTERVAL_SECONDS,
    "seconds_remaining": 0,
    "next_upload_at": 0,
    "last_heartbeat": 0,
    "youtube_daily_limit": False,
    "upload_progress": _UPLOAD_PROGRESS,
}


def set_upload_progress(
    platform: Optional[str],
    percent: int,
    stage: str,
    status: str = "uploading",
    url: str = "",
    clip: Optional[str] = None,
) -> None:
    """Thread-safe upload progress updater exposed via /bot/status for real-time Web UI progress bars."""
    global _UPLOAD_PROGRESS
    if clip:
        _UPLOAD_PROGRESS["clip"] = clip
    if platform in ("youtube", "instagram"):
        _UPLOAD_PROGRESS["current_platform"] = platform
        _UPLOAD_PROGRESS[platform]["percent"] = max(0, min(100, percent))
        _UPLOAD_PROGRESS[platform]["stage"] = stage
        _UPLOAD_PROGRESS[platform]["status"] = status
        if url:
            _UPLOAD_PROGRESS[platform]["url"] = url
    _UPLOAD_PROGRESS["active"] = (
        _UPLOAD_PROGRESS["youtube"]["status"] == "uploading"
        or _UPLOAD_PROGRESS["instagram"]["status"] == "uploading"
    )
    _LAST_RUN_INFO["upload_progress"] = _UPLOAD_PROGRESS
    _save_bot_state()


def _save_bot_state() -> None:
    """Persist bot state to disk so the Web UI timer widget is always accurate."""
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_LAST_RUN_INFO, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as e:
        logger.debug(f"Failed to save bot state: {e}")


def process_next_clip(
    platforms: tuple[str, ...] = ("youtube", "instagram"),
    headless: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Find the oldest pending clip and upload it to the needed platforms.
    Guarantees no duplicate uploads.
    """
    pending = ledger.get_pending_clips(DATA_DIR, platforms=platforms)
    # Filter to only clips that need BOTH platforms (YouTube + Instagram)
    both_pending = [c for c in pending if all(p in c["needed_platforms"] for p in platforms)]
    if both_pending:
        pending = both_pending

    _LAST_RUN_INFO["pending_count"] = len(pending)
    _LAST_RUN_INFO["last_check"] = datetime.now(timezone.utc).isoformat()

    if not pending:
        logger.info("[Bot] No pending clips waiting to be uploaded.")
        _LAST_RUN_INFO["status"] = "waiting"
        return None

    clip = pending[0]
    clip_path = Path(clip["path"])
    needed = clip["needed_platforms"]
    
    # Generate full AI viral metadata for this specific clip
    full_meta = ledger.get_clip_metadata(clip_path, generate_ai=True)
    title = full_meta.get("title") or clip.get("title") or "Epic GTA 6 Moment 🔥"
    yt_description = full_meta.get("youtube_description") or f"{title}\n\n#Shorts #Gaming #GTA6 #RockstarGames #ViralShorts"
    ig_caption = full_meta.get("instagram_caption") or f"{title}\n.\n.\n#reels #gaming #viral #gta6 #gtavi #explorepage"

    stars = clip.get("stars", 3)
    logger.info(f"[Bot] Processing clip: {clip_path.name} (Priority: {stars} stars {'⭐'*stars})")
    logger.info(f"[Bot] Title: '{title}' | Needed platforms: {needed}")
    logger.info(f"[Bot] YouTube Description Preview: {yt_description[:80]}...")
    _LAST_RUN_INFO["status"] = "uploading"
    _LAST_RUN_INFO["current_clip"] = clip_path.name
    _LAST_RUN_INFO["seconds_remaining"] = 0
    _save_bot_state()

    # Initialize progress for both platforms
    set_upload_progress(
        "youtube",
        5 if "youtube" in needed else 100,
        "Queued for YouTube Shorts..." if "youtube" in needed else "Already uploaded to YouTube",
        status="uploading" if "youtube" in needed else "skipped",
        clip=clip_path.name,
    )
    set_upload_progress(
        "instagram",
        0 if "instagram" in needed else 100,
        "Waiting for YouTube stage..." if "instagram" in needed else "Already uploaded to Instagram",
        status="idle" if "instagram" in needed else "skipped",
        clip=clip_path.name,
    )

    if not browser_utils.acquire_global_upload_lock(timeout=60):
        logger.warning("[Bot] Another upload is currently in progress; skipping this cycle.")
        set_upload_progress("youtube", 0, "Upload postponed: Browser lock busy", status="idle")
        set_upload_progress("instagram", 0, "Upload postponed: Browser lock busy", status="idle")
        return None

    try:
        results = {}

        # 1. YouTube Upload
        if "youtube" in needed and "youtube" in platforms:
            logger.info(f"[Bot] Starting YouTube Shorts upload for {clip_path.name}...")
            def yt_cb(pct: int, msg: str, st: str = "uploading", u: str = ""):
                set_upload_progress("youtube", pct, msg, status=st, url=u, clip=clip_path.name)

            try:
                yt_res = youtube_uploader.upload_to_youtube(
                    clip_path,
                    title=title,
                    description=yt_description,
                    headless=headless,
                    progress_cb=yt_cb,
                )
                if yt_res.ok:
                    _LAST_RUN_INFO["youtube_daily_limit"] = False
                    set_upload_progress("youtube", 100, f"✅ Published Short! ({yt_res.remote_url or 'Live'})", status="success", url=yt_res.remote_url or "", clip=clip_path.name)
                    ledger.record_upload(
                        clip_path,
                        platform="youtube",
                        status="success",
                        title=title,
                        remote_url=yt_res.remote_url or "",
                        remote_id=yt_res.remote_id or "",
                    )
                    logger.success(f"[Bot] YouTube Shorts upload SUCCESS for {clip_path.name}!")
                else:
                    record_status = "daily_limit" if yt_res.status == "daily_limit" else "failed"
                    if yt_res.status == "daily_limit":
                        set_upload_progress("youtube", 100, "⚠️ Daily upload limit reached (Wait 24h or switch channel).", status="daily_limit", clip=clip_path.name)
                    else:
                        set_upload_progress("youtube", 0, f"❌ Failed: {yt_res.error or 'Upload error'}", status="failed", clip=clip_path.name)
                    ledger.record_upload(
                        clip_path,
                        platform="youtube",
                        status=record_status,
                        title=title,
                        error=yt_res.error or "Unknown error",
                    )
                    if yt_res.status == "daily_limit":
                        logger.warning(f"[Bot] YouTube channel reached daily upload limit! {yt_res.error}")
                        _LAST_RUN_INFO["youtube_daily_limit"] = True
                    else:
                        logger.error(f"[Bot] YouTube upload failed: {yt_res.error}")

                results["youtube"] = {
                    "ok": yt_res.ok,
                    "url": yt_res.remote_url,
                    "error": yt_res.error,
                }
            except Exception as e:
                logger.error(f"[Bot] Unexpected YouTube exception: {e}")
                set_upload_progress("youtube", 0, f"❌ Exception: {e}", status="failed", clip=clip_path.name)
                ledger.record_upload(clip_path, platform="youtube", status="failed", title=title, error=str(e))
                results["youtube"] = {"ok": False, "error": str(e)}

        # 2. Instagram Upload
        if "instagram" in needed and "instagram" in platforms:
            logger.info(f"[Bot] Starting Instagram Reels upload for {clip_path.name}...")
            def ig_cb(pct: int, msg: str, st: str = "uploading", u: str = ""):
                set_upload_progress("instagram", pct, msg, status=st, url=u, clip=clip_path.name)

            try:
                ig_res = instagram_uploader.upload_to_instagram(
                    clip_path,
                    caption=ig_caption,
                    headless=headless,
                    progress_cb=ig_cb,
                )
                if ig_res.ok:
                    set_upload_progress("instagram", 100, f"✅ Published Reel! ({ig_res.remote_url or 'Live'})", status="success", url=ig_res.remote_url or "", clip=clip_path.name)
                    ledger.record_upload(
                        clip_path,
                        platform="instagram",
                        status="success",
                        title=title,
                        remote_url=ig_res.remote_url or "",
                        remote_id=ig_res.remote_id or "",
                    )
                    logger.success(f"[Bot] Instagram Reels upload SUCCESS for {clip_path.name}!")
                else:
                    set_upload_progress("instagram", 0, f"❌ Failed: {ig_res.error or 'Upload error'}", status="failed", clip=clip_path.name)
                    ledger.record_upload(
                        clip_path,
                        platform="instagram",
                        status="failed",
                        title=title,
                        error=ig_res.error or "Unknown error",
                    )
                    logger.error(f"[Bot] Instagram upload failed: {ig_res.error}")

                results["instagram"] = {
                    "ok": ig_res.ok,
                    "url": ig_res.remote_url,
                    "error": ig_res.error,
                }
            except Exception as e:
                logger.error(f"[Bot] Unexpected Instagram exception: {e}")
                set_upload_progress("instagram", 0, f"❌ Exception: {e}", status="failed", clip=clip_path.name)
                ledger.record_upload(clip_path, platform="instagram", status="failed", title=title, error=str(e))
                results["instagram"] = {"ok": False, "error": str(e)}
    finally:
        browser_utils.release_global_upload_lock()
        _UPLOAD_PROGRESS["active"] = False
        _LAST_RUN_INFO["upload_progress"] = _UPLOAD_PROGRESS
        _save_bot_state()

    _LAST_RUN_INFO["last_upload"] = {
        "clip": clip_path.name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    _LAST_RUN_INFO["current_clip"] = None
    _LAST_RUN_INFO["status"] = "sleeping"
    _save_bot_state()

    return {
        "clip": clip_path.name,
        "path": str(clip_path.resolve()),
        "title": title,
        "results": results,
    }


def run_bot_loop(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    platforms: tuple[str, ...] = ("youtube", "instagram"),
    headless: bool = True,
) -> None:
    """Main scheduling loop running every `interval_seconds` (default 12 minutes)."""
    global _BOT_RUNNING
    _BOT_RUNNING = True
    _STOP_EVENT.clear()
    _LAST_RUN_INFO["interval_seconds"] = interval_seconds

    logger.info("=" * 65)
    logger.info("  NEONCLIPPING AUTONOMOUS UPLOADER BOT STARTED")
    logger.info(f"  Cadence: Uploads every {interval_seconds // 60} minutes ({interval_seconds}s)")
    logger.info(f"  Platforms: {', '.join(platforms)}")
    logger.info(f"  Mode: {'Headless' if headless else 'Headed'}")
    logger.info("=" * 65)

    try:
        while not _STOP_EVENT.is_set():
            logger.info(f"[Bot] Cycle started at {datetime.now().strftime('%H:%M:%S')}")
            _LAST_RUN_INFO["last_heartbeat"] = time.time()
            _save_bot_state()
            
            try:
                res = process_next_clip(platforms=platforms, headless=headless)
                if res:
                    logger.info(f"[Bot] Finished processing clip: {res['clip']}")
            except Exception as e:
                logger.error(f"[Bot] Error during cycle: {e}")

            # Sleep in 1-second increments so we can exit immediately if stop requested
            logger.info(f"[Bot] Next upload scheduled in {interval_seconds // 60} minutes ({interval_seconds}s)...")
            now_ts = time.time()
            _LAST_RUN_INFO["next_upload_at"] = now_ts + interval_seconds
            _LAST_RUN_INFO["seconds_remaining"] = interval_seconds
            _LAST_RUN_INFO["status"] = "sleeping"
            _save_bot_state()

            elapsed = 0
            while elapsed < interval_seconds and not _STOP_EVENT.is_set():
                time.sleep(1)
                elapsed += 1
                rem = max(0, interval_seconds - elapsed)
                _LAST_RUN_INFO["seconds_remaining"] = rem
                _LAST_RUN_INFO["last_heartbeat"] = time.time()
                if elapsed % 2 == 0:
                    _save_bot_state()
                if elapsed % 120 == 0 and elapsed < interval_seconds:
                    logger.info(f"[Bot] Countdown: {rem // 60} minutes until next check...")

    except (KeyboardInterrupt, SystemExit):
        logger.info("[Bot] Shutting down upload bot gracefully...")
    finally:
        _BOT_RUNNING = False
        _LAST_RUN_INFO["status"] = "paused"
        _LAST_RUN_INFO["seconds_remaining"] = 0
        _LAST_RUN_INFO["next_upload_at"] = 0
        _save_bot_state()
        logger.info("[Bot] Upload bot stopped.")


def start_bot_thread(
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    platforms: tuple[str, ...] = ("youtube", "instagram"),
    headless: bool = True,
) -> str:
    """Start the upload bot in a background thread."""
    global _BOT_THREAD
    if _BOT_RUNNING:
        return "Upload bot is already running."

    _STOP_EVENT.clear()
    _BOT_THREAD = threading.Thread(
        target=run_bot_loop,
        args=(interval_seconds, platforms, headless),
        daemon=True,
    )
    _BOT_THREAD.start()
    return f"Upload bot started in background (cadence: {interval_seconds // 60} minutes)."


def stop_bot_thread() -> str:
    """Stop the background upload bot thread."""
    global _BOT_RUNNING
    if not _BOT_RUNNING:
        return "Upload bot is not running."
    _STOP_EVENT.set()
    _BOT_RUNNING = False
    _LAST_RUN_INFO["status"] = "paused"
    _LAST_RUN_INFO["seconds_remaining"] = 0
    _LAST_RUN_INFO["next_upload_at"] = 0
    _save_bot_state()
    return "Stop signal sent to upload bot."


def get_bot_status() -> Dict[str, Any]:
    """Get current live status of upload bot, accurate countdown, and pending queue."""
    pending = ledger.get_pending_clips(DATA_DIR)
    history = ledger.list_uploads(limit=10)

    # Read from persisted bot state file if available (handles external process runner)
    disk_state = {}
    if STATE_FILE.is_file():
        try:
            disk_state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    info = {**_LAST_RUN_INFO, **disk_state}
    status = info.get("status", "idle")
    heartbeat = info.get("last_heartbeat", 0)
    now = time.time()

    # Active if in-process thread is running OR external process sent heartbeat within last 15s
    is_active = _BOT_RUNNING or (heartbeat > 0 and (now - heartbeat) < 15)

    # Accurately compute live remaining seconds
    next_at = info.get("next_upload_at", 0)
    if is_active and status == "sleeping" and next_at > now:
        seconds_remaining = max(0, int(next_at - now))
    elif is_active and status == "uploading":
        seconds_remaining = 0
    else:
        seconds_remaining = 0

    return {
        "running": is_active,
        "status": status if is_active else "paused",
        "current_clip": info.get("current_clip"),
        "seconds_remaining": seconds_remaining,
        "interval_seconds": info.get("interval_seconds", DEFAULT_INTERVAL_SECONDS),
        "interval_minutes": info.get("interval_seconds", DEFAULT_INTERVAL_SECONDS) // 60,
        "pending_count": len(pending),
        "pending_clips": [p["file_name"] for p in pending[:5]],
        "last_check": info.get("last_check"),
        "last_upload": info.get("last_upload"),
        "recent_history": history[:5],
        "upload_progress": info.get("upload_progress") or _UPLOAD_PROGRESS,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NeonClipping 12-Minute Upload Bot")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="Cadence interval in seconds (default: 720 = 12 mins)")
    parser.add_argument("--once", action="store_true", help="Process one pending clip immediately and exit")
    parser.add_argument("--platform", choices=["all", "youtube", "instagram"], default="all", help="Target platform(s)")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible headed mode")
    args = parser.parse_args()

    plat_tuple = ("youtube", "instagram") if args.platform == "all" else (args.platform,)

    if args.once:
        print("[Bot] Running single upload pass (--once)...")
        res = process_next_clip(platforms=plat_tuple, headless=not args.headed)
        print("Result:", res)
    else:
        run_bot_loop(interval_seconds=args.interval, platforms=plat_tuple, headless=not args.headed)
