"""
NeonClipping Auto-Uploader Module.
Connects NeonClipping to autonomous multi-platform publishing (YouTube Shorts & Instagram Reels).
Uses persistent browser sessions, strict SQLite idempotency (no duplicate uploads), and a 12-minute cadence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from uploader import auth, bot, browser_utils, instagram_uploader, ledger, youtube_uploader
import threading

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
_UPLOAD_LOCK = threading.Lock()


def check_auth_status() -> Dict[str, Any]:
    """Check authentication status of connected YouTube and Instagram accounts."""
    yt_ok, yt_msg = youtube_uploader.check_auth()
    ig_ok, ig_msg = instagram_uploader.check_auth()
    return {
        "youtube": {
            "account": "my-channel",
            "authenticated": yt_ok,
            "message": yt_msg,
            "profile_dir": str(youtube_uploader.DEFAULT_PROFILE_DIR),
        },
        "instagram": {
            "account": "ADG",
            "authenticated": ig_ok,
            "message": ig_msg,
            "profile_dir": str(instagram_uploader.DEFAULT_PROFILE_DIR),
        },
    }


def publish_clip(
    video_path: Path | str,
    title: str = "",
    hook: str = "",
    platforms: Optional[List[str]] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """
    Publish a single clip immediately to selected platforms ('youtube', 'instagram', or both).
    Checks idempotency before uploading.
    """
    path = Path(video_path)
    if not path.is_file():
        candidate = DATA_DIR / path.name
        if candidate.is_file():
            path = candidate
        else:
            return {"ok": False, "error": f"Clip file not found: {video_path}"}

    target_platforms = [p.lower() for p in (platforms or ["youtube", "instagram"])]
    if "both" in target_platforms or "all" in target_platforms:
        target_platforms = ["youtube", "instagram"]

    meta = ledger.get_clip_metadata(path)
    effective_title = title.strip() or meta["title"]
    caption = f"{effective_title}\n\n{hook}" if hook and hook != effective_title else (meta["caption"] or effective_title)

    results = {}

    with _UPLOAD_LOCK:
        if not browser_utils.acquire_global_upload_lock(timeout=120):
            return {"ok": False, "error": "Another upload is currently in progress."}
        try:
            if "youtube" in target_platforms:
                if ledger.is_uploaded(path, "youtube"):
                    results["youtube"] = {"ok": True, "status": "already_uploaded", "message": "Already uploaded (idempotency check passed)"}
                else:
                    yt_desc = meta.get("youtube_description") or f"{effective_title}\n\n#Shorts #Gaming #GTA #ViralShorts #Trending #Clips"
                    yt_res = youtube_uploader.upload_to_youtube(path, title=effective_title, description=yt_desc, headless=headless)
                    if yt_res.ok:
                        ledger.record_upload(path, "youtube", "success", title=effective_title, remote_url=yt_res.remote_url or "", remote_id=yt_res.remote_id or "")
                    else:
                        ledger.record_upload(path, "youtube", "failed", title=effective_title, error=yt_res.error or "")
                    results["youtube"] = {"ok": yt_res.ok, "url": yt_res.remote_url, "error": yt_res.error}

            if "instagram" in target_platforms:
                if ledger.is_uploaded(path, "instagram"):
                    results["instagram"] = {"ok": True, "status": "already_uploaded", "message": "Already uploaded (idempotency check passed)"}
                else:
                    ig_cap = meta.get("instagram_caption") or caption
                    ig_res = instagram_uploader.upload_to_instagram(path, caption=ig_cap, headless=headless)
                    if ig_res.ok:
                        ledger.record_upload(path, "instagram", "success", title=effective_title, remote_url=ig_res.remote_url or "", remote_id=ig_res.remote_id or "")
                    else:
                        ledger.record_upload(path, "instagram", "failed", title=effective_title, error=ig_res.error or "")
                    results["instagram"] = {"ok": ig_res.ok, "url": ig_res.remote_url, "error": ig_res.error}
        finally:
            browser_utils.release_global_upload_lock()

    overall_ok = any(r.get("ok") for r in results.values())
    return {
        "ok": overall_ok,
        "file": str(path.resolve()),
        "title": effective_title,
        "platforms": results,
    }


def publish_clip_async(
    video_path: Path | str,
    title: str = "",
    hook: str = "",
    platforms: Optional[List[str]] = None,
) -> str:
    """Trigger background upload of a single clip."""
    import threading
    t = threading.Thread(
        target=publish_clip,
        args=(video_path, title, hook, platforms),
        daemon=True,
    )
    t.start()
    return f"Publishing task dispatched in background for {Path(video_path).name}"


def start_scheduler_bot(interval_minutes: int = 12) -> str:
    """Start the 12-minute autonomous upload scheduler bot in background."""
    return bot.start_bot_thread(interval_seconds=interval_minutes * 60)


def stop_scheduler_bot() -> str:
    """Stop the autonomous upload scheduler bot."""
    return bot.stop_bot_thread()


def get_bot_status() -> Dict[str, Any]:
    """Get current status of upload bot and queue."""
    return bot.get_bot_status()


def load_history() -> List[Dict[str, Any]]:
    return ledger.list_uploads(limit=50)


if __name__ == "__main__":
    print("Testing uploader auth status...")
    status = check_auth_status()
    print(json.dumps(status, indent=2))
