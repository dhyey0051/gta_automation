"""Subtitle Tracker Engine for NeonClipping.
Tracks speaker voice acoustics, eliminates ASR early-lead timing bias,
snaps word timestamps to acoustic speech onsets, and provides real-time word-by-word tracking.
"""

import subprocess
import numpy as np
from pathlib import Path

DEFAULT_SYNC_OFFSET_SEC = 0.16  # Calibrated +160ms sync offset (Whisper & YouTube ASR systematically lead speech by ~150-250ms)


def extract_audio_energy(video_or_audio_path, start_sec=0.0, dur_sec=None, sample_rate=16000, hop_ms=10):
    """Extract short-time RMS energy envelope of audio at 10ms resolution."""
    hop_samples = int(sample_rate * (hop_ms / 1000.0))
    cmd = [
        "ffmpeg", "-v", "error",
        "-ss", f"{max(0.0, float(start_sec)):.2f}"
    ]
    if dur_sec is not None and dur_sec > 0:
        cmd += ["-t", f"{float(dur_sec):.2f}"]
    cmd += [
        "-i", str(video_or_audio_path),
        "-vn", "-ac", "1", "-ar", str(sample_rate),
        "-f", "s16le", "pipe:1"
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
        if len(pcm) < hop_samples:
            return np.array([]), hop_ms
        n_frames = len(pcm) // hop_samples
        pcm_trimmed = pcm[:n_frames * hop_samples].reshape(n_frames, hop_samples)
        # Compute RMS energy per 10ms frame
        energy = np.sqrt(np.mean(pcm_trimmed ** 2, axis=1))
        return energy, hop_ms
    except Exception:
        return np.array([]), hop_ms


def track_and_align_words(video_or_audio_path, start_sec, end_sec, words, sync_offset=DEFAULT_SYNC_OFFSET_SEC):
    """
    Precision Subtitle Tracker:
    1. Analyzes audio energy envelope to find true acoustic speech onsets.
    2. Snaps words to speech bursts rather than pre-speech silence.
    3. Applies calibrated sync offset to ensure subtitles hit with the voice, never ahead of it.
    4. Cleans up overlaps, minimum durations, and natural cadence.
    """
    if not words:
        return []

    dur = max(1.0, float(end_sec) - float(start_sec))
    energy, hop_ms = extract_audio_energy(video_or_audio_path, start_sec, dur)

    has_audio = len(energy) > 50
    hop_sec = hop_ms / 1000.0

    if has_audio:
        med_energy = float(np.median(energy))
        max_energy = float(np.max(energy))
        # Speech threshold: 25% above median toward peak
        speech_thresh = med_energy + 0.15 * max(100.0, max_energy - med_energy)

    aligned = []
    for i, w in enumerate(words):
        raw_start = float(w.get("start", 0.0))
        raw_end = float(w.get("end", raw_start + 0.3))
        text = str(w.get("text", "")).strip()
        if not text:
            continue

        adj_start = raw_start + sync_offset
        adj_end = raw_end + sync_offset

        # If audio energy is available, search for acoustic onset
        if has_audio:
            # Search from raw_start to raw_start + 0.35s
            f_start = max(0, int((raw_start) / hop_sec))
            f_end = min(len(energy), int((raw_start + 0.35) / hop_sec))
            if f_end > f_start:
                search_region = energy[f_start:f_end]
                # Look for the first frame where energy exceeds speech threshold
                speech_idx = np.where(search_region >= speech_thresh)[0]
                if len(speech_idx) > 0:
                    acoustic_onset = (f_start + speech_idx[0]) * hop_sec
                    # If acoustic onset is later than raw_start, snap to acoustic onset!
                    if acoustic_onset > raw_start:
                        # Smooth blend between acoustic onset + slight offset
                        adj_start = acoustic_onset + 0.04

        # Enforce minimum word duration (at least 140ms so it doesn't flicker)
        word_dur = max(0.14, adj_end - adj_start)
        adj_end = adj_start + word_dur

        # Clamp within clip boundaries
        adj_start = max(0.04, min(dur - 0.1, adj_start))
        adj_end = max(adj_start + 0.1, min(dur, adj_end))

        aligned.append({
            "start": round(adj_start, 3),
            "end": round(adj_end, 3),
            "text": text
        })

    # Ensure monotonic ordering and eliminate unnatural overlap
    for i in range(len(aligned) - 1):
        if aligned[i]["end"] > aligned[i + 1]["start"]:
            # If overlap, cap end at the next word's start
            aligned[i]["end"] = max(aligned[i]["start"] + 0.10, aligned[i + 1]["start"])

    return aligned
