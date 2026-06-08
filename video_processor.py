#!/usr/bin/env python3
"""
WealthShock Video Processor — runs in GitHub Actions (14 GB RAM, full ffmpeg)
Reads inputs from env vars, writes final_video.mp4 to the current directory.

Env vars required:
  AUDIO_URL         — direct download URL for the MP3 voiceover
  STOCK_VIDEO_URLS  — JSON array of Pixabay video URLs (finance/money clips)
  TITLE             — video title for animated title card
  SCRIPT            — spoken script text for word-synced captions
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from textwrap import wrap

import requests
from PIL import Image, ImageDraw, ImageFont

FFMPEG_BIN = "ffmpeg"
FFPROBE_BIN = "ffprobe"
OUTPUT_FILE = "final_video.mp4"
THUMBNAIL_SIZE = (1080, 1920)
FPS = 25
CLIP_DURATION = 3.8    # seconds per stock clip (dynamic cut every ~3-4s)
TITLE_DURATION = 3.0   # seconds for opening title card
MUSIC_VOLUME = 0.10    # background music at 10%


# ─── Utilities ────────────────────────────────────────────────────────────────

def log(msg):
    print(f"[processor] {msg}", flush=True)


def stream_download(url, dest_path, chunk_size=65536):
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
            if os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                return True
        except Exception as exc:
            log(f"Download attempt {attempt + 1} failed ({url}): {exc}")
            time.sleep(3)
    return False


def run_ffmpeg(args, timeout=180):
    cmd = [FFMPEG_BIN, "-hide_banner", "-loglevel", "error"] + args
    result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        log(f"ffmpeg error: {result.stderr.decode()[-500:]}")
    return result.returncode == 0


def get_duration(path):
    try:
        result = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return float(result.stdout.strip() or 0)
    except Exception as exc:
        log(f"Duration probe failed for {path}: {exc}")
    return None


# ─── Fonts ────────────────────────────────────────────────────────────────────

def load_font(size):
    # Prefer DejaVu (always on Ubuntu), then Liberation, then Ubuntu font
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/open-sans/OpenSans-Bold.ttf",
        "/Library/Fonts/Impact.ttf",          # macOS fallback
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


# ─── Gradient background ──────────────────────────────────────────────────────

def make_gradient_background():
    width, height = THUMBNAIL_SIZE
    base = Image.new("RGB", THUMBNAIL_SIZE, "#111111")
    top = Image.new("RGB", THUMBNAIL_SIZE, "#841515")
    mask = Image.new("L", THUMBNAIL_SIZE)
    mask.putdata([int(255 * (y / height)) for y in range(height) for _ in range(width)])
    base.paste(top, (0, 0), mask)
    return base


# ─── ASS captions (word-by-word sync) ────────────────────────────────────────

def format_ass_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    cs = int((s - int(s)) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


def generate_ass_captions(script, duration, ass_path):
    words = re.findall(r"\S+", script)
    if not words:
        log("No caption words found in script")
        return False
    interval = max(0.18, duration / max(len(words), 1))
    try:
        font_dir = "/usr/share/fonts/truetype/dejavu"
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("[Script Info]\n")
            f.write("ScriptType: v4.00+\n")
            f.write("PlayResX: 1080\n")
            f.write("PlayResY: 1920\n\n")
            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                    "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                    "Alignment, MarginL, MarginR, MarginV, Encoding\n")
            f.write("Style: Default,DejaVu Sans Bold,72,&H00FFFFFF,&H00000000,"
                    "&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,4,0,8,0,0,120,1\n\n")
            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
            current = 0.0
            for word in words:
                end = min(duration, current + interval)
                safe = word.replace("{", "\\{").replace("}", "\\}")
                f.write(f"Dialogue: 0,{format_ass_time(current)},{format_ass_time(end)},"
                        f"Default,,0,0,0,,{safe}\n")
                current = end
                if current >= duration:
                    break
        log(f"ASS captions written: {len(words)} words over {duration:.1f}s")
        return True
    except Exception as exc:
        log(f"ASS caption generation failed: {exc}")
        return False


# ─── Title card (animated zoom) ──────────────────────────────────────────────

def generate_title_card(title_text, tmp):
    image_path = os.path.join(tmp, "title_card.png")
    video_path = os.path.join(tmp, "title_card.mp4")
    try:
        bg = make_gradient_background()
        draw = ImageDraw.Draw(bg)
        font = load_font(120)
        lines = wrap(title_text.upper(), width=12)
        y = 360
        for line in lines[:4]:
            bbox = draw.textbbox((0, 0), line, font=font)
            x = max(40, (THUMBNAIL_SIZE[0] - (bbox[2] - bbox[0])) // 2)
            draw.text((x, y), line, font=font, fill="#FFD700", stroke_width=6, stroke_fill="#000000")
            y += (bbox[3] - bbox[1]) + 24
        bg.save(image_path)
    except Exception as exc:
        log(f"Title card image failed: {exc}")
        return None

    frames = int(TITLE_DURATION * FPS)
    zoom_expr = f"if(lte(on,1),1,1+0.05*on/{frames})"
    filt = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"zoompan=z='{zoom_expr}':d={frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920,"
        f"fps={FPS}"
    )
    ok = run_ffmpeg([
        "-y", "-framerate", str(FPS), "-loop", "1", "-i", image_path,
        "-t", str(TITLE_DURATION), "-vf", filt,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", video_path,
    ], timeout=120)
    if ok and os.path.exists(video_path):
        log("Title card rendered")
        return video_path
    log("Title card video failed")
    return None


# ─── Stock clip processing (zoom + fade in/out) ───────────────────────────────

def process_clip(url, index, tmp):
    raw = os.path.join(tmp, f"clip_{index}_raw.mp4")
    out = os.path.join(tmp, f"clip_{index}.mp4")

    if not stream_download(url, raw):
        log(f"Clip {index}: download failed")
        return None

    frames = int(CLIP_DURATION * FPS)
    fade_out_start = CLIP_DURATION - 0.5
    zoom_expr = f"if(lte(on,1),1,1+0.05*on/{frames})"

    filt_zoom = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"zoompan=z='{zoom_expr}':d={frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920,"
        f"fps={FPS},"
        f"fade=t=in:st=0:d=0.5,"
        f"fade=t=out:st={fade_out_start:.2f}:d=0.5"
    )
    filt_simple = (
        f"scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,"
        f"fps={FPS},"
        f"fade=t=in:st=0:d=0.5,"
        f"fade=t=out:st={fade_out_start:.2f}:d=0.5"
    )

    for filt in [filt_zoom, filt_simple]:
        ok = run_ffmpeg([
            "-y", "-i", raw, "-t", str(CLIP_DURATION),
            "-filter_complex", filt,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", out,
        ], timeout=120)
        if ok and os.path.exists(out) and os.path.getsize(out) > 0:
            try:
                os.remove(raw)
            except Exception:
                pass
            log(f"Clip {index} processed ({os.path.getsize(out) // 1024}KB)")
            return out
        log(f"Clip {index}: filter attempt failed, trying simpler filter")

    try:
        os.remove(raw)
    except Exception:
        pass
    log(f"Clip {index}: all processing attempts failed")
    return None


# ─── Audio: MP3 → AAC + mix with background music ────────────────────────────

def convert_mp3_to_aac(mp3_path, aac_path):
    ok = run_ffmpeg(["-y", "-i", mp3_path, "-c:a", "aac", "-ar", "44100", "-b:a", "192k", aac_path])
    return ok and os.path.exists(aac_path)


def download_background_music(dest_path):
    # Free royalty-free music from Mixkit (no account required)
    fallback_urls = [
        "https://assets.mixkit.co/music/preview/mixkit-dark-cinematic-drums-570.mp3",
        "https://assets.mixkit.co/music/preview/mixkit-hip-hop-02-738.mp3",
    ]
    for url in fallback_urls:
        if stream_download(url, dest_path):
            log(f"Background music downloaded from {url}")
            return True
    return False


def mix_voice_with_music(voice_aac, music_mp3, output_aac):
    ok = run_ffmpeg([
        "-y",
        "-i", voice_aac,
        "-stream_loop", "-1", "-i", music_mp3,
        "-filter_complex",
        f"[1:a]volume={MUSIC_VOLUME}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[out]",
        "-map", "[out]",
        "-c:a", "aac", "-ar", "44100", "-b:a", "192k",
        output_aac,
    ], timeout=120)
    if ok and os.path.exists(output_aac):
        log("Voice + music mixed")
        return output_aac
    log("Music mix failed — using voice only")
    return voice_aac


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    audio_url = os.environ.get("AUDIO_URL", "").strip()
    stock_urls_json = os.environ.get("STOCK_VIDEO_URLS", "[]").strip()
    title = os.environ.get("TITLE", "WealthShock").strip()
    script = os.environ.get("SCRIPT", "").strip()

    if not audio_url:
        log("ERROR: AUDIO_URL is required")
        sys.exit(1)

    try:
        stock_urls = json.loads(stock_urls_json)
        assert isinstance(stock_urls, list) and len(stock_urls) > 0
    except Exception:
        log(f"ERROR: Invalid STOCK_VIDEO_URLS: {stock_urls_json[:200]}")
        sys.exit(1)

    tmp = tempfile.mkdtemp()
    log(f"Temp directory: {tmp}")

    try:
        # 1. Download voiceover MP3
        audio_mp3 = os.path.join(tmp, "voice.mp3")
        log(f"Downloading audio from {audio_url}")
        if not stream_download(audio_url, audio_mp3):
            log("ERROR: Audio download failed")
            sys.exit(1)
        log(f"Audio downloaded: {os.path.getsize(audio_mp3) // 1024}KB")

        # 2. Get audio duration
        duration = get_duration(audio_mp3) or 60.0
        log(f"Audio duration: {duration:.2f}s")

        # 3. Generate word-synced ASS captions
        ass_path = os.path.join(tmp, "captions.ass")
        has_captions = bool(script) and generate_ass_captions(script, duration, ass_path)

        # 4. Convert MP3 → AAC
        audio_aac = os.path.join(tmp, "voice.aac")
        if not convert_mp3_to_aac(audio_mp3, audio_aac):
            log("MP3→AAC conversion failed — using MP3 directly")
            audio_aac = audio_mp3

        # 5. Mix with background music (10% volume)
        music_mp3 = os.path.join(tmp, "music.mp3")
        if download_background_music(music_mp3):
            mixed_aac = os.path.join(tmp, "mixed.aac")
            audio_final = mix_voice_with_music(audio_aac, music_mp3, mixed_aac)
        else:
            log("No background music — voice only")
            audio_final = audio_aac

        # 6. Animated title card (3s with zoom)
        title_card = generate_title_card(title, tmp)

        # 7. Process stock clips (zoom + fade, 3.8s each)
        processed_clips = []
        for i, url in enumerate(stock_urls[:6]):
            clip = process_clip(url, i, tmp)
            if clip:
                processed_clips.append(clip)

        if not processed_clips:
            log("ERROR: No stock clips processed")
            sys.exit(1)

        # 8. Build concat list: title card + enough clips to cover audio
        title_dur = TITLE_DURATION if title_card else 0.0
        remaining = max(0.0, duration - title_dur)
        clips_needed = max(1, int(remaining / CLIP_DURATION) + 2)

        concat_clips = []
        if title_card:
            concat_clips.append(title_card)
        for i in range(clips_needed):
            concat_clips.append(processed_clips[i % len(processed_clips)])

        concat_list = os.path.join(tmp, "concat.txt")
        with open(concat_list, "w") as f:
            for p in concat_clips:
                f.write(f"file '{p}'\n")

        # 9. Concat video segments
        concat_mp4 = os.path.join(tmp, "concat.mp4")
        ok = run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                         "-c", "copy", concat_mp4], timeout=120)
        if not ok or not os.path.exists(concat_mp4):
            log("Concat (stream copy) failed — re-encoding")
            ok = run_ffmpeg(["-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                             "-c:v", "libx264", "-pix_fmt", "yuv420p", concat_mp4], timeout=180)
        if not ok or not os.path.exists(concat_mp4):
            log("ERROR: Video concat failed")
            sys.exit(1)

        # 10. Final render: video + audio + burned-in captions (1080×1920, no black bars)
        # Build video filter chain
        vf_parts = []
        if has_captions and os.path.exists(ass_path):
            safe_ass = ass_path.replace("\\", "/").replace("'", "\\'")
            font_dir = "/usr/share/fonts/truetype/dejavu"
            vf_parts.append(f"ass='{safe_ass}':fontsdir='{font_dir}'")
        vf_str = ",".join(vf_parts) if vf_parts else "null"

        final_cmd = [
            "-y",
            "-i", concat_mp4,
            "-i", audio_final,
            "-vf", vf_str,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-shortest",
            OUTPUT_FILE,
        ]
        ok = run_ffmpeg(final_cmd, timeout=300)

        if not ok or not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
            log("Final render failed — retrying without captions")
            fallback_cmd = [
                "-y",
                "-i", concat_mp4,
                "-i", audio_final,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
                "-shortest",
                OUTPUT_FILE,
            ]
            ok = run_ffmpeg(fallback_cmd, timeout=300)
            if not ok or not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
                log("ERROR: All render attempts failed")
                sys.exit(1)

        size_mb = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
        log(f"SUCCESS: {OUTPUT_FILE} ({size_mb:.1f} MB)")

    finally:
        try:
            shutil.rmtree(tmp)
            log("Temp directory cleaned up")
        except Exception as exc:
            log(f"Cleanup error: {exc}")


if __name__ == "__main__":
    main()
