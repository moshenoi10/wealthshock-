#!/usr/bin/env python3
"""
WealthShock Video Processor v3 — Professional viral Shorts production
Runs in GitHub Actions (ubuntu-latest, 14 GB RAM, 4 vCPUs, full ffmpeg).

Inputs (env vars):
  AUDIO_URL          — direct download URL for the MP3 voiceover
  STOCK_VIDEO_URLS   — JSON array of Pixabay finance/money video URLs (15-20)
  TITLE              — hook title text for the opening title card
  SCRIPT             — spoken script text for word-synced captions

Output:
  final_video.mp4 — 1080x1920, 30fps, H.264, <100MB
"""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests
from PIL import Image, ImageDraw, ImageFont

# ─── Constants ────────────────────────────────────────────────────────────────
FFMPEG  = "ffmpeg"
FFPROBE = "ffprobe"
OUTPUT  = "final_video.mp4"
W, H    = 1080, 1920
FPS     = 30
CLIP_DUR    = 2.5               # seconds per stock clip (cut every ~2-3s)
FLASH_DUR   = 2 / FPS           # 2-frame white flash between clips
TITLE_DUR   = 3.0               # opening hook title card
MUSIC_VOL   = 0.08              # background music at 8%

MUSIC_FALLBACKS = [
    "https://assets.mixkit.co/music/preview/mixkit-dark-cinematic-drums-570.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-hip-hop-02-738.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-tech-house-vibes-130.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-driving-ambition-32.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-sleek-corporate-background-music-680.mp3",
]

# ─── Logging ──────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[processor] {msg}", flush=True)


# ─── Font discovery ───────────────────────────────────────────────────────────

def _find_font(preferred_names):
    """Return (path, name) for the first available font."""
    candidates = [
        ("/usr/share/fonts/truetype/BebasNeue-Regular.ttf",              "Bebas Neue"),
        ("/usr/share/fonts/truetype/bebas-neue/BebasNeue-Regular.ttf",   "Bebas Neue"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",         "DejaVu Sans Bold"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", "Liberation Sans Bold"),
        ("/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",                "Ubuntu Bold"),
        ("/Library/Fonts/Impact.ttf",                                    "Impact"),
    ]
    for path, name in candidates:
        if os.path.exists(path):
            return path, name
    return None, "DejaVu Sans Bold"

CAPTION_FONT_PATH, CAPTION_FONT_NAME = _find_font([])
FONT_DIR = os.path.dirname(CAPTION_FONT_PATH) if CAPTION_FONT_PATH else "/usr/share/fonts/truetype/dejavu"

log(f"Caption font: {CAPTION_FONT_NAME} ({CAPTION_FONT_PATH})")


# ─── Utilities ────────────────────────────────────────────────────────────────

def stream_download(url, dest, chunk=65536):
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk_data in r.iter_content(chunk_size=chunk):
                        if chunk_data:
                            f.write(chunk_data)
            if os.path.exists(dest) and os.path.getsize(dest) > 1024:
                return True
        except Exception as exc:
            log(f"Download attempt {attempt+1} failed ({url[:60]}): {exc}")
            time.sleep(2)
    return False


def ffmpeg_run(args, timeout=180, label=""):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode != 0:
            msg = r.stderr.decode(errors="replace")[-400:]
            log(f"ffmpeg{' ['+label+']' if label else ''} error: {msg}")
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        log(f"ffmpeg{' ['+label+']' if label else ''} timed out after {timeout}s")
        return False
    except Exception as exc:
        log(f"ffmpeg exception: {exc}")
        return False


def get_duration(path):
    try:
        r = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            return float(r.stdout.strip() or 0)
    except Exception:
        pass
    return None


