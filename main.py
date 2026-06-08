import os, requests, schedule, time, random, pytz, base64, tempfile, subprocess
from datetime import datetime

GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY")
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY")
YT_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")

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
            best_times.append({"market": market, "local": f"{hour:02d}:00 {data['tz']}",
                                "utc": utc_time.strftime("%H:%M"), "utc_hour": utc_time.hour})
    best_times.sort(key=lambda x: x["utc_hour"])
    log("=== OPTIMAL UPLOAD SCHEDULE ===")
    seen, unique = set(), []
    for t in best_times:
        if t["utc_hour"] not in seen:
            seen.add(t["utc_hour"])
            unique.append(t)
            log(f"  {t['market']} | {t['local']} → {t['utc']} UTC")
    log("================================")
    return unique

def get_yt_access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": YT_CLIENT_ID, "client_secret": YT_CLIENT_SECRET,
        "refresh_token": YT_REFRESH_TOKEN, "grant_type": "refresh_token"
    })
    return r.json().get("access_token")

def find_viral_video(topic):
    r = requests.get("https://www.googleapis.com/youtube/v3/search", params={
        "part": "snippet", "q": topic, "type": "video",
        "videoDuration": "medium", "order": "viewCount", "maxResults": 5, "key": YOUTUBE_KEY
    })
    items = r.json().get("items", [])
    if not items:
        return None
    return f"https://www.youtube.com/watch?v={items[0]['id']['videoId']}"

def generate_script(topic):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    prompt = f"""Write a 60-second viral YouTube Shorts script about: {topic}
- Hook (5 sec): one shocking sentence
- 3 shocking facts with real numbers  
- CTA: Follow WealthShock for more
Return ONLY the spoken script."""
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    data = r.json()
    if "candidates" not in data:
        log(f"Gemini error: {data}")
        return None
    return data["candidates"][0]["content"]["parts"][0]["text"]

def generate_title(topic):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    prompt = f"Viral YouTube Shorts title (max 60 chars) about: {topic}. Shocking clickbait. Return ONLY the title."
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    data = r.json()
    if "candidates" not in data:
        return None
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()

def text_to_speech(text):
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/21m00Tcm4TlvDq8ikWAM",
        headers={"xi-api-key": ELEVEN_KEY, "Content-Type": "application/json"},
        json={"text": text, "model_id": "eleven_monolingual_v1",
              "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}
    )
    return r.content

def get_stock_videos(topic, count=5):
    r = requests.get("https://pixabay.com/api/videos/", params={
        "key": PIXABAY_KEY, "q": topic, "per_page": count, "video_type": "film"
    })
    hits = r.json().get("hits", [])
    urls = []
    for h in hits:
        videos = h.get("videos", {})
        for quality in ["medium", "small", "large"]:
            url = videos.get(quality, {}).get("url", "")
            if url.startswith("https://"):
                urls.append(url)
                break
    log(f"Pixabay found {len(urls)} videos")
    return urls[:count]

def create_video_local(audio_content, stock_video_urls, duration=60):
    tmp = tempfile.mkdtemp()
    audio_path = os.path.join(tmp, "audio.mp3")
    with open(audio_path, "wb") as f:
        f.write(audio_content)

    video_paths = []
    for i, url in enumerate(stock_video_urls[:5]):
        try:
            vpath = os.path.join(tmp, f"v{i}.mp4")
            r = requests.get(url, timeout=30)
            with open(vpath, "wb") as f:
                f.write(r.content)
            video_paths.append(vpath)
        except Exception as e:
            log(f"Failed to download video {i}: {e}")

    if not video_paths:
        log("No videos downloaded")
        return None

    concat_list = os.path.join(tmp, "concat.txt")
    with open(concat_list, "w") as f:
        for vp in video_paths:
            f.write(f"file '{vp}'\n")

    concat_path = os.path.join(tmp, "concat.mp4")
    subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0",
                    "-i", concat_list, "-c", "copy", concat_path, "-y"],
                   capture_output=True)

    output_path = os.path.join(tmp, "output.mp4")
    result = subprocess.run([
        "ffmpeg", "-i", concat_path, "-i", audio_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-t", str(duration), "-c:v", "libx264", "-c:a", "aac", "-shortest", "-y", output_path
    ], capture_output=True)

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        log(f"Video created: {os.path.getsize(output_path) // 1024}KB")
        return output_path
    log(f"ffmpeg error: {result.stderr.decode()[-200:]}")
    return None

def upload_to_youtube(video_path, title, description):
    access_token = get_yt_access_token()
    if not access_token:
        log("No YouTube access token")
        return False
    with open(video_path, "rb") as f:
        video_data = f.read()
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    meta = {
        "snippet": {"title": title[:100], "description": description,
                    "tags": ["shorts", "finance", "money", "AI", "viral", "wealth"], "categoryId": "28"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
    }
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers=headers, json=meta)
    upload_url = init.headers.get("Location")
    if not upload_url:
        log(f"No upload URL: {init.text[:200]}")
        return False
    res = requests.put(upload_url,
        headers={"Content-Type": "video/mp4", "Content-Length": str(len(video_data))},
        data=video_data)
    success = res.status_code in [200, 201]
    log(f"YouTube upload: {res.status_code}")
    return success

def run_vugola_pipeline():
    topic = random.choice(TOPICS)
    log(f"PIPELINE 1 (Viral finder): {topic}")
    video_url = find_viral_video(topic)
    if video_url:
        log(f"Found viral video: {video_url} — upload manually to Vugola")

def run_original_pipeline():
    topic = random.choice(TOPICS)
    log(f"PIPELINE 2 (Original): {topic}")
    script = generate_script(topic)
    if not script:
        return
    title = generate_title(topic) or f"{topic.title()} #shorts"
    log(f"Title: {title}")
    audio = text_to_speech(script)
    stock_videos = get_stock_videos(topic)
    if not stock_videos:
        log("No stock videos found")
        return
    video_path = create_video_local(audio, stock_videos)
    if not video_path:
        log("Video creation failed")
        return
    description = f"WealthShock | {topic}\n\n#shorts #finance #AI #viral #money #wealth"
    upload_to_youtube(video_path, title, description)

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
