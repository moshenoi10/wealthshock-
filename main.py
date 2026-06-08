import os, requests, schedule, time, random, pytz, base64
from datetime import datetime

# --- ENV VARS ---
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY")
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY")
YT_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
SHOTSTACK_KEY = os.environ.get("SHOTSTACK_API_KEY")
PEXELS_KEY = os.environ.get("PEXELS_API_KEY")

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
    "US": {"tz": "America/New_York",  "hours": [8, 12, 17, 20, 22]},
    "IL": {"tz": "Asia/Jerusalem",    "hours": [7, 12, 16, 20, 21]},
    "JP": {"tz": "Asia/Tokyo",        "hours": [7, 12, 19, 21, 23]},
    "CN": {"tz": "Asia/Shanghai",     "hours": [7, 12, 18, 20, 22]},
}

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_best_upload_times():
    best_times = []
    for market, data in MARKET_PEAK_HOURS.items():
        tz = pytz.timezone(data["tz"])
        for hour in data["hours"]:
            local_time = datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0)
            utc_time = local_time.astimezone(pytz.utc)
            best_times.append({
                "market": market,
                "local": f"{hour:02d}:00 {data['tz']}",
                "utc": utc_time.strftime("%H:%M"),
                "utc_hour": utc_time.hour
            })
    best_times.sort(key=lambda x: x["utc_hour"])
    log("=== OPTIMAL UPLOAD SCHEDULE ===")
    seen = set()
    unique = []
    for t in best_times:
        if t["utc_hour"] not in seen:
            seen.add(t["utc_hour"])
            unique.append(t)
            log(f"  {t['market']} | {t['local']} → {t['utc']} UTC")
    log("================================")
    return unique

def get_yt_access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": YT_CLIENT_ID,
        "client_secret": YT_CLIENT_SECRET,
        "refresh_token": YT_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    })
    return r.json().get("access_token")

# ─── PIPELINE 1: FIND VIRAL VIDEO ──────────────────────

def find_viral_video(topic):
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": topic,
        "type": "video",
        "videoDuration": "medium",
        "order": "viewCount",
        "maxResults": 5,
        "key": YOUTUBE_KEY
    }
    r = requests.get(url, params=params)
    items = r.json().get("items", [])
    if not items:
        return None
    video_id = items[0]["id"]["videoId"]
    return f"https://www.youtube.com/watch?v={video_id}"

def run_vugola_pipeline():
    topic = random.choice(TOPICS)
    log(f"PIPELINE 1 (Viral finder): {topic}")
    video_url = find_viral_video(topic)
    if not video_url:
        log("No video found")
        return
    log(f"Found viral video: {video_url}")
    log("Upload this video manually to Vugola for auto-clipping")

# ─── PIPELINE 2: ORIGINAL CONTENT ──────────────────────

def generate_script(topic):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    prompt = f"""Write a 60-second viral YouTube Shorts script about: {topic}

Structure:
- Hook (5 sec): one shocking sentence that stops scrolling
- 3 shocking facts with real numbers
- CTA: "Follow WealthShock for more"

Return ONLY the spoken script. No labels, no brackets."""
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    data = r.json()
    if "candidates" not in data:
        log(f"Gemini error: {data}")
        return None
    return data["candidates"][0]["content"]["parts"][0]["text"]

def generate_title(topic):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    prompt = f"Write a viral YouTube Shorts title (max 60 chars) about: {topic}. Make it shocking and clickbait. Return ONLY the title."
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    data = r.json()
    if "candidates" not in data:
        return None
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

def text_to_speech(text):
    voice_id = "21m00Tcm4TlvDq8ikWAM"
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_monolingual_v1",
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    )
    return r.content

def get_stock_videos(topic, count=5):
    r = requests.get(
        "https://api.pexels.com/videos/search",
        headers={"Authorization": PEXELS_KEY},
        params={"query": topic, "per_page": count, "orientation": "portrait"}
    )
    videos = r.json().get("videos", [])
    return [v["video_files"][0]["link"] for v in videos]

def create_video_shotstack(audio_b64, stock_videos, duration=60):
    seg = duration / max(len(stock_videos), 1)
    clips = [{"asset": {"type": "video", "src": url}, "start": i * seg, "length": seg, "fit": "cover"}
             for i, url in enumerate(stock_videos)]
    r = requests.post(
        "https://api.shotstack.io/edit/stage/render",
        headers={"x-api-key": SHOTSTACK_KEY, "Content-Type": "application/json"},
        json={
            "timeline": {
                "tracks": [
                    {"clips": clips},
                    {"clips": [{"asset": {"type": "audio", "src": f"data:audio/mp3;base64,{audio_b64}"},
                                "start": 0, "length": duration}]}
                ]
            },
            "output": {"format": "mp4", "resolution": "sd", "aspectRatio": "9:16"}
        }
    )
    data = r.json()
    log(f"Shotstack response: {data}")
    return data.get("response", {}).get("id")

def wait_for_shotstack(render_id, timeout=300):
    for _ in range(timeout // 10):
        r = requests.get(
            f"https://api.shotstack.io/edit/stage/render/{render_id}",
            headers={"x-api-key": SHOTSTACK_KEY}
        )
        data = r.json().get("response", {})
        if data.get("status") == "done":
            return data.get("url")
        if data.get("status") == "failed":
            log(f"Shotstack failed: {data}")
            return None
        log(f"Shotstack status: {data.get('status')}")
        time.sleep(10)
    return None

def upload_to_youtube(video_url, title, description):
    access_token = get_yt_access_token()
    if not access_token:
        log("Failed to get YouTube access token")
        return False
    video_data = requests.get(video_url).content
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    meta = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["shorts", "finance", "money", "AI", "viral", "wealth"],
            "categoryId": "28"
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    }
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers=headers, json=meta
    )
    upload_url = init.headers.get("Location")
    if not upload_url:
        log(f"No upload URL: {init.text}")
        return False
    res = requests.put(upload_url,
        headers={"Content-Type": "video/mp4", "Content-Length": str(len(video_data))},
        data=video_data)
    return res.status_code in [200, 201]

def run_original_pipeline():
    topic = random.choice(TOPICS)
    log(f"PIPELINE 2 (Original): {topic}")
    script = generate_script(topic)
    if not script:
        log("Script failed — check GEMINI_API_KEY")
        return
    title = generate_title(topic) or f"{topic.title()} #shorts"
    log(f"Title: {title}")
    audio = text_to_speech(script)
    audio_b64 = base64.b64encode(audio).decode()
    stock_videos = get_stock_videos(topic)
    if not stock_videos:
        log("No stock videos found")
        return
    render_id = create_video_shotstack(audio_b64, stock_videos)
    if not render_id:
        log("Shotstack render failed")
        return
    video_url = wait_for_shotstack(render_id)
    if not video_url:
        log("Shotstack timed out")
        return
    description = f"WealthShock | {topic}\n\n#shorts #finance #AI #viral #money #wealth"
    success = upload_to_youtube(video_url, title, description)
    log(f"Upload {'successful' if success else 'failed'}: {title}")

# ─── MAIN ──────────────────────────────────────────────

def run_all():
    run_vugola_pipeline()
    run_original_pipeline()

def start():
    log("WealthShock engine started")
    best_times = get_best_upload_times()
    for t in best_times:
        schedule.every().day.at(t["utc"]).do(run_all)
        log(f"Scheduled: {t['utc']} UTC ({t['market']})")
    log("Running 2 test videos now...")
    run_original_pipeline()
    run_original_pipeline()
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    start()
