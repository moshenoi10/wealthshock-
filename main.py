import os, requests, schedule, time, random
from datetime import datetime

# --- ENV VARS ---
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
ELEVEN_KEY = os.environ.get("ELEVENLABS_API_KEY")
VUGOLA_KEY = os.environ.get("VUGOLA_API_KEY")
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

UPLOAD_TIMES = ["13:00", "16:00", "19:00", "21:00"]

# ─── HELPERS ───────────────────────────────────────────

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

def get_yt_access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": YT_CLIENT_ID,
        "client_secret": YT_CLIENT_SECRET,
        "refresh_token": YT_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    })
    return r.json().get("access_token")

# ─── PIPELINE 1: VUGOLA (viral clipping) ───────────────

def find_viral_video(topic):
    """Find a viral YouTube video on the topic using YouTube Data API"""
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

def clip_with_vugola(video_url, topic):
    """Send video to Vugola API for AI clipping"""
    r = requests.post(
        "https://api.vugolaai.com/v1/clip",
        headers={"Authorization": f"Bearer {VUGOLA_KEY}", "Content-Type": "application/json"},
        json={
            "video_url": video_url,
            "platforms": ["youtube"],
            "aspect_ratio": "9:16",
            "min_duration": 30,
            "max_duration": 60,
            "captions": True,
            "num_clips": 5
        }
    )
    data = r.json()
    log(f"Vugola job started: {data.get('job_id')}")
    return data.get("job_id")

def wait_for_vugola(job_id, timeout=300):
    """Poll Vugola until clips are ready"""
    for _ in range(timeout // 10):
        r = requests.get(
            f"https://api.vugolaai.com/v1/jobs/{job_id}",
            headers={"Authorization": f"Bearer {VUGOLA_KEY}"}
        )
        data = r.json()
        if data.get("status") == "completed":
            return data.get("clips", [])
        if data.get("status") == "failed":
            log("Vugola job failed")
            return []
        time.sleep(10)
    return []

def schedule_vugola_to_youtube(clips):
    """Schedule Vugola clips directly to YouTube"""
    for clip in clips[:3]:
        r = requests.post(
            "https://api.vugolaai.com/v1/schedule",
            headers={"Authorization": f"Bearer {VUGOLA_KEY}", "Content-Type": "application/json"},
            json={
                "clip_id": clip["id"],
                "platform": "youtube",
                "title": clip.get("title", "Viral Finance Short #shorts"),
                "description": "#shorts #finance #money #viral #wealth",
                "publish_at": "now"
            }
        )
        log(f"Scheduled clip to YouTube: {r.json().get('status')}")

def run_vugola_pipeline():
    topic = random.choice(TOPICS)
    log(f"PIPELINE 1 (Vugola): {topic}")
    video_url = find_viral_video(topic)
    if not video_url:
        log("No video found, skipping")
        return
    log(f"Found video: {video_url}")
    job_id = clip_with_vugola(video_url, topic)
    if not job_id:
        return
    clips = wait_for_vugola(job_id)
    if clips:
        schedule_vugola_to_youtube(clips)
        log(f"Done — {len(clips)} clips uploaded")

# ─── PIPELINE 2: ORIGINAL CONTENT ──────────────────────

def generate_script(topic):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
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
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    prompt = f"Write a viral YouTube Shorts title (max 60 chars) for a video about: {topic}. Make it shocking and clickbait. Return ONLY the title."
    r = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]})
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()

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
    clips = []
    seg = duration / max(len(stock_videos), 1)
    for i, url in enumerate(stock_videos):
        clips.append({
            "asset": {"type": "video", "src": url},
            "start": i * seg,
            "length": seg,
            "fit": "cover"
        })

    r = requests.post(
        "https://api.shotstack.io/stage/render",
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
    return r.json().get("response", {}).get("id")

def wait_for_shotstack(render_id, timeout=300):
    for _ in range(timeout // 10):
        r = requests.get(
            f"https://api.shotstack.io/stage/render/{render_id}",
            headers={"x-api-key": SHOTSTACK_KEY}
        )
        data = r.json().get("response", {})
        if data.get("status") == "done":
            return data.get("url")
        if data.get("status") == "failed":
            return None
        time.sleep(10)
    return None

def upload_to_youtube(video_url, title, description):
    access_token = get_yt_access_token()
    video_data = requests.get(video_url).content

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    meta = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": ["shorts", "finance", "money", "AI", "viral", "wealth", "investing"],
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
        log("Failed to get YouTube upload URL")
        return False

    res = requests.put(upload_url,
        headers={"Content-Type": "video/mp4", "Content-Length": str(len(video_data))},
        data=video_data)
    return res.status_code in [200, 201]

def run_original_pipeline():
    import base64
    topic = random.choice(TOPICS)
    log(f"PIPELINE 2 (Original): {topic}")

    script = generate_script(topic)
    if not script:
        log("Script failed — check GEMINI_API_KEY in Render")
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

# ─── SCHEDULER ─────────────────────────────────────────

def run_all():
    run_vugola_pipeline()
    run_original_pipeline()

def start():
    log("WealthShock engine started")
    for t in UPLOAD_TIMES:
        schedule.every().day.at(t).do(run_all)
    log(f"Scheduled at: {', '.join(UPLOAD_TIMES)} EST")
    run_all()
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    start()
