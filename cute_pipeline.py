"""
Cute Viral Video Pipeline — zero text, zero captions, zero voiceover.

Type 1: Kling AI text-to-video (adorable AI creatures / food babies)
Type 2: Pixabay real animal clips (cute cats, dogs, baby animals)

Both types: visuals + soft piano/playful music + mood SFX only.
"""

import gc
import json
import os
import random
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone

import requests

try:
    import jwt as pyjwt
    _HAS_JWT = True
except ImportError:
    _HAS_JWT = False

KLING_BASE = "https://api.klingai.com"
KLING_ACCESS_KEY = os.environ.get("KLING_ACCESS_KEY")
KLING_SECRET_KEY = os.environ.get("KLING_SECRET_KEY")
PIXABAY_KEY = os.environ.get("PIXABAY_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GH_PAT = os.environ.get("GH_PAT")
GH_REPO = os.environ.get("GH_REPO")
YT_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YOUTUBE_REFRESH_TOKEN")
FREESOUND_KEY = os.environ.get("FREESOUND_API_KEY")

GEMINI_MODEL = "gemini-2.5-flash-lite"


def _log(msg):
    print(f"[CUTE {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ─── Kling AI prompts ─────────────────────────────────────────────────────────

KLING_PROMPTS = [
    # Food babies
    "adorable tiny tangerine baby with big glassy eyes being gently fed with a silver spoon, soft warm golden lighting, cinematic macro, heartwarming, no text, 9:16 vertical",
    "miniature strawberry creature with tiny chubby hands waving hello, pastel pink background, magical sparkle bokeh, ultra cute, cinematic, no text",
    "baby avocado character with round shiny eyes sitting in a tiny woven hammock swinging gently, warm dappled sunlight, whimsical, no text",
    "tiny blueberry baby crawling toward a dewdrop, soft morning light, macro cinematic, heartwarming, pastel tones, no text",
    "little watermelon seed creature with big tearful eyes sneezing adorably, slow motion, soft studio lighting, no text",
    "cute lemon chick hatching from a lemon with tiny fluffy wings, sunrise warm light, cinematic, ultra adorable, no text",
    "baby grape cluster character giggling and bouncing, purple pastel world, sparkle particles, no text, cinematic vertical",
    "tiny mushroom elf with dew-drop eyes peeking out of morning grass, magical forest bokeh, soft glow, no text",
    # Magical creatures
    "fluffy cloud kitten floating gently through a pastel sky, dreamy bokeh, cinematic vertical, soft piano mood, no text",
    "tiny glowing firefly fairy resting on a flower petal, night forest, magical particles, warm amber light, no text",
    "miniature bunny astronaut floating in space among stars and planets, pastel galaxy, adorable, no text",
    "baby dragon the size of a palm sleeping curled on a moss stone, soft emerald forest glow, cinematic macro, no text",
    "tiny polar bear cub building a snowflake in slow motion, winter bokeh, warm scarf, heartwarming, no text",
    "micro hedgehog wrapped in a single autumn leaf like a blanket, soft golden light, macro, ultra cute, no text",
    # Real-world cute
    "golden retriever puppy seeing snow for the very first time, slow motion, pure joy, cinematic, no text",
    "baby duckling following its mother across a mirror-still pond at golden hour, cinematic, peaceful, no text",
    "tiny kitten discovering a soap bubble for the first time, wide eyes, playful, soft light, no text, slow motion",
    "baby elephant splashing in a shallow puddle with pure delight, slow motion, warm savanna light, no text",
    "hamster stuffing its cheeks with sunflower seeds in extreme close-up, soft light, adorable, no text",
    "baby otter floating on its back on calm water eating a clam, golden hour, peaceful, cinematic, no text",
]

# Pixabay queries for cute animal clips (Type 2)
CUTE_ANIMAL_QUERIES = [
    "cute kitten cat playing adorable",
    "puppy dog adorable funny",
    "baby duck ducklings water",
    "hamster eating cute small",
    "bunny rabbit fluffy cute",
    "baby animal nature cute",
    "otter playing water cute",
    "baby panda bear cute",
    "golden retriever puppy",
    "baby deer fawn nature",
    "hedgehog cute tiny",
    "baby owl bird cute",
]

CUTE_TITLE_TEMPLATES = [
    "This tiny creature will heal your soul 🥺",
    "I didn't expect to cry at this 😭🥰",
    "The cutest thing the internet ever made ❤️",
    "My heart broke and then fixed itself 💔💕",
    "Warning: dangerously adorable content ahead 🥺",
    "POV: you needed this today 🥰",
    "Instant mood fix 💕✨",
    "This should be illegal it's too cute 😭",
    "I will protect this creature with my life 🛡️🥺",
    "Nature said: here's something beautiful 🌸",
]


# ─── Kling AI client ──────────────────────────────────────────────────────────

class KlingClient:
    def __init__(self, access_key: str, secret_key: str):
        self.access_key = access_key
        self.secret_key = secret_key

    def _token(self) -> str:
        if not _HAS_JWT:
            raise RuntimeError("PyJWT not installed — run: pip install PyJWT")
        now = int(time.time())
        payload = {
            "iss": self.access_key,
            "exp": now + 1800,
            "nbf": now - 5,
        }
        return pyjwt.encode(payload, self.secret_key, algorithm="HS256")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }

    def create_text2video(self, prompt: str, duration: int = 5, aspect_ratio: str = "9:16") -> str | None:
        """Submit a text-to-video task. Returns task_id or None."""
        body = {
            "model_name": "kling-v2.6-pro",
            "prompt": prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "cfg_scale": 0.5,
            "negative_prompt": "text, watermark, blur, low quality, distorted",
        }
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{KLING_BASE}/v1/videos/text2video",
                    headers=self._headers(),
                    json=body,
                    timeout=30,
                )
                data = resp.json()
                if resp.status_code in (200, 201) and data.get("code") == 0:
                    task_id = data["data"]["task_id"]
                    _log(f"Kling task created: {task_id}")
                    return task_id
                _log(f"Kling create attempt {attempt+1}: {resp.status_code} {data}")
            except Exception as exc:
                _log(f"Kling create error (attempt {attempt+1}): {exc}")
            time.sleep(5)
        return None

    def poll(self, task_id: str, timeout: int = 900) -> str | None:
        """Poll until task succeeds. Returns video URL or None."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                resp = requests.get(
                    f"{KLING_BASE}/v1/videos/text2video/{task_id}",
                    headers=self._headers(),
                    timeout=20,
                )
                data = resp.json()
                if resp.status_code == 200 and data.get("code") == 0:
                    task_data = data.get("data", {})
                    status = task_data.get("task_status", "")
                    _log(f"Kling task {task_id}: {status}")
                    if status == "succeed":
                        videos = task_data.get("task_result", {}).get("videos", [])
                        if videos:
                            return videos[0].get("url")
                    if status == "failed":
                        _log(f"Kling task {task_id} FAILED")
                        return None
            except Exception as exc:
                _log(f"Kling poll error: {exc}")
            time.sleep(15)
        _log(f"Kling task {task_id} timed out after {timeout}s")
        return None

    def generate(self, prompt: str) -> str | None:
        """Create + poll. Returns direct video URL or None."""
        task_id = self.create_text2video(prompt, duration=5)
        if not task_id:
            return None
        return self.poll(task_id)


def _get_kling_client() -> KlingClient | None:
    if not _HAS_JWT:
        _log("PyJWT not installed — Kling disabled")
        return None
    if not KLING_ACCESS_KEY or not KLING_SECRET_KEY:
        _log("KLING_ACCESS_KEY or KLING_SECRET_KEY not set")
        return None
    return KlingClient(KLING_ACCESS_KEY, KLING_SECRET_KEY)


# ─── Music download ───────────────────────────────────────────────────────────

def download_cute_music(dest_path: str, mood: str = "soft") -> str | None:
    """Download royalty-free music for cute content. Returns path or None."""

    tag_map = {
        "soft":      "piano,soft,ambient,heartwarming",
        "playful":   "cute,playful,fun,ukulele",
        "dreamy":    "ambient,dreamy,soft,instrumental",
    }
    tags = tag_map.get(mood, "piano,soft,instrumental")

    # 1. ccmixter.org
    try:
        resp = requests.get(
            "http://ccmixter.org/api/query",
            params={"f": "json", "tags": tags, "type": "instrumentals", "limit": 8, "ord": "rand"},
            timeout=20,
        )
        if resp.status_code == 200:
            tracks = resp.json()
            random.shuffle(tracks)
            for track in tracks:
                url = track.get("fileip") or track.get("file_download", "")
                if url and any(url.lower().endswith(ext) for ext in (".mp3", ".ogg", ".wav")):
                    r = requests.get(url, timeout=60, stream=True)
                    if r.status_code == 200:
                        with open(dest_path, "wb") as f:
                            for chunk in r.iter_content(8192):
                                if chunk:
                                    f.write(chunk)
                        if os.path.getsize(dest_path) > 10_000:
                            _log(f"Music (ccmixter): {os.path.getsize(dest_path)//1024}KB")
                            return dest_path
    except Exception as exc:
        _log(f"ccmixter music error: {exc}")

    # 2. Freemusicarchive.org — curated no-key playlists for gentle music
    fma_urls = [
        "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/no_curator/Kai_Engel/Satin/Kai_Engel_-_01_-_Satin.mp3",
        "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/ccCommunity/Chad_Crouch/Arps/Chad_Crouch_-_Algorithms.mp3",
        "https://files.freemusicarchive.org/storage-freemusicarchive-org/music/no_curator/Kai_Engel/Chill_Serenity/Kai_Engel_-_01_-_Dreamland.mp3",
    ]
    random.shuffle(fma_urls)
    for url in fma_urls:
        try:
            r = requests.get(url, timeout=60, stream=True)
            if r.status_code == 200:
                with open(dest_path, "wb") as f:
                    for chunk in r.iter_content(8192):
                        if chunk:
                            f.write(chunk)
                if os.path.getsize(dest_path) > 10_000:
                    _log(f"Music (FMA): {os.path.getsize(dest_path)//1024}KB")
                    return dest_path
        except Exception as exc:
            _log(f"FMA music error: {exc}")

    _log("All music sources failed")
    return None


# ─── SFX download ─────────────────────────────────────────────────────────────

_CUTE_SFX_FALLBACKS = {
    "eating": "https://cdn.mixkit.co/sfx/preview/mixkit-fast-small-sweep-transition-166.mp3",
    "purr":   "https://cdn.mixkit.co/sfx/preview/mixkit-cat-meow-1678.mp3",
    "sparkle":"https://cdn.mixkit.co/sfx/preview/mixkit-fairy-arcade-sparkle-866.mp3",
    "pop":    "https://cdn.mixkit.co/sfx/preview/mixkit-bubble-pop-up-alert-notification-2357.mp3",
}


def download_cute_sfx(sfx_dir: str, sfx_type: str = "sparkle") -> str | None:
    os.makedirs(sfx_dir, exist_ok=True)
    dest = os.path.join(sfx_dir, f"{sfx_type}.mp3")

    if FREESOUND_KEY:
        queries = {"eating": "eating munching cute", "purr": "cat purring soft", "sparkle": "sparkle magic cute", "pop": "pop cute bubble"}
        query = queries.get(sfx_type, "cute sound")
        try:
            resp = requests.get(
                "https://freesound.org/apiv2/search/text/",
                params={"query": query, "token": FREESOUND_KEY, "fields": "id,previews",
                        "filter": "duration:[0.1 TO 4.0]", "page_size": 5},
                timeout=20,
            )
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for r in results:
                    url = r.get("previews", {}).get("preview-hq-mp3")
                    if url:
                        dl = requests.get(url, timeout=30)
                        if dl.status_code == 200:
                            with open(dest, "wb") as f:
                                f.write(dl.content)
                            if os.path.getsize(dest) > 1000:
                                return dest
        except Exception as exc:
            _log(f"Freesound SFX error: {exc}")

    # Fallback: Mixkit CDN
    url = _CUTE_SFX_FALLBACKS.get(sfx_type, _CUTE_SFX_FALLBACKS["sparkle"])
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            with open(dest, "wb") as f:
                f.write(r.content)
            return dest
    except Exception as exc:
        _log(f"Mixkit SFX error: {exc}")

    return None


# ─── Pixabay cute animal clips ────────────────────────────────────────────────

def get_cute_animal_clip_url() -> str | None:
    if not PIXABAY_KEY:
        _log("PIXABAY_API_KEY not set")
        return None
    query = random.choice(CUTE_ANIMAL_QUERIES)
    try:
        resp = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": PIXABAY_KEY,
                "q": query,
                "per_page": 10,
                "video_type": "film",
                "safesearch": "true",
                "order": "popular",
            },
            timeout=20,
        )
        if resp.status_code == 200:
            hits = resp.json().get("hits", [])
            random.shuffle(hits)
            for hit in hits:
                for quality in ["small", "medium"]:
                    url = hit.get("videos", {}).get(quality, {}).get("url", "")
                    if url.startswith("https://"):
                        _log(f"Pixabay animal clip: {query}")
                        return url
    except Exception as exc:
        _log(f"Pixabay animal clip error: {exc}")
    return None


# ─── Title + tags generation ──────────────────────────────────────────────────

def _gemini_generate(prompt: str) -> str | None:
    if not GEMINI_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    try:
        resp = requests.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        return None


def generate_cute_title(context: str) -> str:
    prompt = (
        f"Write a viral YouTube Shorts title (under 60 chars) for a video of: {context}.\n"
        "Make it emotional, wholesome, and universally appealing.\n"
        "Use 1-2 emojis. No hashtags. Return only the title text."
    )
    result = _gemini_generate(prompt)
    if result:
        return result[:100].strip('"').strip("'")
    return random.choice(CUTE_TITLE_TEMPLATES)


def generate_cute_tags() -> list:
    return [
        "cute", "adorable", "wholesome", "heartwarming", "aww", "animals",
        "cuteanimals", "satisfying", "shorts", "viral", "trending", "love",
        "baby", "sweet", "funny", "mood", "smile", "relax", "nature",
    ]


# ─── Temp file hosting ────────────────────────────────────────────────────────

def upload_to_temp_host(file_path: str) -> str | None:
    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post("https://0x0.st", files={"file": f}, timeout=120)
            if resp.status_code == 200 and resp.text.strip().startswith("http"):
                return resp.text.strip()
        except Exception as exc:
            _log(f"0x0.st attempt {attempt+1}: {exc}")
        time.sleep(3)

    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post("https://file.io", files={"file": f}, timeout=120)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("link"):
                    return data["link"]
        except Exception as exc:
            _log(f"file.io attempt {attempt+1}: {exc}")
        time.sleep(3)

    for attempt in range(3):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post("https://tmpfiles.org/api/v1/upload", files={"file": f}, timeout=120)
            if resp.status_code == 200:
                url = resp.json().get("data", {}).get("url", "")
                if url:
                    return url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        except Exception as exc:
            _log(f"tmpfiles.org attempt {attempt+1}: {exc}")
        time.sleep(3)

    return None


# ─── GitHub Actions ───────────────────────────────────────────────────────────

def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def trigger_cute_render(clip_urls: list, music_url: str, title: str, target_duration: int = 30) -> int | None:
    if not GH_PAT or not GH_REPO:
        _log("Missing GH_PAT or GH_REPO")
        return None
    dispatch_url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process_cute_video.yml/dispatches"
    before = datetime.now(timezone.utc)

    inputs = {
        "clip_urls": json.dumps(clip_urls),
        "music_url": music_url,
        "title": title[:100],
        "target_duration": str(target_duration),
    }
    for attempt in range(3):
        try:
            resp = requests.post(
                dispatch_url,
                json={"ref": "main", "inputs": inputs},
                headers=_gh_headers(),
                timeout=30,
            )
            if resp.status_code == 204:
                _log("Cute GHA workflow dispatched")
                break
            _log(f"Cute dispatch attempt {attempt+1}: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            _log(f"Cute dispatch error: {exc}")
        time.sleep(5)
    else:
        return None

    runs_url = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/process_cute_video.yml/runs"
    for _ in range(24):
        time.sleep(5)
        try:
            resp = requests.get(runs_url, headers=_gh_headers(), params={"per_page": 10}, timeout=20)
            if resp.status_code != 200:
                continue
            for run in resp.json().get("workflow_runs", []):
                run_time = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
                if run_time >= before:
                    _log(f"Cute GHA run ID: {run['id']}")
                    return run["id"]
        except Exception as exc:
            _log(f"Cute run polling error: {exc}")

    _log("Timed out finding cute GHA run ID")
    return None


def wait_for_run(run_id: int, timeout: int = 600) -> bool:
    if not run_id:
        return False
    url = f"https://api.github.com/repos/{GH_REPO}/actions/runs/{run_id}"
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(url, headers=_gh_headers(), timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                status, conclusion = data.get("status"), data.get("conclusion")
                _log(f"Cute GHA run {run_id}: {status}/{conclusion}")
                if status == "completed":
                    return conclusion == "success"
        except Exception as exc:
            _log(f"Cute run status error: {exc}")
        time.sleep(30)
    _log(f"Cute GHA run {run_id} timed out")
    return False


def download_artifact(run_id: int) -> str | None:
    if not run_id:
        return None
    tmp = None
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/actions/runs/{run_id}/artifacts",
            headers=_gh_headers(), timeout=20,
        )
        artifacts = resp.json().get("artifacts", []) if resp.status_code == 200 else []
        if not artifacts:
            _log("No cute video artifacts")
            return None

        tmp = tempfile.mkdtemp()
        dl_url = f"https://api.github.com/repos/{GH_REPO}/actions/artifacts/{artifacts[0]['id']}/zip"
        zip_path = os.path.join(tmp, "cute.zip")
        resp = requests.get(dl_url, headers=_gh_headers(), stream=True, timeout=300, allow_redirects=True)
        if resp.status_code != 200:
            _log(f"Cute artifact download failed: {resp.status_code}")
            return None
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(65536):
                if chunk:
                    f.write(chunk)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmp)
        os.remove(zip_path)
        for name in os.listdir(tmp):
            if name.endswith(".mp4"):
                path = os.path.join(tmp, name)
                _log(f"Cute artifact: {name} ({os.path.getsize(path)//1024}KB)")
                return path
        _log("No MP4 in cute artifact")
        return None
    except Exception as exc:
        _log(f"Cute artifact error: {exc}")
        if tmp and os.path.exists(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        return None


# ─── YouTube upload ───────────────────────────────────────────────────────────

def _get_yt_token() -> str | None:
    if not all([YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN]):
        return None
    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={"client_id": YT_CLIENT_ID, "client_secret": YT_CLIENT_SECRET,
                  "refresh_token": YT_REFRESH_TOKEN, "grant_type": "refresh_token"},
            timeout=20,
        )
        data = resp.json()
        if resp.status_code == 200 and "access_token" in data:
            return data["access_token"]
    except Exception as exc:
        _log(f"YouTube auth error: {exc}")
    return None


def upload_to_youtube(video_path: str, title: str) -> str | None:
    access_token = _get_yt_token()
    if not access_token:
        _log("Cannot upload: no YouTube token")
        return None

    tags = generate_cute_tags()
    description = (
        "Pure cuteness. No words needed. 💕\n\n"
        "#cute #adorable #wholesome #heartwarming #aww #cuteanimals #shorts #viral"
    )
    meta = {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": "22",
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }

    boundary = "----CuteVideoBoundary"

    def _stream():
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

    for attempt in range(3):
        try:
            resp = requests.post(
                "https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&uploadType=multipart",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": f"multipart/related; boundary={boundary}",
                },
                data=_stream(),
                timeout=300,
            )
            if resp.status_code in (200, 201):
                vid = resp.json().get("id")
                _log(f"Cute video uploaded: {vid}")
                return vid
            _log(f"YT upload attempt {attempt+1}: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            _log(f"YT upload error: {exc}")
        time.sleep(10)
    return None


# ─── Main pipeline functions ──────────────────────────────────────────────────

def run_kling_cute_video() -> str | None:
    """Type 1: Kling AI generated cute creature video."""
    client = _get_kling_client()
    if not client:
        _log("Kling not available — skipping Type 1")
        return None

    tmp = tempfile.mkdtemp()
    try:
        prompt = random.choice(KLING_PROMPTS)
        _log(f"Kling prompt: {prompt[:80]}…")

        # Generate 2 short clips for 10s total (5s + 5s)
        clip_urls = []
        for i in range(2):
            variant_prompt = prompt if i == 0 else prompt.replace("being gently fed with a silver spoon", "smiling and blinking happily").replace("waving hello", "doing a tiny spin").replace("waving hello", "looking at the camera curiously")
            url = client.generate(variant_prompt)
            if url:
                clip_urls.append(url)
                _log(f"Kling clip {i+1}/2 ready: {url[:60]}…")
            else:
                _log(f"Kling clip {i+1}/2 failed — continuing with {len(clip_urls)} clip(s)")
                break

        if not clip_urls:
            _log("No Kling clips generated")
            return None

        # Download music
        music_path = os.path.join(tmp, "music.mp3")
        music_path = download_cute_music(music_path, mood="soft")
        if not music_path:
            _log("Music download failed for Kling video")
            return None

        music_url = upload_to_temp_host(music_path)
        if not music_url:
            _log("Music temp upload failed")
            return None

        title = generate_cute_title("adorable AI-generated cute creature")
        _log(f"Title: {title}")

        run_id = trigger_cute_render(clip_urls, music_url, title, target_duration=30)
        if not run_id:
            return None

        if not wait_for_run(run_id, timeout=600):
            _log("Cute GHA render failed")
            return None

        video_path = download_artifact(run_id)
        if not video_path:
            return None

        video_id = upload_to_youtube(video_path, title)
        return video_id

    except Exception as exc:
        _log(f"Kling cute pipeline error: {exc}")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        gc.collect()


def run_pixabay_cute_video() -> str | None:
    """Type 2: Real cute animal clip from Pixabay."""
    tmp = tempfile.mkdtemp()
    try:
        clip_url = get_cute_animal_clip_url()
        if not clip_url:
            _log("No Pixabay animal clip available")
            return None

        music_path = os.path.join(tmp, "music.mp3")
        music_path = download_cute_music(music_path, mood=random.choice(["soft", "playful", "dreamy"]))
        if not music_path:
            _log("Music download failed for Pixabay video")
            return None

        music_url = upload_to_temp_host(music_path)
        if not music_url:
            _log("Music temp upload failed")
            return None

        query = random.choice(CUTE_ANIMAL_QUERIES)
        title = generate_cute_title(f"cute {query} animal video")
        _log(f"Title: {title}")

        run_id = trigger_cute_render([clip_url], music_url, title, target_duration=35)
        if not run_id:
            return None

        if not wait_for_run(run_id, timeout=600):
            _log("Cute GHA render failed")
            return None

        video_path = download_artifact(run_id)
        if not video_path:
            return None

        video_id = upload_to_youtube(video_path, title)
        return video_id

    except Exception as exc:
        _log(f"Pixabay cute pipeline error: {exc}")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        gc.collect()


_cute_type_counter = 0


def run_cute_pipeline():
    """
    Alternate between Type 1 (Kling) and Type 2 (Pixabay).
    Falls back to Pixabay if Kling is unavailable.
    """
    global _cute_type_counter
    _cute_type_counter += 1

    kling_available = bool(KLING_ACCESS_KEY and KLING_SECRET_KEY and _HAS_JWT)
    use_kling = kling_available and (_cute_type_counter % 2 == 1)

    if use_kling:
        _log("=== CUTE PIPELINE: Type 1 (Kling AI) ===")
        vid = run_kling_cute_video()
    else:
        _log("=== CUTE PIPELINE: Type 2 (Pixabay animals) ===")
        vid = run_pixabay_cute_video()

    if vid:
        _log(f"Cute video live: https://youtube.com/shorts/{vid}")
    else:
        _log("Cute pipeline: no video uploaded this run")
    return vid


def run_cute_demo():
    """Run exactly 2 demo videos (one of each type) at startup."""
    _log("=== CUTE DEMO: Running 2 test videos ===")

    _log("Demo 1/2 — Pixabay real animal clip")
    vid1 = run_pixabay_cute_video()

    _log("Demo 2/2 — Kling AI cute creature")
    vid2 = run_kling_cute_video()

    _log(f"Demo complete — Pixabay={vid1}  Kling={vid2}")
    return vid1, vid2
