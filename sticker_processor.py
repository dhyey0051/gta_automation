"""
sticker_processor.py - Smart auto-trimming and background removal for streamer divider stickers.
Eliminates excess borders and background canvas, rendering cleanly between facecam and gameplay.
"""
from pathlib import Path
import uuid
import cv2
import numpy as np
from typing import Optional, Dict, Any


def clean_and_trim_sticker(image_bytes: bytes, output_dir: Path) -> Dict[str, Any]:
    """
    Decodes an uploaded image, auto-trims excess padding, removes outer canvas backgrounds
    (making it transparent), and saves the resulting RGBA PNG.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("Invalid image file: could not decode image format.")

    h, w = img.shape[:2]
    if h < 8 or w < 8:
        raise ValueError("Sticker image too small.")

    # Case 1: Image already has transparent alpha channel
    if len(img.shape) == 4 and np.any(img[:, :, 3] < 250):
        alpha = img[:, :, 3]
        coords = cv2.findNonZero((alpha > 12).astype(np.uint8))
        if coords is not None:
            x, y, bw, bh = cv2.boundingRect(coords)
            cropped = img[y:y+bh, x:x+bw]
        else:
            cropped = img
    else:
        # Case 2: Opaque image (JPEG, non-transparent PNG) with outer background canvas
        if len(img.shape) == 2:
            bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            bgr = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        else:
            bgr = img.copy()

        # Margin to step inside outer window frame or edge artifacts (3-5px)
        m = max(2, min(6, w // 15, h // 15))
        top_bar = bgr[m:m+4, m:w-m].reshape(-1, 3)
        bot_bar = bgr[h-m-4:h-m, m:w-m].reshape(-1, 3)
        left_bar = bgr[m:h-m, m:m+4].reshape(-1, 3)
        right_bar = bgr[m:h-m, w-m-4:w-m].reshape(-1, 3)

        border_samples = np.concatenate([top_bar, bot_bar, left_bar, right_bar], axis=0)
        bg_color = np.median(border_samples, axis=0)

        # Flood fill from outer perimeter seeds using FIXED_RANGE
        # FIXED_RANGE guarantees floodFill only matches pixels close to bg_color (the white/light-grey surrounding).
        # It will NEVER enter black borders, red, blue, or any inner graphic!
        flood_mask = np.zeros((h + 2, w + 2), np.uint8)
        tol = 20  # Strict color tolerance for white/light-grey background
        flags = cv2.FLOODFILL_MASK_ONLY | cv2.FLOODFILL_FIXED_RANGE | (4 << 8)
        copy_for_flood = bgr.copy()

        # Seed all along perimeter edges
        step_x = max(1, w // 40)
        step_y = max(1, h // 40)
        seeds = []
        for x in range(m, w - m, step_x):
            seeds.append((x, m))
            seeds.append((x, h - 1 - m))
        for y in range(m, h - m, step_y):
            seeds.append((m, y))
            seeds.append((w - 1 - m, y))

        for cx, cy in seeds:
            cdiff = np.linalg.norm(bgr[cy, cx].astype(float) - bg_color)
            if cdiff <= tol + 10:
                cv2.floodFill(copy_for_flood, flood_mask, (cx, cy), (0, 0, 0),
                              (tol, tol, tol), (tol, tol, tol), flags)

        outer_bg = (flood_mask[1:-1, 1:-1] != 0)
        # Mark outer edge margin
        outer_bg[:m, :] = True
        outer_bg[-m:, :] = True
        outer_bg[:, :m] = True
        outer_bg[:, -m:] = True

        fg_mask = ~outer_bg
        coords = cv2.findNonZero(fg_mask.astype(np.uint8))
        if coords is not None:
            x, y, bw, bh = cv2.boundingRect(coords)
        else:
            x, y, bw, bh = 0, 0, w, h

        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        bgra[outer_bg, 3] = 0

        cropped = bgra[y:y+bh, x:x+bw]

    stk_id = uuid.uuid4().hex[:12]
    out_file = output_dir / f"{stk_id}.png"
    cv2.imwrite(str(out_file), cropped)

    final_h, final_w = cropped.shape[:2]
    return {
        "id": stk_id,
        "width": int(final_w),
        "height": int(final_h),
        "file": f"{stk_id}.png",
        "preview_url": f"/sticker/{stk_id}.png"
    }


def get_sticker_file(data_dir: Path, sticker_id: str) -> Optional[Path]:
    """Resolves and validates a sticker file by its ID."""
    if not isinstance(sticker_id, str) or not sticker_id.isalnum():
        return None
    p = Path(data_dir) / 'stickers' / f"{sticker_id}.png"
    return p if p.exists() else None


def calculate_placement(layout: str, top: float, video_w: int, video_h: int, sticker_path: Path) -> Dict[str, int]:
    """
    Computes exact overlay coordinates for the sticker so it sits perfectly
    on the divider seam without obstructing streamer facecam or gameplay HUD.
    """
    img = cv2.imread(str(sticker_path), cv2.IMREAD_UNCHANGED)
    if img is not None:
        sh, sw = img.shape[:2]
    else:
        sw, sh = 900, 200

    max_h = int(video_h * 0.16) // 2 * 2  # Max ~306px on 1080x1920

    if sw >= sh * 2.2:
        # Wide divider banner (e.g. Twitch streamer bar) -> span full width
        target_w = video_w
        target_h = int(sh * target_w / sw) // 2 * 2
        if target_h > max_h:
            target_h = max_h
            target_w = int(sw * target_h / sh) // 2 * 2
    else:
        # More square or badge-style sticker -> padded width
        target_w = int(video_w * 0.88) // 2 * 2
        target_h = int(sh * target_w / sw) // 2 * 2
        if target_h > max_h:
            target_h = max_h
            target_w = int(sw * target_h / sh) // 2 * 2

    target_x = max(0, (video_w - target_w) // 2)
    cam_h = int(top * video_h) // 2 * 2

    if layout == 'stack':
        # Centered vertically right on the seam between facecam and gameplay
        target_y = cam_h - (target_h // 2)
    elif layout == 'bottom':
        # Cam at bottom, gameplay top -> seam is at video_h - cam_h
        target_y = (video_h - cam_h) - (target_h // 2)
    elif layout == 'side':
        target_y = video_h - target_h - int(video_h * 0.05)
    else:
        # pip, circle, fill, blur, game: lower-third streamer branding
        target_y = int(video_h * 0.70) - (target_h // 2)

    return {
        'x': target_x,
        'y': target_y,
        'w': target_w,
        'h': target_h,
        'bottom': target_y + target_h
    }