def pil_font(size):
    if CAPTION_FONT_PATH and os.path.exists(CAPTION_FONT_PATH):
        try:
            return ImageFont.truetype(CAPTION_FONT_PATH, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


# ─── ASS captions (word-by-word, MrBeast-style) ──────────────────────────────

def _fmt_time(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s - h * 3600 - m * 60
    cs = int((sec - int(sec)) * 100)
    return f"{h}:{m:02d}:{int(sec):02d}.{cs:02d}"


def make_ass_captions(script, duration, path):
    words = re.findall(r"\S+", script)
    if not words:
        return False

    # Proportional timing: longer words get more screen time
    char_counts = [max(1, len(w)) for w in words]
    total_chars  = sum(char_counts)
    raw_durs     = [c / total_chars * duration for c in char_counts]
    # Clamp: min 0.08s, max 0.9s per word, then rescale to exactly fit duration
    clamped = [max(0.08, min(0.9, d)) for d in raw_durs]
    scale   = duration / sum(clamped)
    durs    = [d * scale for d in clamped]

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("[Script Info]\nScriptType: v4.00+\n")
            f.write(f"PlayResX: {W}\nPlayResY: {H}\n\n")
            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                    "Alignment, MarginL, MarginR, MarginV, Encoding\n")
            # Large white bold text, 6px black outline, subtle drop shadow, centered, bottom third
            f.write(f"Style: Word,{CAPTION_FONT_NAME},108,&H00FFFFFF,&H000000FF,"
                    "&H00000000,&H50000000,1,0,0,0,100,100,0,0,1,6,3,2,50,50,300,1\n\n")
            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
            t = 0.0
            for word, dur in zip(words, durs):
                end  = t + dur
                safe = word.replace("{", "\\{").replace("}", "\\}")
                # Uppercase each word for the viral look
                f.write(f"Dialogue: 0,{_fmt_time(t)},{_fmt_time(end)},"
                        f"Word,,0,0,0,,{{\\an2}}{safe.upper()}\n")
                t = end
        log(f"Captions: {len(words)} words over {duration:.1f}s")
        return True
    except Exception as exc:
        log(f"Caption generation failed: {exc}")
        return False


# ─── Hook title card ──────────────────────────────────────────────────────────

def make_title_card(title_text, tmp):
    img_path = os.path.join(tmp, "title.png")
    vid_path = os.path.join(tmp, "title.mp4")

    # Build gradient background + big yellow text
    try:
        w, h = W, H
        img = Image.new("RGB", (w, h), "#0a0a0a")
        # Gradient overlay (dark red at top, near-black at bottom)
        grad = Image.new("RGB", (w, h), "#7a0000")
        mask = Image.new("L", (w, h))
        mask.putdata([int(220 * (1 - y / h)) for y in range(h) for _ in range(w)])
        img.paste(grad, (0, 0), mask)

        draw = ImageDraw.Draw(img)

        # Small "WEALTHSHOCK" label at top
        label_font = pil_font(48)
        draw.text((w // 2, 160), "WEALTHSHOCK", font=label_font,
                  fill="#FFD700", anchor="mm",
                  stroke_width=3, stroke_fill="#000000")

        # Decorative line
        draw.rectangle([80, 210, w - 80, 216], fill="#FFD700")

        # Main hook text (big, yellow, centred, wrapped)
        hook_font = pil_font(130)
        from textwrap import wrap
        lines = wrap(title_text.upper(), width=10)[:5]
        y = 360
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=hook_font)
            tw   = bbox[2] - bbox[0]
            x    = max(30, (w - tw) // 2)
            draw.text((x, y), line, font=hook_font, fill="#FFD700",
                      stroke_width=8, stroke_fill="#000000")
            y += (bbox[3] - bbox[1]) + 20

        # Bottom "FOLLOW FOR MORE" cta
        cta_font = pil_font(52)
        draw.text((w // 2, H - 200), "FOLLOW FOR MORE ↓", font=cta_font,
                  fill="#FFFFFF", anchor="mm",
                  stroke_width=3, stroke_fill="#000000")

        img.save(img_path)
    except Exception as exc:
        log(f"Title card image failed: {exc}")
        return None

    # Animate with Ken Burns (1.0 → 1.05 zoom, 3 seconds)
    frames = int(TITLE_DUR * FPS)
    z_expr = f"1+0.05*on/{frames}"
    ok = ffmpeg_run([
        "-framerate", str(FPS), "-loop", "1", "-i", img_path,
        "-t", str(TITLE_DUR),
        "-vf", (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"zoompan=z='{z_expr}':d={frames}"
                f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':s={W}x{H}:fps={FPS}"),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", vid_path,
    ], timeout=90, label="title-card")

    if ok and os.path.exists(vid_path):
        log("Title card rendered")
        return vid_path
    log("Title card render failed")
    return None


# ─── Flash transition clip ────────────────────────────────────────────────────

def make_flash_clip(tmp):
    path = os.path.join(tmp, "flash.mp4")
    ok = ffmpeg_run([
        "-f", "lavfi",
        "-i", f"color=white:s={W}x{H}:r={FPS}:d={FLASH_DUR:.5f}",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", "-r", str(FPS), path,
    ], timeout=15, label="flash")
    return path if ok and os.path.exists(path) else None


# ─── Stock clip processing (Ken Burns zoom + scale to 1080×1920) ──────────────

def process_clip(raw_path, index, tmp):
    out = os.path.join(tmp, f"clip_{index:02d}.mp4")
    frames = int(CLIP_DUR * FPS)
    # Linear zoom: 1.0 → 1.08 (Ken Burns effect)
    z_expr = f"1+0.08*on/{frames}"

    filters = [
        # 1. Scale to cover 1080×1920 (no black bars ever)
        f"scale={W}:{H}:force_original_aspect_ratio=increase",
        f"crop={W}:{H}",
        # 2. Ken Burns zoom
        (f"zoompan=z='{z_expr}':d={frames}"
         f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':s={W}x{H}:fps={FPS}"),
    ]
    filt = ",".join(filters)

    ok = ffmpeg_run([
        "-i", raw_path, "-t", str(CLIP_DUR),
        "-vf", filt,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", out,
    ], timeout=60, label=f"clip-{index}")

    if ok and os.path.exists(out) and os.path.getsize(out) > 1024:
        return out

    # Fallback: simple scale + crop, no zoom
    log(f"Clip {index}: zoompan failed, using simple scale")
    ok = ffmpeg_run([
        "-i", raw_path, "-t", str(CLIP_DUR),
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-an", out,
    ], timeout=60, label=f"clip-{index}-fallback")

    return out if ok and os.path.exists(out) and os.path.getsize(out) > 1024 else None


def _download_and_process(args):
    """Worker: download + process one clip. Returns (index, output_path|None)."""
    url, index, tmp = args
    raw = os.path.join(tmp, f"raw_{index:02d}.mp4")
    if not stream_download(url, raw):
        log(f"Clip {index}: download failed")
        return index, None
    result = process_clip(raw, index, tmp)
    try:
        os.remove(raw)
    except Exception:
        pass
    return index, result


# ─── Background music ─────────────────────────────────────────────────────────

def download_music(tmp):
    dest = os.path.join(tmp, "music.mp3")
    import random
    urls = MUSIC_FALLBACKS[:]
    random.shuffle(urls)
    for url in urls:
        if stream_download(url, dest):
            log(f"Music downloaded ({os.path.getsize(dest)//1024}KB)")
            return dest
    log("All music downloads failed")
    return None


# ─── Audio mixing ─────────────────────────────────────────────────────────────

def mix_audio(voice_mp3, music_mp3, tmp):
    out = os.path.join(tmp, "audio_final.aac")
    ok = ffmpeg_run([
        "-i", voice_mp3,
        "-stream_loop", "-1", "-i", music_mp3,
        "-filter_complex",
        (f"[1:a]volume={MUSIC_VOL}[m];"
         "[0:a][m]amix=inputs=2:duration=first:dropout_transition=2[out]"),
        "-map", "[out]",
        "-c:a", "aac", "-ar", "44100", "-b:a", "192k",
        out,
    ], timeout=120, label="mix-audio")
    if ok and os.path.exists(out):
        log("Audio mixed with background music")
        return out

    # Fallback: voice only (convert to AAC)
    log("Music mix failed — voice-only fallback")
    out2 = os.path.join(tmp, "voice.aac")
    ok2  = ffmpeg_run(["-i", voice_mp3, "-c:a", "aac", "-ar", "44100", "-b:a", "192k", out2],
                      timeout=60, label="voice-aac")
    return out2 if ok2 and os.path.exists(out2) else voice_mp3


# ─── Build concat list + run concat ──────────────────────────────────────────

def concat_clips(clip_paths, flash_path, title_path, audio_duration, tmp):
    unit_dur = FLASH_DUR + CLIP_DUR          # ~2.567s per unit
    needed   = max(1, int((audio_duration - TITLE_DUR) / unit_dur) + 3)
    n_clips  = len(clip_paths)

    segments = []
    if title_path and os.path.exists(title_path):
        segments.append(title_path)

    for i in range(needed):
        clip = clip_paths[i % n_clips]
        if flash_path and os.path.exists(flash_path):
            segments.append(flash_path)
        segments.append(clip)

    concat_txt = os.path.join(tmp, "concat.txt")
    with open(concat_txt, "w") as f:
        for p in segments:
            f.write(f"file '{p}'\n")

    out = os.path.join(tmp, "concat.mp4")
    ok = ffmpeg_run(["-f", "concat", "-safe", "0", "-i", concat_txt,
                     "-c", "copy", out], timeout=120, label="concat-copy")
    if ok and os.path.exists(out):
        return out

    # Re-encode fallback
    log("Concat stream-copy failed, re-encoding")
    ok = ffmpeg_run(["-f", "concat", "-safe", "0", "-i", concat_txt,
                     "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                     out], timeout=240, label="concat-reencode")
    return out if ok and os.path.exists(out) else None


# ─── Watermark text filter ────────────────────────────────────────────────────

def _watermark_filter():
    """Return ffmpeg drawtext filter string for the WealthShock watermark."""
    # Use fontfile path if known, else rely on fontconfig
    if CAPTION_FONT_PATH and os.path.exists(CAPTION_FONT_PATH):
        font_arg = f":fontfile='{CAPTION_FONT_PATH}'"
    else:
        font_arg = ""
    return (
        "drawtext=text='WealthShock'"
        f"{font_arg}"
        ":fontsize=34"
        ":fontcolor=white@0.35"
        ":x=w-tw-24:y=h-th-24"
    )


# ─── Final render ─────────────────────────────────────────────────────────────

def final_render(video_path, audio_path, ass_path, output):
    vf_parts = []

    # 1. Burned-in captions
    if ass_path and os.path.exists(ass_path):
        safe_ass  = ass_path.replace("\\", "/").replace("'", "\\'")
        safe_fdir = FONT_DIR.replace("'", "\\'")
        vf_parts.append(f"ass='{safe_ass}':fontsdir='{safe_fdir}'")

    # 2. Semi-transparent watermark
    vf_parts.append(_watermark_filter())

    vf = ",".join(vf_parts)

    ok = ffmpeg_run([
        "-i", video_path,
        "-i", audio_path,
        "-vf", vf,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "26",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        "-shortest",
        output,
    ], timeout=360, label="final-render")

    if ok and os.path.exists(output) and os.path.getsize(output) > 0:
        return True

    # Fallback: skip captions, keep watermark
    log("Final render failed — retrying without captions")
    vf_fallback = _watermark_filter()
    ok = ffmpeg_run([
        "-i", video_path, "-i", audio_path,
        "-vf", vf_fallback,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "fast", "-crf", "26",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
        "-movflags", "+faststart",
        "-shortest", output,
    ], timeout=360, label="final-render-fallback")

    return ok and os.path.exists(output) and os.path.getsize(output) > 0


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    audio_url        = os.environ.get("AUDIO_URL", "").strip()
    stock_urls_json  = os.environ.get("STOCK_VIDEO_URLS", "[]").strip()
    title            = os.environ.get("TITLE", "WealthShock").strip()
    script           = os.environ.get("SCRIPT", "").strip()

    if not audio_url:
        log("ERROR: AUDIO_URL env var is required")
        sys.exit(1)

    try:
        stock_urls = json.loads(stock_urls_json)
        assert isinstance(stock_urls, list) and len(stock_urls) >= 1
    except Exception:
        log(f"ERROR: STOCK_VIDEO_URLS must be a non-empty JSON array")
        sys.exit(1)

    tmp = tempfile.mkdtemp()
    log(f"Workspace: {tmp}")
    log(f"Stock clips: {len(stock_urls)}, audio: {audio_url[:60]}")

    try:
        # ── Step 1: Download voiceover ────────────────────────────────────────
        voice_mp3 = os.path.join(tmp, "voice.mp3")
        log("Downloading voiceover...")
        if not stream_download(audio_url, voice_mp3):
            log("ERROR: Voiceover download failed")
            sys.exit(1)
        log(f"Voiceover: {os.path.getsize(voice_mp3)//1024}KB")

        audio_dur = get_duration(voice_mp3) or 60.0
        log(f"Audio duration: {audio_dur:.2f}s")

        # ── Step 2: Word-synced captions ──────────────────────────────────────
        ass_path = os.path.join(tmp, "captions.ass")
        has_caps = bool(script) and make_ass_captions(script, audio_dur, ass_path)

        # ── Step 3: Download + process stock clips in parallel ────────────────
        log(f"Downloading and processing {len(stock_urls)} clips...")
        work_items = [(url, i, tmp) for i, url in enumerate(stock_urls)]

        processed = {}
        # Parallel download+process: I/O bound download + CPU zoompan
        # max_workers=4 to saturate GHA's 4 vCPUs
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for idx, result_path in pool.map(_download_and_process, work_items):
                if result_path:
                    processed[idx] = result_path

        clip_paths = [processed[k] for k in sorted(processed)]
        log(f"Processed {len(clip_paths)}/{len(stock_urls)} clips successfully")

        if not clip_paths:
            log("ERROR: No clips processed")
            sys.exit(1)

        # ── Step 4: Opening title card ────────────────────────────────────────
        title_card = make_title_card(title, tmp)
        if not title_card:
            log("Warning: title card failed, continuing without it")

        # ── Step 5: White flash transition clip ───────────────────────────────
        flash_clip = make_flash_clip(tmp)

        # ── Step 6: Background music ──────────────────────────────────────────
        music_path = download_music(tmp)

        # ── Step 7: Mix audio ─────────────────────────────────────────────────
        if music_path:
            audio_final = mix_audio(voice_mp3, music_path, tmp)
        else:
            audio_final = voice_mp3  # just use MP3 directly as fallback

        # ── Step 8: Concatenate all video segments ────────────────────────────
        log("Concatenating segments...")
        concat_mp4 = concat_clips(clip_paths, flash_clip, title_card, audio_dur, tmp)
        if not concat_mp4:
            log("ERROR: Video concat failed")
            sys.exit(1)
        log(f"Concat: {os.path.getsize(concat_mp4)//1024}KB")

        # ── Step 9: Final render ──────────────────────────────────────────────
        log("Final render: captions + watermark + audio...")
        ok = final_render(concat_mp4, audio_final, ass_path if has_caps else None, OUTPUT)
        if not ok:
            log("ERROR: Final render failed")
            sys.exit(1)

        size_mb = os.path.getsize(OUTPUT) / (1024 * 1024)
        log(f"SUCCESS  →  {OUTPUT}  ({size_mb:.1f} MB)")

    finally:
        try:
            shutil.rmtree(tmp)
            log("Workspace cleaned up")
        except Exception as exc:
            log(f"Cleanup warning: {exc}")


if __name__ == "__main__":
    main()
