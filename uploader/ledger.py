"""
uploader/ledger.py — NeonClipping Upload Ledger & Idempotency Tracker
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tracks uploaded videos in SQLite and JSON so NO video is ever uploaded twice.
Supports multi-platform tracking (YouTube Shorts, Instagram Reels).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from uploader import ai_metadata

BASE_DIR = Path("G:/dhyey/automation/neonclipping-main")
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "upload_ledger.db"
JSON_HISTORY_PATH = DATA_DIR / "upload_history.json"

_DB_LOCK = threading.Lock()


def get_file_hash(file_path: Path | str) -> str:
    """Calculate SHA256 hash of a file efficiently (chunked)."""
    p = Path(file_path)
    if not p.is_file():
        return ""
    hasher = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db() -> None:
    """Initialize SQLite database and schema."""
    with _DB_LOCK:
        conn = _get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT,
                    remote_url TEXT,
                    remote_id TEXT,
                    error TEXT,
                    uploaded_at TEXT NOT NULL
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash_platform ON uploads(file_hash, platform);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON uploads(status);")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS clip_preferences (
                    clip_id TEXT PRIMARY KEY,
                    file_name TEXT,
                    stars INTEGER DEFAULT 3,
                    updated_at TEXT
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_clip_stars ON clip_preferences(stars);")
            conn.commit()
        finally:
            conn.close()


init_db()

MEMORY_FILE = DATA_DIR / "upload_memory.json"


def _load_memory() -> Dict[str, Any]:
    if MEMORY_FILE.is_file():
        try:
            return json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_memory(mem: Dict[str, Any]) -> None:
    try:
        MEMORY_FILE.write_text(json.dumps(mem, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[Ledger] Could not save upload memory: {e}")


def is_uploaded(file_path: Path | str, platform: str) -> bool:
    """
    Check if a file has ALREADY been successfully uploaded to the given platform.
    Uses 3-layer persistent memory:
    1. upload_memory.json
    2. Companion <stem>.json sidecar
    3. SQLite database
    Guarantees NO reel is ever uploaded twice.
    """
    path = Path(file_path)
    if not path.is_file():
        return False

    fhash = get_file_hash(path)
    if not fhash:
        return False

    platform = platform.lower().strip()

    # Layer 1: Check persistent memory JSON file
    mem = _load_memory()
    for key in (fhash, path.name, path.stem):
        if key in mem:
            p_data = mem[key].get("platforms", {}).get(platform, {})
            if isinstance(p_data, dict) and p_data.get("status") in ("success", "published", "skipped"):
                return True
            if p_data is True or p_data == "success":
                return True

    # Layer 2: Check companion <stem>.json sidecar
    json_path = path.with_suffix(".json")
    if json_path.is_file():
        try:
            jdata = json.loads(json_path.read_text(encoding="utf-8"))
            up = jdata.get("uploaded", {})
            if isinstance(up, dict) and up.get(platform) in ("success", True, "published", "skipped"):
                return True
        except Exception:
            pass

    # Layer 3: SQLite database check
    with _DB_LOCK:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM uploads WHERE (file_hash = ? OR file_name = ?) AND platform = ? AND status = 'success' LIMIT 1;",
                (fhash, path.name, platform),
            )
            if cur.fetchone() is not None:
                return True

            # If there are >= 5 failed attempts for this file, stop retrying to prevent spam
            cur.execute(
                "SELECT COUNT(*) FROM uploads WHERE (file_hash = ? OR file_name = ?) AND platform = ? AND status = 'failed';",
                (fhash, path.name, platform),
            )
            failed_count = cur.fetchone()[0]
            if failed_count >= 5:
                logger.warning(f"[Ledger] {path.name} reached max (5) failed attempts on {platform}; skipping.")
                return True
        finally:
            conn.close()

    return False


def record_upload(
    file_path: Path | str,
    platform: str,
    status: str,
    title: str = "",
    remote_url: str = "",
    remote_id: str = "",
    error: str = "",
) -> None:
    """Record an upload result across SQLite, upload_memory.json, and companion metadata."""
    path = Path(file_path)
    fhash = get_file_hash(path) if path.is_file() else ""
    fsize = path.stat().st_size if path.is_file() else 0
    now_iso = datetime.now(timezone.utc).isoformat()
    platform = platform.lower().strip()
    status = status.lower().strip()

    # 1. Write to SQLite
    with _DB_LOCK:
        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT INTO uploads (file_hash, file_name, file_path, file_size, platform, status, title, remote_url, remote_id, error, uploaded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    fhash,
                    path.name,
                    str(path.resolve()),
                    fsize,
                    platform,
                    status,
                    title,
                    remote_url,
                    remote_id,
                    error,
                    now_iso,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # 2. Update persistent memory JSON file
    try:
        mem = _load_memory()
        key = fhash or path.name
        if key not in mem:
            mem[key] = {"file_name": path.name, "file_hash": fhash, "platforms": {}}
        mem[key]["platforms"][platform] = {
            "status": status,
            "title": title,
            "remote_url": remote_url,
            "remote_id": remote_id,
            "uploaded_at": now_iso,
            "error": error,
        }
        _save_memory(mem)
    except Exception as mem_err:
        logger.warning(f"[Ledger] Memory sync failed: {mem_err}")

    # 3. Update companion <stem>.json sidecar
    json_path = path.with_suffix(".json")
    if json_path.is_file():
        try:
            jdata = json.loads(json_path.read_text(encoding="utf-8"))
            if "uploaded" not in jdata or not isinstance(jdata["uploaded"], dict):
                jdata["uploaded"] = {}
            jdata["uploaded"][platform] = status
            jdata["uploaded"][f"{platform}_at"] = now_iso
            json_path.write_text(json.dumps(jdata, indent=2), encoding="utf-8")
        except Exception:
            pass

    # Sync to JSON history for UI inspection
    _sync_json_history()


def _sync_json_history() -> None:
    try:
        entries = list_uploads(limit=200)
        JSON_HISTORY_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[Ledger] Could not sync JSON history: {e}")


def list_uploads(limit: int = 100, platform: Optional[str] = None) -> List[Dict[str, Any]]:
    """List recent upload history."""
    with _DB_LOCK:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            if platform:
                cur.execute(
                    "SELECT * FROM uploads WHERE platform = ? ORDER BY id DESC LIMIT ?;",
                    (platform.lower().strip(), limit),
                )
            else:
                cur.execute("SELECT * FROM uploads ORDER BY id DESC LIMIT ?;", (limit,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def is_vertical_clip(mp4_path: Path) -> bool:
    """
    Check if a video file is a true 9:16 vertical short/reel (height > width).
    Filters out raw stream downloads and horizontal intermediate files.
    Strictly enforces:
    - Duration <= 90s (Shorts/Reels cap)
    - Aspect ratio height > width (>= 1.3 ratio)
    - File size <= 150 MB
    """
    try:
        if mp4_path.stat().st_size > 150 * 1024 * 1024:
            return False
        cmd = ["ffprobe", "-v", "error", "-show_entries", "stream=width,height,duration:format=duration", "-of", "json", str(mp4_path)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        data = json.loads(r.stdout)

        # Reject any video longer than 90 seconds (Shorts/Reels limit)
        dur = float(data.get("format", {}).get("duration") or 0)
        if dur > 90.0:
            return False

        for s in data.get("streams", []):
            w = s.get("width")
            h = s.get("height")
            if w and h:
                return h > w and (h / w) >= 1.3
    except Exception:
        pass
    return False


def is_generic_title(title: str) -> bool:
    """Check if a title is a raw hash, placeholder, or generic fallback."""
    t = (title or "").strip().lower()
    if not t or len(t) < 4:
        return True
    if re.search(r"^[a-f0-9]{10,}", t):
        return True
    generic_prefixes = (
        "activity highlight",
        "clip ",
        "viral gaming moment",
        "uploaded video",
        "vid",
        "video",
        "highlight",
    )
    return any(t.startswith(gp) for gp in generic_prefixes)


def get_clip_metadata(mp4_path: Path | str, generate_ai: bool = True) -> Dict[str, str]:
    """
    Extract or generate high-engagement viral title and rich descriptions.
    Uses companion JSON if valid, or calls local AI (DeepSeek / Gemini) to create
    engaging hooks and descriptions with hashtags. NEVER uses raw IDs.
    """
    path = Path(mp4_path)
    meta = {
        "title": "",
        "hook": "",
        "youtube_description": "",
        "instagram_caption": "",
        "caption": "",
    }

    # 1. Check for existing companion <stem>.json
    json_path = path.with_suffix(".json")
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            settings = data.get("settings", {})
            meta["title"] = data.get("title", "") or settings.get("title", "")
            meta["hook"] = data.get("hook", "") or settings.get("hook", "")
            meta["youtube_description"] = data.get("youtube_description", "")
            meta["instagram_caption"] = data.get("instagram_caption", "")
            meta["caption"] = data.get("post_caption", "") or settings.get("post_caption", "")
        except Exception as e:
            logger.debug(f"[Ledger] Error reading {json_path}: {e}")

    # 2. If title is generic or descriptions are missing, generate via AI (if requested)
    if is_generic_title(meta["title"]) or not meta["youtube_description"] or not meta["instagram_caption"]:
        if generate_ai:
            logger.info(f"[Ledger] Generating AI viral metadata for {path.name}...")
            ai_res = ai_metadata.generate_metadata_for_clip(path, context_hint=meta["title"])
            meta["title"] = ai_res["title"]
            meta["hook"] = ai_res["hook"]
            meta["youtube_description"] = ai_res["youtube_description"]
            meta["instagram_caption"] = ai_res["instagram_caption"]
            meta["caption"] = ai_res["instagram_caption"]

            # Persist to <stem>.json so it's cached and never re-generated
            try:
                existing = {}
                if json_path.is_file():
                    try:
                        existing = json.loads(json_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                existing.update({
                    "title": meta["title"],
                    "hook": meta["hook"],
                    "youtube_description": meta["youtube_description"],
                    "instagram_caption": meta["instagram_caption"],
                    "post_caption": meta["caption"],
                })
                json_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
                logger.success(f"[Ledger] Cached AI metadata to {json_path.name}: '{meta['title']}'")
            except Exception as write_err:
                logger.debug(f"[Ledger] Could not cache JSON metadata: {write_err}")
        else:
            if is_generic_title(meta["title"]):
                meta["title"] = "Insane GTA 6 Gaming Moment 🔥"
            if not meta["youtube_description"]:
                meta["youtube_description"] = f"{meta['title']}\n\n#Shorts #Gaming #GTA6 #RockstarGames #ViralShorts"
            if not meta["instagram_caption"]:
                meta["instagram_caption"] = f"{meta['title']}\n.\n.\n#reels #gaming #viral #gta6 #gtavi #explorepage"

    if not meta["youtube_description"]:
        meta["youtube_description"] = f"{meta['title']}\n\n#Shorts #Gaming #GTA #ViralShorts #Trending #Clips"
    if not meta["instagram_caption"]:
        meta["instagram_caption"] = f"{meta['title']}\n.\n.\n#reels #gaming #viral #gtav #explore #trending #fyp"

    return meta


def get_clip_stars(clip_id_or_name: str, default: int = 3) -> int:
    """
    Get 1-6 star priority rating for a clip.
    6 stars = Top Tier (uploaded first)
    5 stars = High Priority
    ...
    1 star = Marked for deletion
    """
    stem = Path(clip_id_or_name).stem
    # 1. Check SQLite clip_preferences
    with _DB_LOCK:
        conn = _get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT stars FROM clip_preferences WHERE clip_id = ? OR file_name = ? OR file_name = ? LIMIT 1;",
                (stem, stem, f"{stem}.mp4"),
            )
            row = cur.fetchone()
            if row is not None and row[0] is not None:
                return max(1, min(6, int(row[0])))
        except Exception:
            pass
        finally:
            conn.close()

    # 2. Check companion <stem>.json sidecar
    json_path = DATA_DIR / f"{stem}.json"
    if json_path.is_file():
        try:
            jdata = json.loads(json_path.read_text(encoding="utf-8"))
            s = jdata.get("priority_stars") or jdata.get("stars") or jdata.get("settings", {}).get("priority_stars")
            if s is not None:
                return max(1, min(6, int(s)))
        except Exception:
            pass

    return default


def delete_clip_files(clip_id_or_name: str) -> bool:
    """
    Delete all files associated with a clip: .mp4, .json, .srt, .vtt, .part, etc.
    Remove from preference table and mark as deleted in uploads ledger.
    """
    stem = Path(clip_id_or_name).stem
    deleted_any = False
    for ext in (".mp4", ".json", ".srt", ".vtt", ".part", ".ytdl"):
        target = DATA_DIR / f"{stem}{ext}"
        if target.is_file():
            try:
                target.unlink(missing_ok=True)
                deleted_any = True
            except OSError as e:
                logger.warning(f"[Ledger] Could not delete {target.name}: {e}")

    # Remove temporary or low-res files
    for p in DATA_DIR.glob(f"*{stem}*"):
        if p.is_file() and p.name != f"{stem}.mp4":
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    with _DB_LOCK:
        conn = _get_connection()
        try:
            conn.execute("DELETE FROM clip_preferences WHERE clip_id = ? OR file_name = ?;", (stem, f"{stem}.mp4"))
            conn.execute(
                "UPDATE uploads SET status = 'deleted' WHERE file_name = ? OR file_path LIKE ?;",
                (f"{stem}.mp4", f"%{stem}%"),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[Ledger] DB cleanup failed for {stem}: {e}")
        finally:
            conn.close()

    logger.info(f"[Ledger] Deleted clip files and ledger records for {stem}")
    return deleted_any


def set_clip_stars(clip_id_or_name: str, stars: int) -> bool:
    """
    Set 1-6 star priority rating for a clip.
    If stars == 1, immediately deletes the clip per requirement:
    'which videos have 1 stars deleat them'
    """
    stem = Path(clip_id_or_name).stem
    stars = max(1, min(6, int(stars)))

    if stars == 1:
        delete_clip_files(stem)
        return True

    now_iso = datetime.now(timezone.utc).isoformat()
    # 1. Update SQLite
    with _DB_LOCK:
        conn = _get_connection()
        try:
            conn.execute(
                """
                INSERT INTO clip_preferences (clip_id, file_name, stars, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(clip_id) DO UPDATE SET
                    stars = excluded.stars,
                    updated_at = excluded.updated_at;
                """,
                (stem, f"{stem}.mp4", stars, now_iso),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[Ledger] Failed to save clip preference: {e}")
        finally:
            conn.close()

    # 2. Update companion <stem>.json
    json_path = DATA_DIR / f"{stem}.json"
    if json_path.is_file():
        try:
            jdata = json.loads(json_path.read_text(encoding="utf-8"))
            jdata["priority_stars"] = stars
            json_path.write_text(json.dumps(jdata, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Ledger] Could not update {json_path}: {e}")

    logger.info(f"[Ledger] Set priority for {stem} to {stars} stars ⭐")
    return True


def get_pending_clips(
    data_dir: Path | str = DATA_DIR,
    platforms: Tuple[str, ...] = ("youtube", "instagram"),
) -> List[Dict[str, Any]]:
    """
    Scan data directory for true vertical 9:16 clips and return those that have
    NOT yet been uploaded to at least one of the specified platforms.
    Filters out raw stream downloads (> 250 MB or horizontal).
    Sorts descending by star rating (6 stars first, then 5, 4, 3, 2), then oldest first (FIFO).
    Auto-deletes 1-star clips.
    """
    data_path = Path(data_dir)
    if not data_path.is_dir():
        return []

    pending = []
    for f in data_path.glob("*.mp4"):
        # Ignore temp or audio files
        if f.name.endswith(".temp.mp4") or ".audio." in f.name:
            continue
        # Ignore tiny files (< 500 KB) or huge stream downloads (> 250 MB)
        try:
            sz = f.stat().st_size
            if sz < 500 * 1024 or sz > 250 * 1024 * 1024:
                continue
        except OSError:
            continue

        # Strictly require 9:16 vertical aspect ratio
        if not is_vertical_clip(f):
            continue

        needed_platforms = []
        for p in platforms:
            if not is_uploaded(f, p):
                needed_platforms.append(p)

        if needed_platforms:
            stars = get_clip_stars(f.stem, default=3)
            if stars == 1:
                # 1-star clips are deleted per requirement
                delete_clip_files(f.stem)
                continue

            meta = get_clip_metadata(f, generate_ai=False)

            # Read chunk info if available
            chunk_label = ""
            chunk_range = ""
            json_path = f.with_suffix(".json")
            if json_path.is_file():
                try:
                    jdata = json.loads(json_path.read_text(encoding="utf-8"))
                    settings = jdata.get("settings", {})
                    chunk_label = jdata.get("chunk_label") or settings.get("chunk_label") or ""
                    chunk_range = jdata.get("chunk_range") or settings.get("chunk_range") or ""
                except Exception:
                    pass

            pending.append({
                "path": str(f.resolve()),
                "file_name": f.name,
                "file_size": f.stat().st_size,
                "mtime": f.stat().st_mtime,
                "needed_platforms": needed_platforms,
                "stars": stars,
                "chunk_label": chunk_label,
                "chunk_range": chunk_range,
                "title": meta["title"],
                "hook": meta["hook"],
                "youtube_description": meta["youtube_description"],
                "instagram_caption": meta["instagram_caption"],
                "caption": meta["instagram_caption"],
            })

    # Sort descending by stars (-stars: 6 stars first, then 5, 4, 3, 2), then oldest first (mtime)
    pending.sort(key=lambda x: (-x.get("stars", 3), x["mtime"]))
    return pending


if __name__ == "__main__":
    print(f"Upload ledger initialized at {DB_PATH}")
    pending = get_pending_clips()
    print(f"Pending clips found: {len(pending)}")
    for p in pending:
        print(f" - {p['file_name']}: needs {p['needed_platforms']} | Title: {p['title']}")
