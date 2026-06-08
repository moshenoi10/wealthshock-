import json
import os
import random
import re
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
from PIL import Image, ImageDraw, ImageFont

load_dotenv()
FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AB_TEST_FILE = os.path.join(BASE_DIR, "ab_tests.json")
THUMBNAIL_SIZE = (1080, 1920)

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY")
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
    access_token = get_yt_access_token()
    if not access_token:
        return []
    end_date = datetime.utcnow().date() - timedelta(days=1)
    start_date = end_date - timedelta(days=27)
    url = "https://youtubeanalytics.googleapis.com/v2/reports"
    params = {
        "ids": "channel==MINE",
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "metrics": "views",
        "dimensions": "dayOfWeek",
        "sort": "-views",
        "maxResults": "7",
    }
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params, timeout=20)
    if resp.status_code != 200:
        log(f"Analytics error: {resp.status_code} {resp.text[:200]}")
        return []
    data = resp.json()
    rows = data.get("rows", [])
    day_names = []
    for row in rows:
        value = row[0] if row else None
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            day_index = int(value) - 1
            if 0 <= day_index <= 6:
                day_names.append(list(WEEKDAY_MAP.values())[day_index])
        elif isinstance(value, str):
            name = value.strip().lower()
            if name in WEEKDAY_MAP:
                day_names.append(WEEKDAY_MAP[name])
    return day_names


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
                "maxResults": count,
                "key": YOUTUBE_KEY,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            log(f"YouTube trending status {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        items = data.get("items", [])
        topics = []
        for item in items:
            title = item.get("snippet", {}).get("title")
            if title:
                topics.append(title.strip())
            if len(topics) >= count:
                break
        log(f"YouTube Trending discovered {len(topics)} topics")
        return topics
    except Exception as exc:
        log(f"Trend discovery failed: {exc}")
        return []


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


def generate_script(topic):
    if not GEMINI_KEY:
        log("Missing Gemini API key")
        return None
    prompt = f"""Create a 60-second viral YouTube Shorts script about: {topic}
- Start with an emotional hook that feels shocking or controversial
- Include 3 brief eye-opening facts or secrets with believable numbers
- Add a counterintuitive twist or belief that people disagree with
- End with a powerful CTA: follow, save, or share
Return ONLY the spoken script, with clear sentence breaks."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
    data = r.json()
    if "candidates" not in data:
        log(f"Gemini script error: {data}")
        return None
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def generate_title(topic, variant=None):
    if not GEMINI_KEY:
        fallback = f"{topic.title()} #shorts"
        if variant:
            fallback += f" ({variant})"
        return fallback
    prompt = f"Write a viral YouTube Shorts title under 60 characters about: {topic}. Use strong psychological triggers, controversy, or shocking value."
    if variant:
        prompt += f" Create an alternate title that is different from the first one. Mark it as variant {variant}."
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
    data = r.json()
    if "candidates" not in data:
        log(f"Gemini title error: {data}")
        fallback = f"{topic.title()} #shorts"
        if variant:
            fallback += f" ({variant})"
        return fallback
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


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
    if not GEMINI_KEY:
        return fallback_hashtags(topic, title)
    prompt = f"Generate 10 viral YouTube hashtags for this Shorts topic and title: {topic} / {title}. Focus on finance, money, wealth, AI, viral growth, and viewer curiosity. Return only hashtags separated by spaces."
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
    data = r.json()
    if "candidates" not in data:
        log(f"Gemini hashtags error: {data}")
        return fallback_hashtags(topic, title)
    text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    tags = [sanitize_hashtag(tag) for tag in re.split(r"\s+", text) if tag.startswith("#")]
    if not tags:
        tags = fallback_hashtags(topic, title)
    return tags[:12]


def get_stock_videos(topic, count=5):
    query = topic if topic else "finance"
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": PIXABAY_KEY, "q": query, "per_page": count * 2, "video_type": "film", "safesearch": "true"},
            timeout=20,
        )
        data = r.json()
        hits = data.get("hits", [])
        urls = []
        for h in hits:
            videos = h.get("videos", {})
            for quality in ["large", "medium", "small"]:
                url = videos.get(quality, {}).get("url")
                if url and url.startswith("https://"):
                    urls.append(url)
                    break
        if not urls:
            log("Pixabay returned no video URLs, falling back to finance search")
            return get_stock_videos("finance", count)
        log(f"Pixabay found {len(urls)} videos")
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
    path = os.path.join(tmp, "thumbnail.png")
    image_url = get_stock_image(topic)
    try:
        if image_url:
            resp = requests.get(image_url, timeout=20)
            if resp.status_code == 200:
                bg = Image.open(BytesIO(resp.content)).convert("RGB")
                bg = bg.resize(THUMBNAIL_SIZE)
            else:
                bg = make_gradient_background(THUMBNAIL_SIZE)
        else:
            bg = make_gradient_background(THUMBNAIL_SIZE)
    except Exception:
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
    return path


def call_elevenlabs_tts(text):
    if not ELEVEN_KEY:
        log("Missing ElevenLabs API key")
        return None
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
        headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        json={
            "text": text,
            "model_id": "eleven_turbo_v2_5",
            "voice_settings": {"stability": random.uniform(0.35, 0.75), "similarity_boost": random.uniform(0.4, 0.9)},
            "output_format": "mp3_44100_128",
        },
        timeout=30,
    )
    if r.status_code != 200:
        log(f"ElevenLabs TTS error {r.status_code}: {r.text[:300]}")
        return None
    return r.content


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
    audio_data = call_elevenlabs_tts(text)
    if not audio_data:
        return None
    with open(mp3_path, "wb") as f:
        f.write(audio_data)
    if not convert_audio_segment(mp3_path, wav_path, tempo=1.0, volume=1.0):
        return None
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
        audio_data = call_elevenlabs_tts(segment)
        if not audio_data:
            log("Dynamic TTS failed, falling back to full text")
            return text_to_speech_fallback(script)
        with open(mp3_path, "wb") as f:
            f.write(audio_data)

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


def create_video_local(audio_path, stock_video_urls, duration=60):
    if not os.path.exists(audio_path):
        log("Audio path missing for video creation")
        return None
    tmp = tempfile.mkdtemp()
    video_paths = []
    for i, url in enumerate(stock_video_urls[:5]):
        try:
            vpath = os.path.join(tmp, f"v{i}.mp4")
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            with open(vpath, "wb") as f:
                f.write(r.content)
            video_paths.append(vpath)
        except Exception as exc:
            log(f"Failed to download video {i}: {exc}")

    if not video_paths:
        log("No videos downloaded")
        return None

    normalized_paths = []
    for i, vpath in enumerate(video_paths):
        norm_path = os.path.join(tmp, f"norm_{i}.mp4")
        if normalize_video(vpath, norm_path):
            normalized_paths.append(norm_path)
    if not normalized_paths:
        log("No normalized videos available")
        return None

    concat_list = os.path.join(tmp, "concat.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for vp in normalized_paths:
            f.write(f"file '{vp}'\n")

    concat_path = os.path.join(tmp, "concat.mp4")
    concat_result = subprocess.run([
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
    ], capture_output=True)
    if concat_result.returncode != 0 or not os.path.exists(concat_path):
        log(f"Concat failed: {concat_result.stderr.decode()[-300:]}")
        return None

    output_path = os.path.join(tmp, "output.mp4")
    result = subprocess.run([
        FFMPEG_BIN,
        "-y",
        "-i",
        concat_path,
        "-i",
        audio_path,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "baseline",
        "-level",
        "3.1",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "44100",
        "-shortest",
        output_path,
    ], capture_output=True)
    if result.returncode != 0 or not os.path.exists(output_path):
        log(f"Final render failed: {result.stderr.decode()[-300:]}")
        return None
    log(f"Video created: {os.path.getsize(output_path) // 1024}KB")
    return output_path


def upload_thumbnail(video_id, thumbnail_path, access_token):
    if not video_id or not os.path.exists(thumbnail_path):
        return False
    with open(thumbnail_path, "rb") as f:
        data = f.read()
    resp = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
        params={"videoId": video_id},
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "image/png"},
        data=data,
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

    with open(video_path, "rb") as f:
        video_data = f.read()

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
    body = []
    body.append(f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n")
    body.append(json.dumps(meta))
    body.append(f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n")
    body.append(video_data)
    body.append(f"\r\n--{boundary}--\r\n")
    payload = b"".join(part if isinstance(part, bytes) else part.encode("utf-8") for part in body)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }
    url = "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=multipart"
    resp = requests.post(url, headers=headers, data=payload, timeout=120)
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
    best_days = get_channel_best_days()
    windows = []
    if best_days:
        seen = set()
        for weekday in best_days[:3]:
            for market, data in MARKET_PEAK_HOURS.items():
                for hour in data["hours"][:2]:
                    tz = pytz.timezone(data["tz"])
                    local_time = datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0)
                    utc_time = local_time.astimezone(pytz.utc)
                    key = (weekday, utc_time.strftime("%H:%M"))
                    if key in seen:
                        continue
                    seen.add(key)
                    windows.append({"weekday": weekday, "market": market, "utc": utc_time.strftime("%H:%M")})
                    if len(windows) >= 6:
                        break
                if len(windows) >= 6:
                    break
            if len(windows) >= 6:
                break
        if windows:
            log("Using analytics-backed upload windows")
            return windows
    log("Falling back to market peak upload schedule")
    windows = []
    seen = set()
    for market, data in MARKET_PEAK_HOURS.items():
        for hour in data["hours"][:3]:
            tz = pytz.timezone(data["tz"])
            local_time = datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0)
            utc_time = local_time.astimezone(pytz.utc)
            if utc_time.strftime("%H:%M") in seen:
                continue
            seen.add(utc_time.strftime("%H:%M"))
            windows.append({"weekday": None, "market": market, "utc": utc_time.strftime("%H:%M")})
    return windows


def run_vugola_pipeline():
    topic = choose_topic()
    log(f"PIPELINE 1 (Viral finder): {topic}")
    video_url = find_viral_video(topic)
    if video_url:
        log(f"Found viral video: {video_url} — upload manually to Vugola")


def run_original_pipeline():
    topic = choose_topic()
    log(f"PIPELINE 2 (Original): {topic}")
    script = generate_script(topic)
    if not script:
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

    stock_videos = get_stock_videos(topic)
    if not stock_videos:
        log("No stock videos found")
        return

    video_path = create_video_local(audio_path, stock_videos, duration=60)
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


def run_all():
    run_vugola_pipeline()
    run_original_pipeline()


def start():
    log("WealthShock engine started")
    upload_windows = get_best_upload_windows()
    for window in upload_windows:
        if window["weekday"] and window["weekday"] in WEEKDAY_MAP:
            schedule_method = getattr(schedule.every(), window["weekday"])
            schedule_method.at(window["utc"]).do(run_all)
            log(f"Scheduled: {window['weekday'].capitalize()} {window['utc']} UTC ({window['market']})")
        else:
            schedule.every().day.at(window["utc"]).do(run_all)
            log(f"Scheduled: daily {window['utc']} UTC ({window['market']})")

    log("Running a test A/B pipeline now...")
    run_original_pipeline()
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    if not os.path.exists(AB_TEST_FILE):
        save_ab_tests([])
    start()
