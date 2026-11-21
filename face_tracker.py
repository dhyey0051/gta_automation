"""Face Tracker Engine for NeonClipping.
Detects and tracks the YouTuber/streamer's face across video frames using OpenCV YuNet.
Isolates the persistent streamer face from temporary in-game characters.
Computes optimal framing for Facecam (top split ~16:10) and Gameplay (bottom split).
"""
import subprocess
import tempfile
from pathlib import Path
import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

MODEL_PATH = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
BW, BH = 640, 360  # Resolution for fast YuNet detection


def _get_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=20
        )
        return float(r.stdout.strip() or 0)
    except Exception:
        return 0.0


def track_streamer(video_path, start_sec=0.0, end_sec=None, sample_count=10):
    """
    Detects and tracks the streamer's face in `video_path` between `start_sec` and `end_sec`.
    Returns a dict with 'found', 'cam', 'game', 'confidence', 'method'.
    """
    if cv2 is None or not MODEL_PATH.exists():
        return _fallback_result("OpenCV or YuNet model missing")

    dur = _get_duration(video_path)
    if dur <= 0:
        return _fallback_result("Could not read video duration")

    start_sec = max(0.0, float(start_sec))
    end_sec = min(dur, float(end_sec)) if end_sec is not None else dur
    clip_dur = max(1.0, end_sec - start_sec)

    sample_count = max(6, min(20, sample_count))
    # Sample moments across the clip
    times = [start_sec + clip_dur * (i + 0.5) / sample_count for i in range(sample_count)]

    det = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", (BW, BH), score_threshold=0.50, nms_threshold=0.3)

    raw_faces = []
    with tempfile.TemporaryDirectory() as d:
        for i, t in enumerate(times):
            frame_png = Path(d) / f"frame_{i}.png"
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-ss", f"{t:.2f}",
                "-i", str(video_path),
                "-frames:v", "1",
                "-vf", f"scale={BW}:{BH}",
                str(frame_png)
            ]
            try:
                subprocess.run(cmd, capture_output=True, timeout=25)
                if not frame_png.exists():
                    continue
                img = cv2.imread(str(frame_png))
                if img is None:
                    continue
                _, faces = det.detect(img)
                if faces is not None and len(faces) > 0:
                    for f in faces:
                        # f: [x, y, w, h, x_re, y_re, x_le, y_le, x_nt, y_nt, x_rc, y_rc, x_lc, y_lc, score]
                        fx, fy, fw, fh = float(f[0])/BW, float(f[1])/BH, float(f[2])/BW, float(f[3])/BH
                        score = float(f[14])
                        raw_faces.append({
                            "t": t,
                            "cx": fx + fw / 2.0,
                            "cy": fy + fh / 2.0,
                            "w": fw,
                            "h": fh,
                            "score": score
                        })
            except Exception:
                continue

    if not raw_faces:
        # Fall back to border detector if available
        return _fallback_border_check(video_path)

    # Spatial clustering: group face detections that remain in the same region
    clusters = []
    for f in raw_faces:
        matched = False
        for c in clusters:
            # Face center distance threshold (~12% horizontally, 15% vertically)
            if abs(f["cx"] - c["cx"]) < 0.12 and abs(f["cy"] - c["cy"]) < 0.15:
                c["points"].append(f)
                c["cx"] = float(np.mean([p["cx"] for p in c["points"]]))
                c["cy"] = float(np.mean([p["cy"] for p in c["points"]]))
                matched = True
                break
        if not matched:
            clusters.append({"cx": f["cx"], "cy": f["cy"], "points": [f]})

    # Rank clusters by number of distinct sampled frames they appear in
    for c in clusters:
        unique_frames = len(set(p["t"] for p in c["points"]))
        c["frame_rate"] = unique_frames / float(sample_count)

    clusters.sort(key=lambda c: (c["frame_rate"], len(c["points"])), reverse=True)
    best = clusters[0]

    # If the top cluster appears in >= 30% of frames (or >= 2 frames for short clips)
    if best["frame_rate"] >= 0.28 or len(best["points"]) >= 3:
        fcx = float(best["cx"])
        fcy = float(best["cy"])
        med_w = float(np.median([p["w"] for p in best["points"]]))
        med_h = float(np.median([p["h"] for p in best["points"]]))

        # Check if facecam_detect can find a crisp static border enclosing this face
        border_cam = _check_enclosing_border(video_path, fcx, fcy)
        if border_cam:
            cam_box = border_cam
            method = "tracked_border+face"
        else:
            # Target 16:10 aspect ratio (~1.607) for top 35% vertical split
            ar_target = 1.607
            # Box width covers face + shoulders nicely
            cam_w = min(0.48, max(0.20, med_w * 3.4))
            cam_h = min(0.48, cam_w / ar_target * (BW / BH)) # aspect corrected for 16:9 normalized
            # Position face in upper-center of camera box
            cam_x = max(0.0, min(1.0 - cam_w, fcx - cam_w / 2.0))
            cam_y = max(0.0, min(1.0 - cam_h, fcy - cam_h * 0.40))
            cam_box = {
                "x": round(float(cam_x), 4),
                "y": round(float(cam_y), 4),
                "w": round(float(cam_w), 4),
                "h": round(float(cam_h), 4)
            }
            method = "face_tracker_yunet"

        # Determine optimal gameplay area to avoid webcam obstruction in bottom layer
        if fcx < 0.35:
            # Streamer on left -> crop gameplay shifted slightly right so center action is clean
            game_box = {"x": 0.15, "y": 0.0, "w": 0.85, "h": 1.0}
        elif fcx > 0.65:
            # Streamer on right -> shift gameplay slightly left
            game_box = {"x": 0.0, "y": 0.0, "w": 0.85, "h": 1.0}
        else:
            game_box = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

        return {
            "found": True,
            "method": method,
            "confidence": round(float(best["frame_rate"]), 2),
            "cam": cam_box,
            "game": game_box,
            "face_center": (round(fcx, 4), round(fcy, 4))
        }

    return _fallback_border_check(video_path)


def _check_enclosing_border(video_path, fcx, fcy):
    """If facecam_detect finds a static rectangular border that contains (fcx, fcy), use it."""
    try:
        import facecam_detect
        res = facecam_detect.detect(video_path)
        if res.get("found") and "cam" in res:
            c = res["cam"]
            if c["x"] <= fcx <= c["x"] + c["w"] and c["y"] <= fcy <= c["y"] + c["h"]:
                return c
    except Exception:
        pass
    return None


def _fallback_border_check(video_path):
    """Tries border-based detection before returning generic default layout."""
    try:
        import facecam_detect
        res = facecam_detect.detect(video_path)
        if res.get("found") and "cam" in res:
            return {
                "found": True,
                "method": "static_border_fallback",
                "confidence": res.get("confidence", 0.7),
                "cam": res["cam"],
                "game": res.get("game", {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
            }
    except Exception:
        pass
    return _fallback_result("no streamer face or webcam detected")


def _fallback_result(reason):
    """Graceful fallback so split-screen never fails even on videos without facecams."""
    return {
        "found": False,
        "confidence": 0.0,
        "reason": reason,
        "cam": {"x": 0.15, "y": 0.05, "w": 0.70, "h": 0.45},
        "game": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}
    }
