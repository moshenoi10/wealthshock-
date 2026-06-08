"""
WealthShock - Render worker (lightweight, no ffmpeg)
Responsibilities:
  - Script/title/hashtag generation (Gemini → local fallback)
  - gTTS voiceover (MP3, no ffmpeg needed)
  - Upload audio to free temp host (0x0.st / file.io / tmpfiles.org)
  - Trigger GitHub Actions video processing workflow
  - Poll for completion and download finished MP4
  - Upload to YouTube with A/B title testing
  - Run daily scheduler
"""

import gc
import json
import os
import random
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone

import pytz
import requests
import schedule
from dotenv import load_dotenv
from gtts import gTTS

import analytics as analytics_module

load_dotenv()

# ─── Environment variables ───────────────────────────────────────────────────
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
YOUTUBE_KEY = os.environ.get("YOUTUBE_API_KEY")
YT_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")
GH_PAT = os.environ.get("GH_PAT")
GH_REPO = os.environ.get("GH_REPO")  # "owner/repo"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AB_TEST_FILE = os.path.join(BASE_DIR, "ab_tests.json")
ANALYTICS_DB_FILE = os.path.join(BASE_DIR, "performance_db.json")

GEMINI_MODEL = "gemini-2.5-flash-lite"

_analytics_db = None


def get_db() -> analytics_module.PerformanceDB:
    global _analytics_db
    if _analytics_db is None:
        _analytics_db = analytics_module.PerformanceDB(
            ANALYTICS_DB_FILE,
            gh_pat=os.environ.get("GH_PAT"),
            gh_repo=os.environ.get("GH_REPO"),
        )
    return _analytics_db

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


# ─── Utilities ────────────────────────────────────────────────────────────────

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


def request_with_retries(method, url, max_retries=3, delay=5, **kwargs):
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code == 503:
                log(f"503 from {url}, retry {attempt + 1}/{max_retries}")
                time.sleep(delay)
                continue
            return resp
        except requests.RequestException as exc:
            log(f"Request error to {url}, retry {attempt + 1}/{max_retries}: {exc}")
            time.sleep(delay)
    return None


def stream_download(url, dest_path, chunk_size=8192):
    for attempt in range(3):
        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if chunk:
                            f.write(chunk)
            if os.path.getsize(dest_path) > 0:
                return True
        except Exception as exc:
            log(f"Download attempt {attempt + 1} failed for {url}: {exc}")
            time.sleep(3)
    return False


# ─── YouTube ─────────────────────────────────────────────────────────────────

def get_yt_access_token():
    if not all([YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN]):
        log("Missing YouTube OAuth env vars")
        return None
    for attempt in range(3):
        try:
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
            if resp.status_code == 200 and "access_token" in data:
                return data["access_token"]
            log(f"YouTube auth attempt {attempt + 1} failed: {data}")
        except Exception as exc:
            log(f"YouTube auth error (attempt {attempt + 1}): {exc}")
        time.sleep(5)
    return None


