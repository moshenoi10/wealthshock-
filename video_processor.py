#!/usr/bin/env python3
"""
WealthShock Video Processor v4 — Remotion Orchestrator
Runs in GitHub Actions (ubuntu-latest, 14 GB RAM).

Inputs (env vars):
  AUDIO_URL          — direct download URL for the MP3 voiceover
  STOCK_VIDEO_URLS   — JSON array of Pixabay video URLs (15-20)
  TITLE              — video title (passed through, not used for rendering)
  SCRIPT             — spoken script text for word-synced captions

Pipeline:
  1. Download voiceover MP3 → remotion/public/audio/voice.mp3
  2. Detect audio duration via ffprobe
  3. Download background music → remotion/public/audio/music.mp3
  4. Download stock clips in parallel → remotion/public/clips/clip_NN.mp4
  5. Calculate word-by-word caption frame timings
  6. Write remotion_props.json
  7. node remotion/render.js  →  final_video.mp4

Output:
  final_video.mp4 — 1080x1920, 30fps, H.264
"""

import concurrent.futures
import json
import os
import re
import random
import shutil
import subprocess
import sys
import time

import requests

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REMOTION_DIR = os.path.join(SCRIPT_DIR, 'remotion')
PUBLIC_DIR   = os.path.join(REMOTION_DIR, 'public')
CLIPS_DIR    = os.path.join(PUBLIC_DIR, 'clips')
AUDIO_DIR    = os.path.join(PUBLIC_DIR, 'audio')
PROPS_FILE   = os.path.join(SCRIPT_DIR, 'remotion_props.json')
OUTPUT_FILE  = os.path.join(SCRIPT_DIR, 'final_video.mp4')

# ─── Constants ────────────────────────────────────────────────────────────────
FPS          = 30
CLIP_FRAMES  = 75    # 2.5 s × 30 fps
FLASH_FRAMES = 2     # white flash between clips

MUSIC_URLS = [
    "https://assets.mixkit.co/music/preview/mixkit-dark-cinematic-drums-570.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-hip-hop-02-738.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-tech-house-vibes-130.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-driving-ambition-32.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-sleek-corporate-background-music-680.mp3",
]


def log(msg):
    print(f"[processor] {msg}", flush=True)


# ─── Download helpers ─────────────────────────────────────────────────────────

