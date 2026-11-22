"""
NeonClipping MCP Server.
Provides tools to clip gaming & long videos into captioned vertical shorts (Reels, TikTok, Shorts).
Powered by local DeepSeek & Gemini models via 9router, faster-whisper, and ffmpeg.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

mcp = FastMCP("neonclipping")


def _is_server_alive(port: int = 8770) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _get_active_model() -> Dict[str, Any]:
    try:
        import sys
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        import smart
        m = smart._model()
        return {"model": m, "provider": "9router (Local Gateway)" if str(m).startswith("9router:") else "ollama"}
    except Exception as e:
        return {"model": None, "error": str(e)}


@mcp.tool()
def neonclipping_status() -> Dict[str, Any]:
    """Check NeonClipping engine health, GPU acceleration, local LLM status (DeepSeek/Gemini), and rendered clips."""
    ffmpeg_path = shutil.which("ffmpeg")
    server_running = _is_server_alive(8770)
    llm_info = _get_active_model()

    gpu_name = "N/A"
    try:
        gpu_out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if gpu_out.returncode == 0:
            gpu_name = gpu_out.stdout.strip()
    except Exception:
        pass

    clips = list(DATA_DIR.glob("*.mp4"))

    return {
        "status": "ready",
        "web_ui_url": "http://127.0.0.1:8770",
        "web_ui_running": server_running,
        "ffmpeg_installed": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path,
        "gpu": gpu_name,
        "active_llm": llm_info.get("model"),
        "llm_provider": llm_info.get("provider"),
        "available_llms": [
            "DeepSeek (freemodel/deepseek-v4-pro-max) [PRIMARY]",
            "Gemini (freemodel/gemini-3.7-flash) [FALLBACK]"
        ],
        "clips_count": len(clips),
        "data_directory": str(DATA_DIR),
        "supported_sources": ["YouTube link", "Twitch stream / VOD", "Kick stream / VOD", "Local MP4 / MOV file"],
        "caption_styles": ["GTA Neon", "Hormozi", "MrBeast", "Impact", "Clean"],
        "caption_languages": ["Auto (Hinglish/English)", "English", "Hindi"],
        "layouts": ["blur (9:16 blurred background)", "gameplay (full 9:16)", "facecam + gameplay (split)"]
    }


@mcp.tool()
def neonclipping_set_llm(model: str = "deepseek") -> Dict[str, Any]:
    """Select which local LLM model to use for moment scoring, titling, and hooks: 'deepseek' or 'gemini'.

    Args:
        model: Either 'deepseek' (quality/deep analysis) or 'gemini' (lightning fast).
    """
    model_lower = model.strip().lower()
    if "gemini" in model_lower or "gimini" in model_lower:
        os.environ["NEONCLIPPING_MODEL"] = "gemini"
        chosen = "Gemini (freemodel/gemini-3.7-flash)"
    else:
        os.environ["NEONCLIPPING_MODEL"] = "deepseek"
        chosen = "DeepSeek (freemodel/deepseek-v4-pro-max)"

    return {
        "selected_model": chosen,
        "env_var_set": os.environ.get("NEONCLIPPING_MODEL")
    }


@mcp.tool()
def neonclipping_make_clips(
    url: str,
    count: int = 2,
    min_len: int = 15,
    max_len: int = 60,
    layout: str = "blur",
    mode: str = "fast",
    caption_style: str = "GTA Neon",
    caption_language: str = "Auto"
) -> Dict[str, Any]:
    """Create captioned vertical 9:16 clips from a YouTube URL, Twitch/Kick stream, or local video file.

    Args:
        url: The video URL or local file path to clip.
        count: Number of viral clips to produce (default: 2).
        min_len: Minimum clip duration in seconds (default: 15).
        max_len: Maximum clip duration in seconds (default: 60).
        layout: 'blur' (vertical with blurred background), 'gameplay' (full crop), or 'split'.
        mode: 'fast' (~2 min audio/motion scan) or 'deep' (full AI transcript analysis).
        caption_style: 'GTA Neon' (bouncing yellow pop-up), 'Hormozi', 'MrBeast', 'Impact', or 'Clean'.
        caption_language: 'Auto' (speaks Hindi -> romanized Hinglish), 'English', or 'Hindi'.
    """
    payload = {
        "url": url,
        "count": count,
        "min_len": min_len,
        "max_len": max_len,
        "layout": layout,
        "mode": mode,
        "caption_style": caption_style,
        "caption_language": caption_language
    }

    port = int(os.environ.get("NEONCLIPPING_PORT", "8770"))
    if not _is_server_alive(port):
        return {
            "error": "NeonClipping local web server is not running.",
            "action_required": f"Start the server by running NeonClipping-Start.bat or checking http://127.0.0.1:{port}"
        }

    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/make",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            job_id = data.get("job")
            return {
                "success": True,
                "job_id": job_id,
                "status_url": f"http://127.0.0.1:{port}/make/{job_id}",
                "message": f"Job {job_id} started. Poll neonclipping_get_job with this job_id."
            }
    except Exception as e:
        return {"error": f"Failed to submit clip job: {str(e)}"}


@mcp.tool()
def neonclipping_get_job(job_id: str) -> Dict[str, Any]:
    """Poll the status and output of a running clipping job.

    Args:
        job_id: The job ID returned by neonclipping_make_clips.
    """
    port = int(os.environ.get("NEONCLIPPING_PORT", "8770"))
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/make/{job_id}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data
    except Exception as e:
        return {"error": f"Failed to fetch job status: {str(e)}"}


@mcp.tool()
def neonclipping_list_clips() -> List[Dict[str, Any]]:
    """List all finished MP4 clips saved in the NeonClipping data directory."""
    clips = []
    for f in DATA_DIR.glob("*.mp4"):
        try:
            stat = f.stat()
            clips.append({
                "filename": f.name,
                "path": str(f.resolve()),
                "file_url": f.as_uri(),
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": stat.st_mtime
            })
        except Exception:
            pass
    clips.sort(key=lambda x: x["modified"], reverse=True)
    return clips


@mcp.tool()
def neonclipping_open_folder() -> Dict[str, Any]:
    """Open the NeonClipping clips folder in Windows File Explorer."""
    try:
        os.startfile(str(DATA_DIR))
        return {"success": True, "path": str(DATA_DIR)}
    except Exception as e:
        return {"error": f"Could not open directory: {str(e)}"}


@mcp.tool()
def neonclipping_upload_status() -> Dict[str, Any]:
    """Check the authentication status of connected YouTube and Instagram accounts and recent upload logs."""
    import auto_upload
    cfg_file = BASE_DIR / "config.json"
    auto_enabled = True
    if cfg_file.exists():
        try:
            auto_enabled = json.loads(cfg_file.read_text()).get("auto_upload", True)
        except Exception:
            pass
    return {
        "accounts": auto_upload.check_auth_status(),
        "recent_uploads": auto_upload.load_history()[-10:],
        "auto_upload_enabled": auto_enabled
    }


@mcp.tool()
def neonclipping_upload_clip(
    clip_id_or_path: str,
    platforms: List[str] = ["youtube", "instagram"],
    title: str = "",
    hook: str = ""
) -> Dict[str, Any]:
    """Upload a specific clip to YouTube Shorts and/or Instagram Reels automatically.

    Args:
        clip_id_or_path: The filename, clip ID, or full path to the MP4 file in data/.
        platforms: List containing 'youtube', 'instagram', or both (default: ['youtube', 'instagram']).
        title: Custom title for the upload (optional; falls back to clip filename or hook).
        hook: Opening hook text to include in caption/description (optional).
    """
    import auto_upload
    target = Path(clip_id_or_path)
    if not target.is_file():
        target = DATA_DIR / (clip_id_or_path if clip_id_or_path.endswith(".mp4") else f"{clip_id_or_path}.mp4")
    if not target.is_file():
        return {"ok": False, "error": f"Clip not found: {clip_id_or_path}"}

    return auto_upload.publish_clip(target, title, hook, platforms)


@mcp.tool()
def neonclipping_enable_auto_upload(enabled: bool = True) -> Dict[str, Any]:
    """Toggle whether newly rendered clips are automatically uploaded to YouTube Shorts and Instagram Reels.

    Args:
        enabled: True to automatically upload every new clip upon rendering, False to only render without uploading.
    """
    os.environ["NEONCLIPPING_AUTO_UPLOAD"] = "1" if enabled else "0"
    cfg_file = BASE_DIR / "config.json"
    try:
        cur = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
        cur["auto_upload"] = enabled
        cfg_file.write_text(json.dumps(cur, indent=2))
    except Exception:
        pass
    return {
        "auto_upload_enabled": enabled,
        "platforms": ["YouTube Shorts (adg-cliping)", "Instagram Reels (ADG)"],
        "message": f"Automatic uploading is now {'ENABLED' if enabled else 'DISABLED'}. Every new clip made will be published directly."
    }


@mcp.tool()
def neonclipping_bot_status() -> Dict[str, Any]:
    """Get real-time status of the autonomous 12-minute upload scheduler bot and pending queue."""
    import auto_upload
    return auto_upload.get_bot_status()


@mcp.tool()
def neonclipping_start_bot(interval_minutes: int = 12) -> Dict[str, Any]:
    """Start the autonomous upload bot that uploads pending clips to YouTube and Instagram every 12 minutes.

    Args:
        interval_minutes: Cadence interval between uploads in minutes (default: 12 minutes).
    """
    import auto_upload
    msg = auto_upload.start_scheduler_bot(interval_minutes=interval_minutes)
    status = auto_upload.get_bot_status()
    return {
        "message": msg,
        "bot_status": status,
    }


@mcp.tool()
def neonclipping_stop_bot() -> Dict[str, Any]:
    """Stop the autonomous upload bot."""
    import auto_upload
    msg = auto_upload.stop_scheduler_bot()
    return {"message": msg}


if __name__ == "__main__":
    mcp.run()