def upload_video_file(video_path, title, description, tags=None):
    access_token = get_yt_access_token()
    if not access_token:
        log("Cannot upload: no YouTube access token")
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
        yield f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode()
        yield json.dumps(meta).encode()
        yield f"\r\n--{boundary}\r\nContent-Type: video/mp4\r\n\r\n".encode()
        with open(video_path, "rb") as vf:
            while True:
                chunk = vf.read(8192)
                if not chunk:
                    break
                yield chunk
        yield f"\r\n--{boundary}--\r\n".encode()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }
    url = "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=multipart"

    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, data=multipart_stream(), timeout=300)
            if resp.status_code in [200, 201]:
                vid = resp.json().get("id")
                log(f"Uploaded video: {vid}")
                return vid
            log(f"Upload attempt {attempt + 1} failed: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            log(f"Upload attempt {attempt + 1} error: {exc}")
        time.sleep(10)
    return None


# ─── AI: Gemini → local fallback ─────────────────────────────────────────────

def _gemini_generate(prompt):
    if not GEMINI_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    resp = request_with_retries(
        "POST", url,
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=30,
    )
    if resp is None:
        return None
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        log(f"Gemini response error: {data}")
        return None


def generate_script(topic, target_duration=60):
    prompt = (
        f"Create a {target_duration}-second viral YouTube Shorts script about: {topic}\n"
        "- Start with an emotional hook that feels shocking or controversial\n"
        "- Include 3 brief eye-opening facts or secrets with believable numbers\n"
        "- Add a counterintuitive twist that most people disagree with\n"
        "- End with a powerful CTA: follow, save, or share\n"
        f"- Aim for exactly {target_duration} seconds of spoken content at natural pace\n"
        "Return ONLY the spoken script, no labels, with clear sentence breaks."
    )
    result = _gemini_generate(prompt)
    if result:
        return result

    hook = f"You won't believe this about {topic.split()[0]}!"
    facts = [f"Fact {i + 1}: {random.randint(2, 95)}% of people don't know this about {topic}." for i in range(3)]
    twist = f"But here's the twist — most advice about {topic} is completely backwards."
    return " ".join([hook] + facts + [twist, "Follow and save this video right now."])


def generate_title(topic, variant=None, style_hint=None):
    style_instructions = {
        "question": "Phrase it as a question that triggers curiosity.",
        "number_list": "Start with a number (e.g. '5 things', '3 secrets').",
        "secret_reveal": "Frame it as a secret or hidden truth being exposed.",
        "shocking_statement": "Make it a shocking, unbelievable statement.",
        "statement": "Use a bold, direct statement.",
    }
    style_guide = style_instructions.get(style_hint, "") if style_hint else ""
    prompt = (
        f"Write a viral YouTube Shorts title under 60 characters about: {topic}. "
        "Use strong psychological triggers, controversy, or shocking value."
    )
    if style_guide:
        prompt += f" {style_guide}"
    if variant:
        prompt += f" Make it variant {variant} with a different angle or wording."
    result = _gemini_generate(prompt)
    if result:
        return result[:100]
    base = topic.title()
    return f"{base} ({variant})" if variant else f"{base} #shorts"


def sanitize_hashtag(text):
    cleaned = re.sub(r"[^A-Za-z0-9]", "", text)
    return f"#{cleaned.lower()}" if cleaned else None


def generate_hashtags(topic, title):
    prompt = (
        f"Generate 10 viral YouTube hashtags for: {topic} / {title}. "
        "Focus on finance, money, wealth, AI. Return only hashtags separated by spaces."
    )
    result = _gemini_generate(prompt)
    if result:
        tags = [sanitize_hashtag(t) for t in re.split(r"\s+", result) if t.startswith("#")]
        tags = [t for t in tags if t]
        if tags:
            return tags[:12]
    words = re.findall(r"[A-Za-z0-9]+", f"{topic} {title}")
    extras = {sanitize_hashtag(w) for w in words if len(w) > 2}
    core = ["#shorts", "#finance", "#money", "#wealth", "#viral"]
    return core + [t for t in extras if t][:8]


def build_description(topic, hashtags):
    tags = " ".join(hashtags[:12])
    return (
        f"WealthShock | {topic}\n\n"
        "Warning: the hidden rules of money, power, and AI are not what your teacher told you.\n\n"
        f"{tags}\n\n"
        "Follow for the fastest finance hacks, shocking market secrets, and money mindset truth. #shorts"
    )


# ─── Topic selection ──────────────────────────────────────────────────────────

def trending_topics(region="US", count=8):
    if not YOUTUBE_KEY:
        return []
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet", "chart": "mostPopular", "regionCode": region,
                    "maxResults": count * 3, "key": YOUTUBE_KEY},
            timeout=20,
        )
        if resp.status_code != 200:
            return []
        FINANCE_KEYWORDS = {"money", "finance", "invest", "trading", "stock", "crypto",
                            "bitcoin", "wealth", "rich", "ai", "business", "income", "market"}
        topics = []
        for item in resp.json().get("items", []):
            title = item.get("snippet", {}).get("title", "")
            if any(kw in title.lower() for kw in FINANCE_KEYWORDS):
                topics.append(title.strip())
            if len(topics) >= count:
                break
        return topics
    except Exception as exc:
        log(f"Trending topics failed: {exc}")
        return []


