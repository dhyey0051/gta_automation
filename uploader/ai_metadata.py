"""
uploader/ai_metadata.py — AI Viral Metadata Generator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Uses local DeepSeek (freemodel/deepseek-v4-pro-max) and Gemini (freemodel/gemini-3.7-flash)
via 9router (http://127.0.0.1:20128/v1) to generate engaging, human-written titles,
compelling hooks, and rich descriptions with trending hashtags.
NEVER uses raw file IDs, hashes, or generic filler.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

ROUTER_URL = os.environ.get("NEONCLIPPING_ROUTER_URL", "http://127.0.0.1:20128/v1")
ROUTER_KEY = os.environ.get("NEONCLIPPING_ROUTER_KEY", "sk-9router")

DEEPSEEK_MODEL = "freemodel/deepseek-v4-pro-max"
GEMINI_MODEL = "freemodel/gemini-3.7-flash"
VIRAL_PRESETS_PATH = Path(__file__).resolve().parent / "viral_presets.json"


def _load_viral_presets() -> Dict[str, Any]:
    """Load curated viral GTA 6 & gaming hashtags and hooks from viral_presets.json."""
    if VIRAL_PRESETS_PATH.is_file():
        try:
            return json.loads(VIRAL_PRESETS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _call_llm(messages: list[dict], model: str = DEEPSEEK_MODEL, timeout: int = 25) -> Optional[str]:
    """Call local 9router LLM gateway with fallback."""
    models_to_try = [model]
    fallback = GEMINI_MODEL if model == DEEPSEEK_MODEL else DEEPSEEK_MODEL
    if fallback not in models_to_try:
        models_to_try.append(fallback)

    for m in models_to_try:
        try:
            req_data = json.dumps({
                "model": m,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 400,
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{ROUTER_URL}/chat/completions",
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {ROUTER_KEY}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    raw_text = resp.read().decode("utf-8")
                    # 9router appends 'data: [DONE]' to responses; strip up to last '}'
                    last_brace = raw_text.rfind("}")
                    if last_brace != -1:
                        raw_text = raw_text[:last_brace + 1]
                    data = json.loads(raw_text)
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0]["message"]["content"].strip()
        except Exception as e:
            logger.debug(f"[AI Metadata] LLM call failed for {m}: {e}")

    return None


def generate_metadata_for_clip(
    clip_path: Path | str,
    transcript: str = "",
    context_hint: str = "",
) -> Dict[str, str]:
    """
    Generate professional, viral title, description, and hashtags for a clip.
    Guarantees no raw IDs or meaningless filler text.
    """
    path = Path(clip_path)

    # 1. Check if valid transcript exists or extract from faster_whisper
    if not transcript:
        # Check for companion words file
        words_file = path.with_suffix(".words.json")
        if not words_file.exists():
            for wf in path.parent.glob(f"{path.stem}*.words.json"):
                words_file = wf
                break

        if words_file.exists():
            try:
                wdata = json.loads(words_file.read_text(encoding="utf-8"))
                transcript = " ".join(w["text"] for w in wdata.get("words", []))
            except Exception:
                pass

    # If still no transcript, try a quick 15s transcription
    if not transcript and path.is_file():
        try:
            import captions
            res = captions.transcribe(path, 0, 20, path.parent, lambda m: None, language=None, model="base")
            transcript = " ".join(w["text"] for w in res.get("words", []))
        except Exception:
            pass

    # 2. Try to find source video title from state.json files
    if not context_hint:
        for sf in path.parent.glob("*.state.json"):
            try:
                sdata = json.loads(sf.read_text(encoding="utf-8"))
                if sdata.get("title") and sdata.get("title") != "Uploaded video":
                    context_hint = sdata["title"]
                    break
            except Exception:
                pass

    # 3. Prompt local LLM (DeepSeek primary, Gemini fallback)
    clean_context = context_hint.strip() or "High-energy gaming and GTA gameplay livestream"
    clean_transcript = transcript.strip()

    # Load curated viral hashtags & templates
    presets = _load_viral_presets()
    gta_data = presets.get("gta6", {})
    yt_tags_curated = gta_data.get("youtube_hashtags", "#Shorts #GTA6 #GTAVI #RockstarGames #ViceCity #GTA #Gaming #ViralShorts #TrendingGaming")
    ig_tags_curated = gta_data.get("instagram_hashtags", "#reels #gaming #viral #gta6 #gtavi #rockstargames #vicecity #lucia #gtav #explorepage #trending #fyp #gamerlife")
    cta_yt = presets.get("call_to_actions", {}).get("youtube", "Drop a like if you enjoyed! 🔔 Subscribe for daily viral gaming moments!")
    cta_ig = presets.get("call_to_actions", {}).get("instagram", "Follow @adgclipz for daily viral gaming moments! 🎮 Tag a friend below 👇")

    prompt = f"""You are a top viral social media manager for YouTube Shorts and Instagram Reels gaming channels.
