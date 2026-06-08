import gc
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from io import BytesIO
from textwrap import wrap

import imageio_ffmpeg
import pytz
import requests
import schedule
from dotenv import load_dotenv
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

load_dotenv()
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AB_TEST_FILE = os.path.join(BASE_DIR, "ab_tests.json")
THUMBNAIL_SIZE = (1080, 1920)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY")
YT_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")

YOUTUBE_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

TOPICS = [
    "shocking money facts most people don't know",
    "AI tools that will make you rich in 2025",
    "why the rich get richer and poor get poorer",
    "financial mistakes destroying your future",
    "passive income secrets banks hide from you",
    "investing habits of billionaires revealed",
    "the truth about the stock market nobody tells you",
    "side hustles making people millionaires",
    "crypto facts that will blow your mind",
    "how to save money like the ultra wealthy",
    "mindset shifts that made people millionaires",
    "things school never taught you about money",
]

MARKET_PEAK_HOURS = {
    "US": {"tz": "America/New_York", "hours": [12, 17, 20]},
    "IL": {"tz": "Asia/Jerusalem", "hours": [12, 16, 20]},
    "JP": {"tz": "Asia/Tokyo", "hours": [12, 19, 21]},
    "CN": {"tz": "Asia/Shanghai", "hours": [12, 18, 20]},
}