def choose_topic(force_category=None):
    trending = trending_topics()
    pool = (trending + TOPICS) if trending else TOPICS

    if force_category:
        filtered = [t for t in pool if analytics_module.categorize_topic(t) == force_category]
        if filtered:
            topic = random.choice(filtered)
            log(f"Topic (forced {force_category}): {topic}")
            return topic

    # Use analytics weights when enough data exists
    weights = get_db().get_topic_weights()
    if weights:
        cats = list(weights.keys())
        wts = [weights[c] for c in cats]
        chosen_cat = random.choices(cats, weights=wts, k=1)[0]
        filtered = [t for t in pool if analytics_module.categorize_topic(t) == chosen_cat]
        if filtered:
            topic = random.choice(filtered)
            log(f"Topic (analytics-weighted, cat={chosen_cat}): {topic}")
            return topic

    topic = random.choice(pool)
    log(f"Topic: {topic}")
    return topic


def find_viral_video(topic):
    if not YOUTUBE_KEY:
        return None
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"part": "snippet", "q": topic, "type": "video",
                    "order": "viewCount", "maxResults": 3, "key": YOUTUBE_KEY},
            timeout=20,
        )
        for item in resp.json().get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
    except Exception as exc:
        log(f"Viral video search failed: {exc}")
    return None


# ─── Pixabay stock videos (multi-keyword, context-matched) ───────────────────

# Base finance keyword sets — always queried for visual variety
_BASE_CLIP_QUERIES = [
    "money cash dollar bills finance",
    "stock market trading charts graphs",
    "bitcoin cryptocurrency blockchain technology",
    "luxury wealth success millionaire",
    "business office corporate finance",
    "investment savings bank gold",
]

# Script-pattern → Pixabay query mapping for semantic matching
_SCRIPT_CLIP_MAP = {
    ("stock", "market", "trading", "invest", "nasdaq", "sp500"): "stock market trading charts",
    ("bitcoin", "crypto", "blockchain", "coin", "defi"):          "bitcoin cryptocurrency digital",
    ("rich", "wealth", "luxury", "millionaire", "billionaire"):   "luxury wealth success lifestyle",
    ("bank", "loan", "debt", "credit", "interest"):               "bank finance money institution",
    ("business", "entrepreneur", "startup", "company"):           "business corporate office success",
    ("income", "salary", "earn", "passive", "hustle"):            "income money earning finance",
    ("save", "saving", "budget", "spend", "frugal"):              "saving money piggy bank",
    ("ai", "artificial", "technology", "future"):                 "technology artificial intelligence",
}