Video context: "{clean_context}"
Spoken dialogue/transcript: "{clean_transcript if clean_transcript else '[Non-verbal high energy gameplay/action]'}"

Generate engaging, human-written metadata for this vertical short video.
Output ONLY valid JSON with these exact keys:
{{
  "title": "A punchy, viral hook title (max 65 chars, includes 1 emoji, NO boring text, NO raw IDs)",
  "youtube_description": "2-3 lines engaging summary + question for comments + call to subscribe. Professional format.",
  "instagram_caption": "Catchy 1st line hook + 2 lines of context + Call to follow @adgclipz for more.",
  "youtube_hashtags": "{yt_tags_curated}",
  "instagram_hashtags": "{ig_tags_curated}"
}}
"""

    messages = [
        {"role": "system", "content": "You output only clean, valid JSON without markdown fences."},
        {"role": "user", "content": prompt},
    ]

    raw_resp = _call_llm(messages, model=DEEPSEEK_MODEL)
    if raw_resp:
        try:
            # Extract JSON block from potential markdown or thinking blocks
            m = re.search(r"\{[\s\S]*\}", raw_resp)
            if m:
                parsed = json.loads(m.group(0))

                title = parsed.get("title", "").strip()
                yt_desc = parsed.get("youtube_description", "").strip()
                ig_caption = parsed.get("instagram_caption", "").strip()
                yt_tags = yt_tags_curated
                ig_tags = ig_tags_curated

                if title and not re.search(r"^[a-f0-9]{12,}", title, re.IGNORECASE):
                    full_yt_desc = f"{title}\n\n{yt_desc}\n\n{cta_yt}\n\n{yt_tags}"
                    full_ig_caption = f"{title}\n\n{ig_caption}\n.\n.\n{cta_ig}\n.\n{ig_tags}"
                    return {
                        "title": title,
                        "youtube_description": full_yt_desc,
                        "instagram_caption": full_ig_caption,
                        "hook": title,
                    }
        except Exception as e:
            logger.debug(f"[AI Metadata] Failed to parse LLM JSON: {e}")

    # Fallback if LLM unreachable: create high quality gaming template (NEVER raw hash)
    safe_title = "GTA 6 Vice City Police Chase Goes Completely WRONG! 🚗💥"
    if "vice city" in clean_context.lower() or "gta" in clean_context.lower():
        safe_title = "Exploring Vice City Goes Completely Wrong! 🚗💥"
    elif clean_context and len(clean_context) > 5 and not "uploaded" in clean_context.lower():
        safe_title = f"{clean_context[:50]} 🎮"

    safe_yt_desc = (
        f"{safe_title}\n\n"
        "Insane gaming highlight from the stream! What would you have done here? Drop your thoughts below! 👇\n\n"
        f"{cta_yt}\n\n"
        f"{yt_tags_curated}"
    )

    safe_ig_caption = (
        f"{safe_title}\n\n"
        "You won't believe what happened next 😭 Tag a friend who would fail this!\n\n"
        f"{cta_ig}\n.\n.\n"
        f"{ig_tags_curated}"
    )

    return {
        "title": safe_title,
        "youtube_description": safe_yt_desc,
        "instagram_caption": safe_ig_caption,
        "hook": safe_title,
    }


if __name__ == "__main__":
    print("Testing AI Metadata generation...")
    res = generate_metadata_for_clip("data/58e70791faa846d2a19441448ec1b324.mp4", context_hint="GTA 6 Vice City Police Chase")
    print(json.dumps(res, indent=2))