WEEKDAY_MAP = {
    "monday": "monday",
    "tuesday": "tuesday",
    "wednesday": "wednesday",
    "thursday": "thursday",
    "friday": "friday",
    "saturday": "saturday",
    "sunday": "sunday",
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_json_file(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log(f"Failed to read JSON {path}: {exc}")
        return default


def save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        log(f"Failed to write JSON {path}: {exc}")


def get_yt_access_token():
    if not YT_CLIENT_ID or not YT_CLIENT_SECRET or not YT_REFRESH_TOKEN:
        log("Missing YouTube OAuth environment variables")
        return None
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": YT_CLIENT_ID,
            "client_secret": YT_CLIENT_SECRET,
            "refresh_token": YT_REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    data = resp.json()
    if resp.status_code != 200 or "access_token" not in data:
        log(f"YouTube auth error: {data}")
        return None
    return data["access_token"]


def get_youtube_oauth_url(redirect_uri, access_type="offline"):
    if not YT_CLIENT_ID:
        return None
    scope = "+".join(YOUTUBE_OAUTH_SCOPES)
    return (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={YT_CLIENT_ID}"
        f"&redirect_uri={redirect_uri}"
        "&response_type=code"
        f"&scope={scope}"
        f"&access_type={access_type}"
        "&prompt=consent"
    )


def get_channel_best_days():
    # Removed: YouTube Analytics API returns 403 (insufficient scopes)
    # Use market peak hours schedule instead
    log("Analytics dependency removed; using market peak hours schedule")
    return []


def trending_topics(region="US", count=8):
    if not YOUTUBE_KEY:
        log("Missing YouTube API key for trending topics")
        return []
    try:
        url = "https://www.googleapis.com/youtube/v3/videos"
        resp = requests.get(
            url,
            params={
                "part": "snippet",
                "chart": "mostPopular",
                "regionCode": region,
                "maxResults": count * 3,  # Fetch more to filter
                "key": YOUTUBE_KEY,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            log(f"YouTube trending status {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        items = data.get("items", [])
        
        # Filter for finance/money/AI/business keywords
        FINANCE_KEYWORDS = ["money", "finance", "invest", "trading", "stock", "crypto", "bitcoin", "wealth", "rich",
                           "ai", "artificial intelligence", "business", "startup", "entrepreneur", "income",
                           "saving", "debt", "loan", "bank", "market", "economic", "gdp", "growth"]
        
        topics = []
        for item in items:
            title = item.get("snippet", {}).get("title", "").lower()
            if title and any(keyword in title for keyword in FINANCE_KEYWORDS):
                original_title = item.get("snippet", {}).get("title", "").strip()
                if original_title:
                    topics.append(original_title)
            if len(topics) >= count:
                break
        
        if topics:
            log(f"YouTube Trending discovered {len(topics)} finance topics")
            return topics
        else:
            log("No finance topics in trending; using curated list")
            return []
    except Exception as exc:
        log(f"Trend discovery failed: {exc}")
        return []


def find_viral_video(topic):
    if not YOUTUBE_KEY:
        log("Missing YouTube API key for viral video discovery")
        return None
    try:
        url = "https://www.googleapis.com/youtube/v3/search"
        resp = requests.get(
            url,
            params={
                "part": "snippet",
                "q": topic,
                "type": "video",
                "order": "viewCount",
                "maxResults": 3,
                "regionCode": "US",
                "key": YOUTUBE_KEY,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            log(f"Viral video search error {resp.status_code}: {resp.text[:200]}")
            return None
        items = resp.json().get("items", [])
        for item in items:
            video_id = item.get("id", {}).get("videoId")
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
    except Exception as exc:
        log(f"Viral video discovery failed: {exc}")
    return None


def choose_topic():
    trending = trending_topics()
    if trending:
        pool = trending + TOPICS
        topic = random.choice(pool)
        log(f"Selected trending topic: {topic}")
        return topic
    topic = random.choice(TOPICS)
    log(f"Selected curated topic: {topic}")
    return topic


def request_with_retries(method, url, max_retries=3, delay=5, **kwargs):
    attempt = 0
    while attempt < max_retries:
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code == 503:
                attempt += 1
                log(f"Received 503 from {url}, retrying {attempt}/{max_retries} after {delay}s")
                time.sleep(delay)
                continue
            return resp
        except requests.RequestException as exc:
            attempt += 1
            log(f"Request exception to {url}, retry {attempt}/{max_retries}: {exc}")
            time.sleep(delay)
    return None


def generate_script(topic):
    # Prefer Gemini when API key is provided, otherwise use a local template fallback
    if GEMINI_KEY:
        try:
            prompt = f"""Create a 60-second viral YouTube Shorts script about: {topic}
- Start with an emotional hook that feels shocking or controversial
- Include 3 brief eye-opening facts or secrets with believable numbers
- Add a counterintuitive twist or belief that people disagree with
- End with a powerful CTA: follow, save, or share
Return ONLY the spoken script, with clear sentence breaks."""
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
            r = request_with_retries("POST", url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
            if r is not None:
                data = r.json()
                if "candidates" in data:
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                log(f"Gemini script error: {data}")
            else:
                log("Gemini script request failed after retries")
        except Exception as exc:
            log(f"Gemini call failed: {exc}")

    # Local fallback: craft a short scripted narrative with hooks/facts/CTA
    def local_generate_script(topic_text):
        hook = f"You won't believe this about {topic_text.split()[0]}!"
        facts = []
        for i in range(3):
            val = random.randint(2, 95)
            facts.append(f"Fact {i+1}: {val}% of people are surprised by this about {topic_text}.")
        twist = f"But here's the twist: most advice gets this backwards — {topic_text} works differently."
        cta = "If you want more, follow and save this video."
        parts = [hook] + facts + [twist, cta]
        return " ".join(parts)

    return local_generate_script(topic)


def generate_title(topic, variant=None):
    # Try Gemini first
    if GEMINI_KEY:
        try:
            prompt = f"Write a viral YouTube Shorts title under 60 characters about: {topic}. Use strong psychological triggers, controversy, or shocking value."
            if variant:
                prompt += f" Create an alternate title that is different from the first one. Mark it as variant {variant}."
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
            r = request_with_retries("POST", url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
            if r is not None:
                data = r.json()
                if "candidates" in data:
                    return data["candidates"][0]["content"]["parts"][0]["text"].strip()
                log(f"Gemini title error: {data}")
            else:
                log("Gemini title request failed after retries")
        except Exception as exc:
            log(f"Gemini title call failed: {exc}")

    # Local fallback title
    base = topic.title()
    if variant:
        return f"{base} — {variant}"
    return f"{base} #shorts"


def sanitize_hashtag(text):
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text)
    if not cleaned:
        return None
    return f"#{cleaned.lower()}"


def fallback_hashtags(topic, title):
    words = re.findall(r"[A-Za-z0-9]+", f"{topic} {title}")
    tags = {sanitize_hashtag(word) for word in words if len(word) > 2}
    tags = [tag for tag in tags if tag]
    core = ["#shorts", "#finance", "#money", "#wealth", "#viral"]
    return core + tags[:8]


def generate_hashtags(topic, title):
    if GEMINI_KEY:
        try:
            prompt = f"Generate 10 viral YouTube hashtags for this Shorts topic and title: {topic} / {title}. Focus on finance, money, wealth, AI, viral growth, and viewer curiosity. Return only hashtags separated by spaces."
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
            r = request_with_retries("POST", url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
            if r is not None:
                data = r.json()
                if "candidates" in data:
                    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                    tags = [sanitize_hashtag(tag) for tag in re.split(r"\s+", text) if tag.startswith("#")]
                    if tags:
                        return tags[:12]
                log(f"Gemini hashtags error: {data}")
            else:
                log("Gemini hashtags request failed after retries")
        except Exception as exc:
            log(f"Gemini hashtags call failed: {exc}")

    return fallback_hashtags(topic, title)


def get_stock_videos(topic, count=4):
    # Limit to 4 max so the edit stays lightweight and memory-friendly
    count = min(count, 4)
    search_terms = ["money", "cash", "wealth", "stock market", "bitcoin", "business"]
    query = " ".join([topic] + search_terms) if topic else " ".join(search_terms)
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "per_page": count * 4,
                "video_type": "film",
                "safesearch": "true",
                "order": "popular",
            },
            timeout=20,
        )
        data = r.json()
        hits = data.get("hits", [])
        urls = []
        for h in hits:
            videos = h.get("videos", {})
            for quality in ["small", "medium", "large"]:
                url = videos.get(quality, {}).get("url")
                if url and url.startswith("https://"):
                    urls.append(url)
                    break
        if not urls:
            log("Pixabay returned no finance video URLs, falling back to money query")
            return get_stock_videos("money", count)
        log(f"Pixabay found {len(urls)} finance clips, using {min(len(urls), count)}")
        return urls[:count]
    except Exception as exc:
        log(f"Stock video fetch failed: {exc}")
        return []


def get_stock_image(topic):
    query = topic if topic else "finance"
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            params={"key": PIXABAY_KEY, "q": query, "image_type": "photo", "per_page": 10, "safesearch": "true"},
            timeout=20,
        )
        data = r.json()
        hits = data.get("hits", [])
        for hit in hits:
            for key in ["largeImageURL", "webformatURL", "previewURL"]:
                source = hit.get(key)
                if source and source.startswith("https://"):
                    return source
    except Exception as exc:
        log(f"Stock image fetch failed: {exc}")
    return None


def format_ass_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    centiseconds = int((secs - int(secs)) * 100)
    return f"{hours}:{minutes:02d}:{int(secs):02d}.{centiseconds:02d}"


def escape_ass_path(path):
    return path.replace("'", "\\'")


def generate_video_ass_captions(script, duration, ass_path):
    words = [w for w in re.findall(r"[^\s]+", script)]
    if not words:
        log("No caption words generated")
        return None

    interval = max(0.18, duration / len(words))
    try:
        with open(ass_path, "w", encoding="utf-8") as f:
            f.write("[Script Info]\n")
            f.write("ScriptType: v4.00+\n")
            f.write("PlayResX: 1080\n")
            f.write("PlayResY: 1920\n")
            f.write("\n")
            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
            f.write("Style: Default,Impact,72,&H00FFFFFF,&H00000000,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,4,0,8,0,0,120,1\n")
            f.write("\n")
            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
            current = 0.0
            for word in words:
                end = min(duration, current + interval)
                safe_word = word.replace("{", "\\{").replace("}", "\\}")
                f.write(
                    f"Dialogue: 0,{format_ass_time(current)},{format_ass_time(end)},Default,,0,0,0,,{safe_word}\n"
                )
                current = end
                if current >= duration:
                    break
        log(f"Caption ASS generated: {ass_path}")
        return ass_path
    except Exception as exc:
        log(f"Caption ASS generation failed: {exc}")
        return None


def get_media_duration(path):
    try:
        result = subprocess.run(
            [
                FFMPEG_BIN,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log(f"Duration probe failed: {result.stderr.strip()}")
            return None
        return float(result.stdout.strip() or 0.0)
    except Exception as exc:
        log(f"Media duration failed: {exc}")
        return None


def download_background_music(dest_path):
    fallback_url = "https://assets.mixkit.co/music/preview/mixkit-dark-cinematic-drums-570.mp3"
    try:
        if PIXABAY_KEY:
            r = requests.get(
                "https://pixabay.com/api/audio/",
                params={"key": PIXABAY_KEY, "q": "lofi dramatic cinematic", "per_page": 10, "safesearch": "true"},
                timeout=20,
            )
            data = r.json()
            hits = data.get("hits", [])
            for hit in hits:
                candidate = hit.get("download_url") or hit.get("previews", {}).get("mp3")
                if candidate and candidate.startswith("https://"):
                    if stream_download(candidate, dest_path):
                        log(f"Background music downloaded from Pixabay: {candidate}")
                        return True
        if stream_download(fallback_url, dest_path):
            log(f"Background music downloaded from fallback URL")
            return True
    except Exception as exc:
        log(f"Background music download failed: {exc}")
    return False


def mix_voice_with_music(voice_path, music_path, output_path):
    if not os.path.exists(music_path):
        return voice_path
    try:
        result = subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                voice_path,
                "-stream_loop",
                "-1",
                "-i",
                music_path,
                "-filter_complex",
                "[1:a]volume=0.10[a1];[0:a][a1]amix=inputs=2:duration=first:dropout_transition=2",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "44100",
                output_path,
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.exists(output_path):
            log(f"Music mix failed: {result.stderr.decode()[-300:]}")
            return voice_path
        log(f"Voice and music mixed: {output_path}")
        return output_path
    except Exception as exc:
        log(f"Music mixing failed: {exc}")
        return voice_path


def generate_title_card_video(title_text, tmp, duration=3.0):
    try:
        image_path = os.path.join(tmp, "title_card.png")
        video_path = os.path.join(tmp, "title_card.mp4")
        bg = make_gradient_background(THUMBNAIL_SIZE)
        draw = ImageDraw.Draw(bg)
        title_font = load_font(120)
        lines = wrap(title_text.upper(), width=12)
        y = 360
        for line in lines[:4]:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            text_width = bbox[2] - bbox[0]
            x = max(40, (THUMBNAIL_SIZE[0] - text_width) // 2)
            draw.text((x, y), line, font=title_font, fill="#FFD700", stroke_width=6, stroke_fill="#000000")
            y += bbox[3] - bbox[1] + 24
        bg.save(image_path)

        frames = int(duration * 25)
        zoom_expr = f"if(lte(on,1),1,1+0.05*on/{frames})"
        filter_expr = (
            f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"zoompan=z='{zoom_expr}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920,"
            f"fps=25"
        )
        result = subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-framerate",
                "25",
                "-loop",
                "1",
                "-i",
                image_path,
                "-t",
                str(duration),
                "-vf",
                filter_expr,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                video_path,
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.exists(video_path):
            log(f"Title card video creation failed: {result.stderr.decode()[-300:]}")
            return None
        return video_path
    except Exception as exc:
        log(f"Title card creation failed: {exc}")
        return None


def process_stock_clip(url, index, tmp, duration=3.8):
    input_path = os.path.join(tmp, f"clip_{index}.mp4")
    output_path = os.path.join(tmp, f"clip_{index}_proc.mp4")
    if not stream_download(url, input_path, chunk_size=8192):
        log(f"Failed to download clip {url}")
        return None
    frames = int(duration * 25)
    try:
        zoom_expr = f"if(lte(on,1),1,1+0.05*on/{frames})"
        filter_expr = (
            f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
            f"zoompan=z='{zoom_expr}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920,"
            f"fps=25,fade=t=in:st=0:d=0.5,fade=t=out:st={duration - 0.5}:d=0.5"
        )
        result = subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                input_path,
                "-t",
                str(duration),
                "-filter_complex",
                filter_expr,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-an",
                output_path,
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.exists(output_path):
            log(f"Clip processing failed, retrying without zoom: {result.stderr.decode()[-300:]}")
            result = subprocess.run(
                [
                    FFMPEG_BIN,
                    "-y",
                    "-i",
                    input_path,
                    "-t",
                    str(duration),
                    "-vf",
                    "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=25,fade=t=in:st=0:d=0.5,fade=t=out:st={:.2f}:d=0.5".format(duration - 0.5),
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-an",
                    output_path,
                ],
                capture_output=True,
            )
        if result.returncode != 0 or not os.path.exists(output_path):
            log(f"Clip processing ultimately failed: {result.stderr.decode()[-300:]}")
            return None
        return output_path
    except Exception as exc:
        log(f"Clip processing exception: {exc}")
        return None


def create_video_local(audio_path, stock_video_urls, title_text, script, duration=60):
    if not os.path.exists(audio_path):
        log("Audio path missing for video creation")
        return None
    if not stock_video_urls:
        log("No stock videos available for premium edit")
        return None

    tmp = tempfile.mkdtemp()
    try:
        music_path = os.path.join(tmp, "background_music.mp3")
        if download_background_music(music_path):
            mixed_path = os.path.join(tmp, "mixed_audio.wav")
            audio_input_path = mix_voice_with_music(audio_path, music_path, mixed_path)
        else:
            log("Proceeding without background music")
            audio_input_path = audio_path

        audio_duration = get_media_duration(audio_input_path) or duration
        target_duration = min(duration, audio_duration)
        captions_path = os.path.join(tmp, "captions.ass")
        if not generate_video_ass_captions(script, audio_duration, captions_path):
            log("Caption generation failed")
            return None

        title_video = generate_title_card_video(title_text, tmp, duration=3.0)
        if not title_video:
            log("Title card generation failed")
            return None

        processed_clips = []
        for index, url in enumerate(stock_video_urls):
            clip_path = process_stock_clip(url, index, tmp, duration=3.8)
            if clip_path:
                processed_clips.append(clip_path)
        if not processed_clips:
            log("No processed stock clips available")
            return None

        clip_duration = 3.8
        title_duration = 3.0
        remaining = max(0.0, target_duration - title_duration)
        segments_needed = max(1, int((remaining + clip_duration - 1e-9) // clip_duration))
        concat_clips = [title_video]
        for i in range(segments_needed):
            concat_clips.append(processed_clips[i % len(processed_clips)])

        concat_list = os.path.join(tmp, "concat.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for clip_path in concat_clips:
                f.write(f"file '{clip_path}'\n")

        concat_path = os.path.join(tmp, "concat.mp4")
        concat_result = subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                concat_list,
                "-c",
                "copy",
                concat_path,
            ],
            capture_output=True,
        )
        if concat_result.returncode != 0 or not os.path.exists(concat_path):
            log(f"Concat failed, retrying re-encode: {concat_result.stderr.decode()[-300:]}")
            concat_result = subprocess.run(
                [
                    FFMPEG_BIN,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    concat_list,
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    concat_path,
                ],
                capture_output=True,
            )
        if concat_result.returncode != 0 or not os.path.exists(concat_path):
            log(f"Final concat failed: {concat_result.stderr.decode()[-300:]}")
            return None

        final_output = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        subtitles_filter = (
            f"subtitles='{escape_ass_path(captions_path)}'"
            ":force_style='FontName=Impact,FontSize=72,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=4,Alignment=8,MarginV=120'"
        )
        result = subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-i",
                concat_path,
                "-i",
                audio_input_path,
                "-vf",
                subtitles_filter,
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "44100",
                "-shortest",
                final_output,
            ],
            capture_output=True,
        )
        if result.returncode != 0 or not os.path.exists(final_output):
            log(f"Final video render failed: {result.stderr.decode()[-300:]}")
            return None

        log(f"Final video ready: {final_output} ({os.path.getsize(final_output) // 1024}KB)")
        return final_output
    finally:
        try:
            if os.path.exists(tmp):
                shutil.rmtree(tmp)
        except Exception as exc:
            log(f"Failed to clean temp directory: {exc}")


def load_font(size):
    candidates = [
        "/Library/Fonts/Impact.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def make_gradient_background(size):
    width, height = size
    base = Image.new("RGB", size, "#111111")
    top = Image.new("RGB", size, "#841515")
    mask = Image.new("L", size)
    mask_data = []
    for y in range(height):
        mask_data.extend([int(255 * (y / height))] * width)
    mask.putdata(mask_data)
    base.paste(top, (0, 0), mask)
    return base


def generate_thumbnail(topic, title, hashtags=None):
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, "thumbnail.png")
        image_url = get_stock_image(topic)
        if image_url:
            image_path = os.path.join(tmp, "thumb_source.png")
            if not stream_download(image_url, image_path, chunk_size=8192):
                bg = make_gradient_background(THUMBNAIL_SIZE)
            else:
                try:
                    bg = Image.open(image_path).convert("RGB")
                    bg = bg.resize(THUMBNAIL_SIZE)
                except Exception:
                    bg = make_gradient_background(THUMBNAIL_SIZE)
        else:
            bg = make_gradient_background(THUMBNAIL_SIZE)

        draw = ImageDraw.Draw(bg)
        title_font = load_font(110)
        hook_font = load_font(140)
        tag_font = load_font(48)

        overlay = Image.new("RGBA", THUMBNAIL_SIZE, (0, 0, 0, 140))
        bg.paste(overlay, (0, 0), overlay)

        hook_text = "SHOCKING"
        title_lines = wrap(title.upper(), width=18)
        hashtag_line = " ".join(hashtags[:5]) if hashtags else "#Shorts #Finance"

        draw.text((60, 80), hook_text, font=hook_font, fill="#FFD700")
        y = 260
        for line in title_lines[:4]:
            draw.text((60, y), line, font=title_font, fill="#FFFFFF")
            bbox = draw.textbbox((60, y), line, font=title_font)
            line_height = bbox[3] - bbox[1]
            y += line_height + 12

        draw.rectangle([50, THUMBNAIL_SIZE[1] - 220, THUMBNAIL_SIZE[0] - 50, THUMBNAIL_SIZE[1] - 100], outline="#FFD700", width=6)
        draw.text((60, THUMBNAIL_SIZE[1] - 180), hashtag_line, font=tag_font, fill="#FFFFFF")
        bg.save(path)
        log(f"Thumbnail generated: {path}")
        # Return path but will be cleaned up in finally
        return path
    finally:
        # Note: we don't delete the temp directory immediately because the thumbnail file
        # is still being used by upload functions. Clean up will happen after upload.
        pass


def call_gtts(text, output_path, speaking_rate=1.0, pitch=0.0):
    try:
        tts = gTTS(text=text, lang="en", slow=False)
        with open(output_path, "wb") as f:
            tts.write_to_fp(f)
        return True
    except Exception as exc:
        log(f"gTTS error: {exc}")
        return False


def stream_download(url, dest_path, chunk_size=8192):
    try:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as exc:
        log(f"Download failed {url}: {exc}")
        return False


def convert_audio_segment(mp3_path, wav_path, tempo=1.0, volume=1.0):
    args = [
        FFMPEG_BIN,
        "-y",
        "-f",
        "mp3",
        "-i",
        mp3_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "44100",
        "-ac",
        "2",
    ]
    filters = []
    if tempo and abs(tempo - 1.0) > 0.001:
        filters.append(f"atempo={tempo:.2f}")
    if volume and abs(volume - 1.0) > 0.001:
        filters.append(f"volume={volume:.2f}")
    if filters:
        args.extend(["-af", ",".join(filters)])
    args.append(wav_path)
    result = subprocess.run(args, capture_output=True)
    if result.returncode != 0:
        log(f"Audio convert failed: {result.stderr.decode()[-300:]}")
        return False
    return True


def split_script(script):
    pieces = re.split(r"([.!?])", script)
    segments = []
    for i in range(0, len(pieces) - 1, 2):
        sentence = (pieces[i] + pieces[i + 1]).strip()
        if sentence:
            segments.append(sentence)
    if not segments and script.strip():
        segments = [script.strip()]
    return segments


def text_to_speech_fallback(text):
    tmp = tempfile.mkdtemp()
    mp3_path = os.path.join(tmp, "fallback.mp3")
    wav_path = os.path.join(tmp, "fallback.wav")
    if not call_gtts(text, mp3_path, speaking_rate=1.0, pitch=-1.0):
        try:
            if os.path.exists(tmp):
                shutil.rmtree(tmp)
        except Exception:
            pass
        return None
    if not convert_audio_segment(mp3_path, wav_path, tempo=1.0, volume=1.0):
        try:
            if os.path.exists(tmp):
                shutil.rmtree(tmp)
        except Exception:
            pass
        return None
    try:
        if os.path.exists(mp3_path):
            os.remove(mp3_path)
    except Exception:
        pass
    return wav_path


def text_to_speech_dynamic(script):
    tmp = tempfile.mkdtemp()
    segments = split_script(script)
    if not segments:
        log("No script segments for TTS")
        return None

    wav_paths = []
    for index, segment in enumerate(segments[:12]):
        segment = segment.strip()
        if not segment:
            continue
        mp3_path = os.path.join(tmp, f"segment_{index}.mp3")
        wav_path = os.path.join(tmp, f"segment_{index}.wav")
        if not call_gtts(segment, mp3_path, speaking_rate=1.0 + random.uniform(-0.08, 0.12), pitch=random.uniform(-2.0, 2.5)):
            log("Dynamic TTS failed, falling back to full text")
            return text_to_speech_fallback(script)

        tempo = 1.0
        volume = 1.0
        if segment.endswith("!") or segment.endswith("?"):
            tempo = random.uniform(1.02, 1.12)
            volume = 1.1
        elif len(segment) < 25:
            tempo = random.uniform(1.03, 1.12)
        elif len(segment) > 70:
            tempo = random.uniform(0.94, 1.0)

        if not convert_audio_segment(mp3_path, wav_path, tempo=tempo, volume=volume):
            return text_to_speech_fallback(script)
        wav_paths.append(wav_path)
        try:
            if os.path.exists(mp3_path):
                os.remove(mp3_path)
        except Exception:
            pass

    if not wav_paths:
        return text_to_speech_fallback(script)

    concat_list = os.path.join(tmp, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for wav in wav_paths:
            f.write(f"file '{wav}'\n")

    final_wav = os.path.join(tmp, "voice_final.wav")
    result = subprocess.run([
        FFMPEG_BIN,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        concat_list,
        "-c",
        "copy",
        final_wav,
    ], capture_output=True)
    if result.returncode != 0 or not os.path.exists(final_wav):
        log(f"Audio concat failed: {result.stderr.decode()[-300:]}")
        return text_to_speech_fallback(script)
    for wav in wav_paths:
        try:
            if os.path.exists(wav):
                os.remove(wav)
        except Exception:
            pass
    try:
        if os.path.exists(concat_list):
            os.remove(concat_list)
    except Exception:
        pass
    return final_wav


def normalize_video(vpath, output_path):
    result = subprocess.run([
        FFMPEG_BIN,
        "-y",
        "-i",
        vpath,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        output_path,
    ], capture_output=True)
    if result.returncode != 0:
        log(f"Video normalize failed: {result.stderr.decode()[-300:]}")
        return False
    return True


def upload_thumbnail(video_id, thumbnail_path, access_token):
    if not video_id or not os.path.exists(thumbnail_path):
        return False
    with open(thumbnail_path, "rb") as f:
        resp = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            params={"videoId": video_id},
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "image/png"},
            data=f,
            timeout=30,
        )
    if resp.status_code not in [200, 201]:
        log(f"Thumbnail upload failed: {resp.status_code} {resp.text[:200]}")
        return False
    log(f"Thumbnail uploaded for {video_id}")
    return True


def upload_video_file(video_path, title, description, tags=None, thumbnail_path=None):
    access_token = get_yt_access_token()
    if not access_token:
        log("Unable to upload without YouTube access token")
        return None

    meta = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags or [],
            "categoryId": "28",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }

    boundary = "----WebKitFormBoundaryWealthShock"
    def multipart_stream():
        yield f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8")
        yield json.dumps(meta).encode("utf-8")
        yield f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode("utf-8")
        with open(video_path, "rb") as vf:
            while True:
                chunk = vf.read(8192)
                if not chunk:
                    break
                yield chunk
        yield f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }
    url = "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=multipart"
    resp = requests.post(url, headers=headers, data=multipart_stream(), timeout=120)
    if resp.status_code not in [200, 201]:
        log(f"YouTube upload failed: {resp.status_code} {resp.text[:300]}")
        return None

    data = resp.json()
    video_id = data.get("id")
    if not video_id:
        log(f"Upload succeeded but no video ID returned: {data}")
        return None

    if thumbnail_path:
        upload_thumbnail(video_id, thumbnail_path, access_token)
    return video_id


def get_video_statistics(video_ids):
    if not YOUTUBE_KEY or not video_ids:
        return {}
    url = "https://www.googleapis.com/youtube/v3/videos"
    resp = requests.get(
        url,
        params={"part": "statistics,snippet", "id": ",".join(video_ids), "key": YOUTUBE_KEY},
        timeout=20,
    )
    if resp.status_code != 200:
        log(f"Video stats failed: {resp.status_code} {resp.text[:200]}")
        return {}
    data = resp.json()
    results = {}
    for item in data.get("items", []):
        vid = item.get("id")
        results[vid] = {
            "title": item.get("snippet", {}).get("title"),
            "views": int(item.get("statistics", {}).get("viewCount", 0)),
            "likes": int(item.get("statistics", {}).get("likeCount", 0)),
            "comments": int(item.get("statistics", {}).get("commentCount", 0)),
        }
    return results


def load_ab_tests():
    tests = load_json_file(AB_TEST_FILE, [])
    if not isinstance(tests, list):
        tests = []
    return tests


def save_ab_tests(tests):
    save_json_file(AB_TEST_FILE, tests)


def record_ab_test(topic, title_a, title_b, video_a_id, video_b_id, thumbnail_path):
    tests = load_ab_tests()
    record = {
        "topic": topic,
        "title_a": title_a,
        "title_b": title_b,
        "video_a_id": video_a_id,
        "video_b_id": video_b_id,
        "thumbnail_path": thumbnail_path,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "updated_at": None,
        "stats": {},
    }
    tests.append(record)
    save_ab_tests(tests)
    log(f"A/B test recorded: {video_a_id} vs {video_b_id}")
    return record


def update_ab_test_results():
    tests = load_ab_tests()
    if not tests:
        log("No A/B tests found")
        return
    for record in tests:
        ids = [record.get("video_a_id"), record.get("video_b_id")]
        stats = get_video_statistics([vid for vid in ids if vid])
        record["stats"] = stats
        record["updated_at"] = datetime.utcnow().isoformat() + "Z"
    save_ab_tests(tests)
    log(f"Updated {len(tests)} A/B test result records")


def build_description(topic, hashtags):
    tags = " ".join(hashtags[:12])
    return (
        f"WealthShock | {topic}\n\n"
        "Warning: the hidden rules of money, power, and AI are not what your teacher told you.\n\n"
        f"{tags}\n\n"
        "Follow for the fastest finance hacks, shocking market secrets, and money mindset truth. #shorts"
    )


def get_best_upload_windows():
    # Use market peak hours schedule (analytics removed due to 403 errors)
    log("Using market peak hours upload schedule")
    windows = []
    seen = set()
    for market, data in MARKET_PEAK_HOURS.items():
        for hour in data["hours"][:3]:
            try:
                tz = pytz.timezone(data["tz"])
                local_time = datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0)
                utc_time = local_time.astimezone(pytz.utc)
                if utc_time.strftime("%H:%M") in seen:
                    continue
                seen.add(utc_time.strftime("%H:%M"))
                windows.append({"weekday": None, "market": market, "utc": utc_time.strftime("%H:%M")})
            except Exception as exc:
                log(f"Error adding upload window for {market}: {exc}")
    return windows


def run_vugola_pipeline():
    try:
        topic = choose_topic()
        log(f"PIPELINE 1 (Viral finder): {topic}")
        video_url = find_viral_video(topic)
        if video_url:
            log(f"Found viral video: {video_url} — upload manually to Vugola")
    except Exception as exc:
        log(f"PIPELINE 1 ERROR: {exc}")


def run_original_pipeline():
    try:
        topic = choose_topic()
        log(f"PIPELINE 2 (Original): {topic}")
        script = generate_script(topic)
        if not script:
            log("Script generation failed")
            return

        title_a = generate_title(topic, variant="A")
        title_b = generate_title(topic, variant="B")
        if title_a == title_b:
            title_b = f"{title_a} (Alt)"

        hashtags = generate_hashtags(topic, title_a)
        description = build_description(topic, hashtags)

        audio_path = text_to_speech_dynamic(script)
        if not audio_path:
            log("Audio generation failed")
            return

        stock_videos = get_stock_videos(topic, count=4)
        if not stock_videos:
            log("No stock videos found")
            return

        video_path = create_video_local(audio_path, stock_videos, title_a, script, duration=60)
        if not video_path:
            log("Video creation failed")
            return

        thumbnail_path = generate_thumbnail(topic, title_a, hashtags)
        if not thumbnail_path:
            log("Thumbnail creation failed")

        video_a_id = upload_video_file(video_path, title_a, description, tags=hashtags, thumbnail_path=thumbnail_path)
        video_b_id = upload_video_file(video_path, title_b, description, tags=hashtags, thumbnail_path=thumbnail_path)

        if video_a_id or video_b_id:
            record_ab_test(topic, title_a, title_b, video_a_id, video_b_id, thumbnail_path)
            update_ab_test_results()
        
        # Clean up temporary files after upload
        temp_dirs = set()
        for path in [audio_path, video_path, thumbnail_path]:
            if path:
                try:
                    temp_dir = os.path.dirname(path)
                    if temp_dir and os.path.exists(temp_dir):
                        temp_dirs.add(temp_dir)
                except Exception:
                    pass
        
        for temp_dir in temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    log(f"Cleaned up temp directory: {temp_dir}")
            except Exception as exc:
                log(f"Failed to clean temp directory {temp_dir}: {exc}")
    except Exception as exc:
        log(f"PIPELINE 2 ERROR: {exc}")


def run_all():
    try:
        run_vugola_pipeline()
        run_original_pipeline()
        # Force garbage collection after pipelines to free memory
        gc.collect()
        log("Memory cleanup completed")
    except Exception as exc:
        log(f"run_all() error: {exc}")


def start():
    try:
        log("WealthShock engine started")
        upload_windows = get_best_upload_windows()
        for window in upload_windows:
            try:
                if window["weekday"] and window["weekday"] in WEEKDAY_MAP:
                    schedule_method = getattr(schedule.every(), window["weekday"])
                    schedule_method.at(window["utc"]).do(run_all)
                    log(f"Scheduled: {window['weekday'].capitalize()} {window['utc']} UTC ({window['market']})")
                else:
                    schedule.every().day.at(window["utc"]).do(run_all)
                    log(f"Scheduled: daily {window['utc']} UTC ({window['market']})")
            except Exception as exc:
                log(f"Error scheduling window {window}: {exc}")

        log("Running a test A/B pipeline now...")
        run_original_pipeline()
        gc.collect()
        log("Initial pipeline complete, memory freed")
        
        while True:
            try:
                schedule.run_pending()
                time.sleep(30)
            except Exception as exc:
                log(f"Scheduler error: {exc}")
                time.sleep(10)
    except Exception as exc:
        log(f"FATAL startup error: {exc}")
        raise


if __name__ == "__main__":
    if not os.path.exists(AB_TEST_FILE):
        save_ab_tests([])
    start()