def get_varied_stock_clips(script, count=15):
    """
    Query Pixabay with multiple finance keyword sets for visual variety.
    Script is scanned for topic keywords to prioritise relevant visuals.
    Returns up to `count` unique clip URLs.
    """
    if not PIXABAY_KEY:
        log("Missing Pixabay API key")
        return []

    script_lower = script.lower()

    # Build priority queries from script content
    priority = []
    for patterns, query in _SCRIPT_CLIP_MAP.items():
        if any(kw in script_lower for kw in patterns):
            priority.append(query)

    # Combine: priority first, then base queries (deduplicated)
    queries = priority + [q for q in _BASE_CLIP_QUERIES if q not in priority]
    queries = queries[:6]  # cap at 6 API calls

    per_query = max(3, count // len(queries) + 2)
    seen, urls = set(), []

    for query in queries:
        if len(urls) >= count:
            break
        for attempt in range(2):
            try:
                resp = requests.get(
                    "https://pixabay.com/api/videos/",
                    params={
                        "key": PIXABAY_KEY,
                        "q": query,
                        "per_page": per_query * 2,
                        "video_type": "film",
                        "safesearch": "true",
                        "order": "popular",
                    },
                    timeout=20,
                )
                for hit in resp.json().get("hits", []):
                    for quality in ["small", "medium"]:
                        url = hit.get("videos", {}).get(quality, {}).get("url")
                        if url and url.startswith("https://") and url not in seen:
                            seen.add(url)
                            urls.append(url)
                            break
                break
            except Exception as exc:
                log(f"Pixabay '{query[:30]}' attempt {attempt+1}: {exc}")
                time.sleep(2)

    random.shuffle(urls)
    result = urls[:count]
    log(f"Pixabay: {len(result)} varied clips from {len(queries)} keyword queries")
    return result


# ─── TTS (MP3 only, no ffmpeg on Render) ─────────────────────────────────────

def generate_voiceover(script):
    tmp = tempfile.mkdtemp()
    mp3_path = os.path.join(tmp, "voice.mp3")
    for attempt in range(3):
        try:
            tts = gTTS(text=script, lang="en", slow=False)
            tts.save(mp3_path)
            if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
                log(f"gTTS voiceover: {os.path.getsize(mp3_path) // 1024}KB")
                return mp3_path
        except Exception as exc:
            log(f"gTTS attempt {attempt + 1} failed: {exc}")
            time.sleep(5)
    shutil.rmtree(tmp, ignore_errors=True)
    return None


# ─── Free temp file hosting ───────────────────────────────────────────────────

def upload_to_temp_host(file_path):
    # 0x0.st — files persist 24h+ (size-dependent), no account needed
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post("https://0x0.st", files={"file": f}, timeout=120)
            if resp.status_code == 200:
                url = resp.text.strip()
                if url.startswith("http"):
                    log(f"Audio at 0x0.st: {url}")
                    return url
        except Exception as exc:
            log(f"0x0.st attempt {attempt + 1}: {exc}")
            time.sleep(3)

    # file.io — single-use download link
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post("https://file.io", files={"file": f}, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("link"):
                    log(f"Audio at file.io: {data['link']}")
                    return data["link"]
        except Exception as exc:
            log(f"file.io attempt {attempt + 1}: {exc}")
            time.sleep(3)

    # tmpfiles.org — 60 min expiry (enough for GHA to finish)
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post("https://tmpfiles.org/api/v1/upload", files={"file": f}, timeout=120)
            if resp.status_code == 200:
                url = resp.json().get("data", {}).get("url", "")
                if url:
                    # tmpfiles.org returns /xxxxxx/file.ext — prefix dl. for direct download
                    dl_url = url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                    log(f"Audio at tmpfiles.org: {dl_url}")
                    return dl_url
        except Exception as exc:
            log(f"tmpfiles.org attempt {attempt + 1}: {exc}")
            time.sleep(3)

    log("All temp file hosts failed")
    return None


# ─── GitHub Actions integration ───────────────────────────────────────────────

def _gh_headers():
    return {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def trigger_and_get_run_id(inputs):
    if not GH_PAT or not GH_REPO:
        log("Missing GH_PAT or GH_REPO — cannot trigger GitHub Actions")
        return None

    dispatch_url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process_video.yml/dispatches"
    before = datetime.now(timezone.utc)

    for attempt in range(3):
        try:
            resp = requests.post(
                dispatch_url,
                json={"ref": "main", "inputs": inputs},
                headers=_gh_headers(),
                timeout=30,
            )
            if resp.status_code == 204:
                log("GitHub Actions workflow dispatched")
                break
            log(f"Dispatch attempt {attempt + 1} failed: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            log(f"Dispatch attempt {attempt + 1} error: {exc}")
        time.sleep(5)
    else:
        return None

    # Poll for the new run created after dispatch
    runs_url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process_video.yml/runs"
    for _ in range(24):  # up to 2 minutes
        time.sleep(5)
        try:
            resp = requests.get(runs_url, headers=_gh_headers(), params={"per_page": 10}, timeout=20)
            if resp.status_code != 200:
                continue
            for run in resp.json().get("workflow_runs", []):
                created_str = run.get("created_at", "")
                try:
                    run_time = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                    if run_time >= before:
                        log(f"GHA run ID: {run['id']}")
                        return run["id"]
                except Exception:
                    pass
        except Exception as exc:
            log(f"Run polling error: {exc}")

    log("Timed out finding GHA run ID")
    return None


def wait_for_github_run(run_id, timeout_seconds=420):
    if not run_id:
        return False
    url = f"https://api.github.com/repos/{GH_REPO}/actions/runs/{run_id}"
    start = time.time()
    while time.time() - start < timeout_seconds:
        try:
            resp = requests.get(url, headers=_gh_headers(), timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                status = data.get("status")
                conclusion = data.get("conclusion")
                log(f"GHA run {run_id}: {status}/{conclusion}")
                if status == "completed":
                    return conclusion == "success"
        except Exception as exc:
            log(f"Run status check error: {exc}")
        time.sleep(30)
    log(f"GHA run {run_id} timed out after {timeout_seconds}s")
    return False


def wait_for_two_runs(run_id_a, run_id_b, timeout_seconds=660):
    """Poll two GHA runs simultaneously. Returns (success_a, success_b)."""
    start = time.time()
    runs = {
        "A": {"id": run_id_a, "done": run_id_a is None, "success": False},
        "B": {"id": run_id_b, "done": run_id_b is None, "success": False},
    }

    while time.time() - start < timeout_seconds:
        if all(r["done"] for r in runs.values()):
            break
        for label, r in runs.items():
            if r["done"] or not r["id"]:
                continue
            try:
                resp = requests.get(
                    f"https://api.github.com/repos/{GH_REPO}/actions/runs/{r['id']}",
                    headers=_gh_headers(), timeout=20,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    status = data.get("status")
                    conclusion = data.get("conclusion")
                    log(f"GHA run {label} ({r['id']}): {status}/{conclusion}")
                    if status == "completed":
                        r["done"] = True
                        r["success"] = conclusion == "success"
            except Exception as exc:
                log(f"Run {label} status error: {exc}")
        if not all(r["done"] for r in runs.values()):
            time.sleep(30)

    for label, r in runs.items():
        if not r["done"]:
            log(f"GHA run {label} ({r['id']}) timed out")
    return runs["A"]["success"], runs["B"]["success"]


def download_video_artifact(run_id):
    if not run_id:
        return None
    artifacts_url = f"https://api.github.com/repos/{GH_REPO}/actions/runs/{run_id}/artifacts"
    tmp = None
    try:
        for attempt in range(3):
            try:
                resp = requests.get(artifacts_url, headers=_gh_headers(), timeout=20)
                if resp.status_code == 200:
                    break
                time.sleep(5)
            except Exception as exc:
                log(f"Artifact list attempt {attempt + 1}: {exc}")
        else:
            log("Failed to list artifacts")
            return None

        artifacts = resp.json().get("artifacts", [])
        if not artifacts:
            log("No artifacts found for run")
            return None

        artifact_id = artifacts[0]["id"]
        dl_url = f"https://api.github.com/repos/{GH_REPO}/actions/artifacts/{artifact_id}/zip"

        tmp = tempfile.mkdtemp()
        zip_path = os.path.join(tmp, "video.zip")

        resp = requests.get(
            dl_url, headers=_gh_headers(),
            stream=True, timeout=300, allow_redirects=True,
        )
        if resp.status_code != 200:
            log(f"Artifact download failed: {resp.status_code}")
            return None

        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmp)
        os.remove(zip_path)

        for name in os.listdir(tmp):
            if name.endswith(".mp4"):
                mp4 = os.path.join(tmp, name)
                log(f"Artifact: {mp4} ({os.path.getsize(mp4) // 1024}KB)")
                return mp4

        log("No MP4 found in artifact ZIP")
        return None
    except Exception as exc:
        log(f"Artifact download error: {exc}")
        if tmp and os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        return None


# ─── A/B Tests ────────────────────────────────────────────────────────────────

def load_ab_tests():
    tests = load_json_file(AB_TEST_FILE, [])
    return tests if isinstance(tests, list) else []


def save_ab_tests(tests):
    save_json_file(AB_TEST_FILE, tests)


def record_ab_test(topic, title_a, title_b, video_a_id, video_b_id):
    tests = load_ab_tests()
    tests.append({
        "topic": topic,
        "title_a": title_a,
        "title_b": title_b,
        "video_a_id": video_a_id,
        "video_b_id": video_b_id,
        "created_at": datetime.utcnow().isoformat() + "Z",
    })
    save_ab_tests(tests)
    log(f"A/B test recorded: {video_a_id} vs {video_b_id}")


# ─── Scheduling ───────────────────────────────────────────────────────────────

def get_best_upload_windows():
    windows = []
    seen = set()
    for market, data in MARKET_PEAK_HOURS.items():
        for hour in data["hours"][:3]:
            try:
                tz = pytz.timezone(data["tz"])
                local_time = datetime.now(tz).replace(hour=hour, minute=0, second=0, microsecond=0)
                utc_str = local_time.astimezone(pytz.utc).strftime("%H:%M")
                if utc_str not in seen:
                    seen.add(utc_str)
                    windows.append({"market": market, "utc": utc_str})
            except Exception as exc:
                log(f"Window error for {market}: {exc}")
    return windows


# ─── Analytics: metrics fetching & self-improvement ──────────────────────────

def fetch_video_metrics(video_id: str) -> dict:
    """Fetch views/likes/comments from Data API v3, avg retention + CTR from Analytics API."""
    access_token = get_yt_access_token()
    metrics = {}

    # Data API v3 — public stats (no Analytics scope needed)
    if YOUTUBE_KEY:
        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics", "id": video_id, "key": YOUTUBE_KEY},
                timeout=20,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    stats = items[0].get("statistics", {})
                    metrics["views"] = int(stats.get("viewCount", 0))
                    metrics["likes"] = int(stats.get("likeCount", 0))
                    metrics["comments"] = int(stats.get("commentCount", 0))
        except Exception as exc:
            log(f"Data API metrics error for {video_id}: {exc}")

    # YouTube Analytics API — retention + CTR (requires yt-analytics.readonly scope)
    if access_token:
        try:
            resp = requests.get(
                "https://youtubeanalytics.googleapis.com/v2/reports",
                params={
                    "ids": "channel==MINE",
                    "filters": f"video=={video_id}",
                    "metrics": "averageViewPercentage,impressionClickThroughRate,estimatedMinutesWatched",
                    "dimensions": "video",
                    "startDate": "2020-01-01",
                    "endDate": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                },
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            )
            if resp.status_code == 200:
                rows = resp.json().get("rows", [])
                if rows:
                    metrics["avg_view_percentage"] = float(rows[0][1]) if len(rows[0]) > 1 else None
                    metrics["ctr"] = float(rows[0][2]) if len(rows[0]) > 2 else None
            elif resp.status_code == 403:
                log(f"Analytics API 403 for {video_id} — token may lack yt-analytics scope, skipping")
        except Exception as exc:
            log(f"Analytics API error for {video_id}: {exc}")

    return metrics


def fetch_analytics_updates():
    """Check all videos that need 24h or 72h metrics and update the DB."""
    log("Fetching analytics updates...")
    db = get_db()
    updated = 0
    for checkpoint in [24, 72]:
        for v in db.get_videos_needing_metrics(checkpoint):
            vid_id = v.get("video_id")
            metrics = fetch_video_metrics(vid_id)
            if metrics.get("views") is not None:
                db.update_video_metrics(vid_id, metrics, f"{checkpoint}h")
                log(f"Updated {checkpoint}h metrics for {vid_id}: {metrics.get('views')} views")
                updated += 1
    if updated == 0:
        log("No videos needed metric updates")


def check_viral_alerts():
    """
    Viral threshold: 1000 views in 24h → trigger 5 more runs on same topic.
    Engagement drop → vary content style (logged only; next run_original_pipeline picks up trend).
    """
    db = get_db()
    for v in db.get_videos_needing_alert_check():
        vid_id = v.get("video_id")
        views_24h = v.get("views_24h", 0) or 0

        if views_24h >= 1000:
            topic_cat = v.get("topic_category", "money_general")
            log(f"VIRAL ALERT: {vid_id} hit {views_24h} views in 24h! Triggering 5 bonus runs on {topic_cat}")
            db.record_alert("viral_hit", vid_id, {"views_24h": views_24h, "topic_category": topic_cat})
            for i in range(5):
                log(f"Viral bonus run {i+1}/5...")
                run_original_pipeline(force_topic_category=topic_cat)
                time.sleep(10)

        trend = db.get_engagement_trend()
        if trend == "falling":
            log(f"Engagement trend is FALLING — next runs will vary style")
            db.record_alert("engagement_drop", vid_id, {"trend": trend})

        db.mark_alert_checked(vid_id)


def run_weekly_analysis():
    """Generate weekly performance report and log key learnings."""
    log("Running weekly analytics analysis...")
    db = get_db()
    report = db.generate_weekly_report()
    if report.get("status") == "no_data":
        log("Weekly report: no data yet")
        return
    log(
        f"Weekly report — {report.get('total_videos')} videos, "
        f"avg {report.get('avg_views_per_video')} views/video, "
        f"trend: {report.get('engagement_trend')}, "
        f"top topic: {report.get('top_topic')}, "
        f"best style: {report.get('best_title_style')}, "
        f"optimal length: {report.get('optimal_length_s')}s"
    )


# ─── Pipelines ────────────────────────────────────────────────────────────────

def run_vugola_pipeline():
    try:
        topic = choose_topic()
        log(f"PIPELINE 1 (Viral finder): {topic}")
        url = find_viral_video(topic)
        if url:
            log(f"Viral video found: {url}")
    except Exception as exc:
        log(f"PIPELINE 1 ERROR: {exc}")


def run_original_pipeline(force_topic_category=None):
    """
    True A/B test: two completely different videos (different topic, script,
    clips, and hook). Both GHA render jobs run in parallel; each video is
    uploaded separately to YouTube. Analytics-guided: length, title style,
    and topic weights are sourced from PerformanceDB.
    """
    temp_dirs = []
    db = get_db()
    target_duration = db.get_optimal_script_length()
    style_hint = db.get_best_title_style()
    log(f"Analytics: target_duration={target_duration}s, style_hint={style_hint}")

    try:
        # ── Two different topics ───────────────────────────────────────────────
        topic_a = choose_topic(force_category=force_topic_category)
        topic_b = choose_topic()
        for _ in range(5):
            if topic_b != topic_a:
                break
            topic_b = choose_topic()
        log(f"PIPELINE 2 — A: '{topic_a}' | B: '{topic_b}'")

        # ── Scripts & metadata ────────────────────────────────────────────────
        script_a = generate_script(topic_a, target_duration=target_duration)
        script_b = generate_script(topic_b, target_duration=target_duration)
        if not script_a or not script_b:
            log("Script generation failed for one or both videos")
            return

        title_a  = generate_title(topic_a, style_hint=style_hint)
        title_b  = generate_title(topic_b, style_hint=style_hint)
        tags_a   = generate_hashtags(topic_a, title_a)
        tags_b   = generate_hashtags(topic_b, title_b)
        desc_a   = build_description(topic_a, tags_a)
        desc_b   = build_description(topic_b, tags_b)

        # ── Voiceovers ─────────────────────────────────────────────────────────
        audio_a = generate_voiceover(script_a)
        audio_b = generate_voiceover(script_b)
        if not audio_a or not audio_b:
            log("Voiceover generation failed for one or both videos")
            return
        temp_dirs += [os.path.dirname(audio_a), os.path.dirname(audio_b)]

        # ── Different stock clips for each video ──────────────────────────────
        clips_a = get_varied_stock_clips(script_a, count=15)
        clips_b = get_varied_stock_clips(script_b, count=15)
        if not clips_a:
            log("No stock clips for video A — aborting")
            return
        if not clips_b:
            log("No stock clips for video B — falling back to shuffled A clips")
            clips_b = clips_a[:]
            random.shuffle(clips_b)

        # ── Upload audio to temp hosts ────────────────────────────────────────
        audio_url_a = upload_to_temp_host(audio_a)
        audio_url_b = upload_to_temp_host(audio_b)
        if not audio_url_a or not audio_url_b:
            log("Audio upload to temp host failed")
            return

        # ── Trigger two GHA render jobs in parallel ───────────────────────────
        run_id_a = trigger_and_get_run_id({
            "audio_url": audio_url_a,
            "stock_video_urls": json.dumps(clips_a),
            "title": title_a[:100],
            "script": script_a[:4000],
        })
        time.sleep(4)  # small gap so run IDs are distinguishable by timestamp
        run_id_b = trigger_and_get_run_id({
            "audio_url": audio_url_b,
            "stock_video_urls": json.dumps(clips_b),
            "title": title_b[:100],
            "script": script_b[:4000],
        })

        if not run_id_a and not run_id_b:
            log("Both GHA triggers failed — aborting")
            return

        # ── Wait for both jobs simultaneously ─────────────────────────────────
        success_a, success_b = wait_for_two_runs(run_id_a, run_id_b, timeout_seconds=660)

        # ── Download & upload each video ──────────────────────────────────────
        vid_a_id = vid_b_id = None

        if success_a and run_id_a:
            video_a = download_video_artifact(run_id_a)
            if video_a:
                temp_dirs.append(os.path.dirname(video_a))
                vid_a_id = upload_video_file(video_a, title_a, desc_a, tags=tags_a)
                if vid_a_id:
                    db.record_upload(vid_a_id, title_a, topic_a, target_duration, ab_variant="A")

        if success_b and run_id_b:
            video_b = download_video_artifact(run_id_b)
            if video_b:
                temp_dirs.append(os.path.dirname(video_b))
                vid_b_id = upload_video_file(video_b, title_b, desc_b, tags=tags_b)
                if vid_b_id:
                    db.record_upload(vid_b_id, title_b, topic_b, target_duration, ab_variant="B")

        if vid_a_id or vid_b_id:
            record_ab_test(topic_a, title_a, title_b, vid_a_id, vid_b_id)
            log(f"Pipeline complete — A={vid_a_id}  B={vid_b_id}")
        else:
            log("Both YouTube uploads failed")

    except Exception as exc:
        log(f"PIPELINE 2 ERROR: {exc}")
    finally:
        for d in temp_dirs:
            try:
                if d and os.path.exists(d):
                    shutil.rmtree(d)
            except Exception as exc:
                log(f"Cleanup error: {exc}")
        gc.collect()


def run_all():
    try:
        run_vugola_pipeline()
        run_original_pipeline()
        gc.collect()
    except Exception as exc:
        log(f"run_all() error: {exc}")


def start():
    try:
        log("WealthShock engine started")

        # ── Upload schedule: use analytics-optimised slots if we have data ────
        db = get_db()
        optimal_slots = db.get_optimal_schedule()
        if optimal_slots:
            log(f"Using analytics-optimised upload schedule ({len(optimal_slots)} slots)")
            for slot in optimal_slots:
                try:
                    schedule.every().day.at(slot["utc"]).do(run_all)
                    log(f"Scheduled (analytics): {slot['day']} {slot['utc']} UTC (score={slot['score']:.0f})")
                except Exception as exc:
                    log(f"Scheduling error: {exc}")
        else:
            log("No analytics data yet — using market-peak schedule")
            for window in get_best_upload_windows():
                try:
                    schedule.every().day.at(window["utc"]).do(run_all)
                    log(f"Scheduled: daily {window['utc']} UTC ({window['market']})")
                except Exception as exc:
                    log(f"Scheduling error: {exc}")

        # ── Recurring analytics jobs ──────────────────────────────────────────
        schedule.every(6).hours.do(fetch_analytics_updates)
        schedule.every(20).minutes.do(check_viral_alerts)
        schedule.every().monday.at("04:00").do(run_weekly_analysis)
        log("Analytics scheduler: metrics every 6h, viral check every 20min, weekly report Mondays 04:00 UTC")

        log("Running initial pipeline...")
        run_original_pipeline()
        gc.collect()
        log("Initial pipeline complete")

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
