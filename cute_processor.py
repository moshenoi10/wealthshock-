"""
cute_processor.py — runs on GitHub Actions.

Downloads cute video clip(s) + music, mixes with ffmpeg,
outputs final_cute_video.mp4. Zero captions, zero text.
"""

import json
import os
import subprocess
import sys
import requests

CLIP_URLS = json.loads(os.environ["CLIP_URLS"])
MUSIC_URL = os.environ["MUSIC_URL"]
TARGET_DURATION = float(os.environ.get("TARGET_DURATION", "30"))


def download(url: str, dest: str):
    print(f"Downloading {url[:80]}… → {dest}")
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(65536):
                        if chunk:
                            f.write(chunk)
            size = os.path.getsize(dest)
            print(f"  {size // 1024} KB")
            if size > 0:
                return
        except Exception as exc:
            print(f"  Attempt {attempt+1} failed: {exc}")
    print(f"ERROR: failed to download {url}", file=sys.stderr)
    sys.exit(1)


def ffprobe_duration(path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 10.0


def run(cmd: list):
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True)


# ── 1. Download clips ─────────────────────────────────────────────────────────
clips = []
for i, url in enumerate(CLIP_URLS[:3]):
    dest = f"clip_{i}.mp4"
    download(url, dest)
    clips.append(dest)

# ── 2. Concatenate (or use single clip directly) ──────────────────────────────
if len(clips) > 1:
    with open("concat.txt", "w") as f:
        for c in clips:
            f.write(f"file '{c}'\n")
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "combined.mp4"])
else:
    os.rename(clips[0], "combined.mp4")

# ── 3. Trim to target duration ────────────────────────────────────────────────
actual_dur = ffprobe_duration("combined.mp4")
target = min(TARGET_DURATION, actual_dur)
print(f"Source duration: {actual_dur:.1f}s  →  target: {target:.1f}s")

run(["ffmpeg", "-y", "-i", "combined.mp4", "-t", str(target), "-c", "copy", "trimmed.mp4"])

# ── 4. Download music ─────────────────────────────────────────────────────────
download(MUSIC_URL, "music.mp3")

# ── 5. Convert music to PCM AAC so ffmpeg can stream-loop reliably ────────────
run(["ffmpeg", "-y", "-i", "music.mp3", "-c:a", "pcm_s16le", "music.wav"])

# ── 6. Mix: video + looped music, fade out last 2 seconds ────────────────────
fade_start = max(0.0, target - 2.0)
run([
    "ffmpeg", "-y",
    "-i", "trimmed.mp4",
    "-stream_loop", "-1", "-i", "music.wav",
    "-filter_complex",
    f"[1:a]volume=0.45,afade=t=out:st={fade_start}:d=2[mus]",
    "-map", "0:v",
    "-map", "[mus]",
    "-t", str(target),
    "-c:v", "libx264", "-crf", "23", "-preset", "fast",
    "-c:a", "aac", "-b:a", "192k",
    "-movflags", "+faststart",
    "final_cute_video.mp4",
])

size = os.path.getsize("final_cute_video.mp4")
print(f"\nDone: final_cute_video.mp4  ({size // 1024} KB, {target:.1f}s)")