def stream_download(url, dest, retries=3):
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
                r.raise_for_status()
                with open(dest, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            if os.path.exists(dest) and os.path.getsize(dest) > 1024:
                return True
        except Exception as exc:
            log(f"Download attempt {attempt + 1} failed ({url[:60]}): {exc}")
            time.sleep(2)
    return False


def _download_clip(args):
    url, dest = args
    return dest if stream_download(url, dest) else None


# ─── Audio duration ───────────────────────────────────────────────────────────

def get_duration(path):
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            val = r.stdout.strip()
            return float(val) if val else None
    except Exception:
        pass
    return None


# ─── Caption timing ───────────────────────────────────────────────────────────

def calculate_captions(script, audio_duration, fps=FPS):
    """
    Character-proportional word timing — longer words get more screen time.
    Returns list of {text, startFrame, endFrame}.
    """
    words = re.findall(r'\S+', script)
    if not words:
        return []

    char_counts  = [max(1, len(w)) for w in words]
    total_chars  = sum(char_counts)
    char_rate    = audio_duration / total_chars   # seconds per character

    # Clamp each word's raw duration, then rescale so total == audio_duration
    raw   = [max(0.08, min(0.9, c * char_rate)) for c in char_counts]
    scale = audio_duration / sum(raw)
    durs  = [d * scale for d in raw]

    captions = []
    frame = 0
    for word, dur in zip(words, durs):
        end_frame = frame + max(2, round(dur * fps))
        captions.append({'text': word, 'startFrame': frame, 'endFrame': end_frame})
        frame = end_frame
    return captions


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    audio_url       = os.environ.get('AUDIO_URL', '').strip()
    stock_urls_json = os.environ.get('STOCK_VIDEO_URLS', '[]').strip()
    script          = os.environ.get('SCRIPT', '').strip()

    if not audio_url:
        log('ERROR: AUDIO_URL is required')
        sys.exit(1)

    try:
        stock_urls = json.loads(stock_urls_json)
        if not isinstance(stock_urls, list) or not stock_urls:
            raise ValueError('empty list')
    except Exception:
        log('ERROR: STOCK_VIDEO_URLS must be a non-empty JSON array')
        sys.exit(1)

    # ── Prepare asset directories ─────────────────────────────────────────────
    os.makedirs(CLIPS_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)

    # ── Step 1: Voiceover ─────────────────────────────────────────────────────
    voice_path = os.path.join(AUDIO_DIR, 'voice.mp3')
    log(f'Downloading voiceover ({audio_url[:60]}...)')
    if not stream_download(audio_url, voice_path):
        log('ERROR: Voiceover download failed')
        sys.exit(1)
    log(f'Voiceover: {os.path.getsize(voice_path) // 1024} KB')

    audio_dur = get_duration(voice_path) or 60.0
    log(f'Audio duration: {audio_dur:.2f}s')

    # ── Step 2: Background music ──────────────────────────────────────────────
    music_path = os.path.join(AUDIO_DIR, 'music.mp3')
    music_ok = False
    urls = MUSIC_URLS[:]
    random.shuffle(urls)
    for url in urls:
        if stream_download(url, music_path):
            log(f'Music: {os.path.getsize(music_path) // 1024} KB')
            music_ok = True
            break
    if not music_ok:
        log('Warning: all music downloads failed — proceeding without music')

    # ── Step 3: Stock clips in parallel ──────────────────────────────────────
    log(f'Downloading {len(stock_urls)} stock clips...')
    clip_args = [
        (url, os.path.join(CLIPS_DIR, f'clip_{i:02d}.mp4'))
        for i, url in enumerate(stock_urls)
    ]

    clip_files = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(_download_clip, clip_args):
            if result:
                clip_files.append(os.path.basename(result))

    clip_files.sort()
    log(f'Downloaded {len(clip_files)}/{len(stock_urls)} clips')

    if not clip_files:
        log('ERROR: No clips downloaded')
        sys.exit(1)

    # ── Step 4: Caption timing ────────────────────────────────────────────────
    captions = calculate_captions(script, audio_dur) if script else []
    log(f'Captions: {len(captions)} words')

    # ── Step 5: Total frame count ─────────────────────────────────────────────
    # Must be long enough to cover the audio; slightly overshoots so -shortest
    # in the React <Audio> component handles the actual trim.
    unit = CLIP_FRAMES + FLASH_FRAMES            # frames per clip+flash unit
    num_units = int((audio_dur * FPS) / unit) + 4
    total_frames = max(num_units * unit, int(audio_dur * FPS) + FPS * 3)

    # ── Step 6: Write props ───────────────────────────────────────────────────
    props = {
        'audioFile': 'audio/voice.mp3',
        'musicFile': 'audio/music.mp3' if music_ok else '',
        'clipFiles': [f'clips/{f}' for f in clip_files],
        'captions': captions,
        'durationInFrames': total_frames,
    }
    with open(PROPS_FILE, 'w') as f:
        json.dump(props, f, indent=2)
    log(f'Props: {len(clip_files)} clips, {len(captions)} words, {total_frames} frames')

    # ── Step 7: Remotion render ───────────────────────────────────────────────
    log('Starting Remotion render (node render.js)...')
    result = subprocess.run(
        ['node', 'render.js'],
        cwd=REMOTION_DIR,
        timeout=540,  # 9 min — leaves 1 min buffer in 10-min job
    )

    if result.returncode != 0:
        log(f'ERROR: Remotion render exited with code {result.returncode}')
        sys.exit(1)

    if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) < 1024:
        log('ERROR: final_video.mp4 not produced or empty')
        sys.exit(1)

    size_mb = os.path.getsize(OUTPUT_FILE) / 1024 / 1024
    log(f'SUCCESS: final_video.mp4 ({size_mb:.1f} MB)')

    # Cleanup staged assets (GHA workspace is ephemeral, but be tidy)
    for d in [CLIPS_DIR, AUDIO_DIR]:
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)


if __name__ == '__main__':
    main()
