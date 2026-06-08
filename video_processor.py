#!/usr/bin/env python3
"""
WealthShock Video Processor v5 — Remotion Orchestrator
Runs in GitHub Actions (ubuntu-latest, 14 GB RAM).

Inputs (env vars):
  AUDIO_URL          — direct download URL for the MP3 voiceover
  STOCK_VIDEO_URLS   — JSON array of Pixabay video URLs (15-20)
  TITLE              — video title
  SCRIPT             — spoken script text for word-synced captions
  FREESOUND_API_KEY  — (optional) freesound.org API key for SFX downloads

Pipeline:
  1. Download voiceover MP3 → remotion/public/audio/voice.mp3
  2. Detect audio duration via ffprobe
  3. Download background music (ccmixter → Mixkit fallback)
  4. Download stock clips in parallel → remotion/public/clips/clip_NN.mp4
  5. Download SFX → remotion/public/sfx/
  6. Parse captions with emphasis + emoji
  7. Detect hook text, stat charts, SFX events, cut frames
  8. Write remotion_props.json
  9. node remotion/render.js → final_video.mp4

Output:
  final_video.mp4 — 1080x1920, 30fps, H.264
"""

import concurrent.futures
import json
import os
import re
import random
import shutil
import subprocess
import sys
import time

import requests

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REMOTION_DIR = os.path.join(SCRIPT_DIR, 'remotion')
PUBLIC_DIR   = os.path.join(REMOTION_DIR, 'public')
CLIPS_DIR    = os.path.join(PUBLIC_DIR, 'clips')
AUDIO_DIR    = os.path.join(PUBLIC_DIR, 'audio')
SFX_DIR      = os.path.join(PUBLIC_DIR, 'sfx')
PROPS_FILE   = os.path.join(SCRIPT_DIR, 'remotion_props.json')
OUTPUT_FILE  = os.path.join(SCRIPT_DIR, 'final_video.mp4')

# ─── Constants ────────────────────────────────────────────────────────────────
FPS            = 30
HOOK_FRAMES    = 90
HOOK_CLIP_DUR  = 15
MAIN_CLIP_DUR  = 40
TRANSITION_DUR = 6

MUSIC_URLS = [
    "https://assets.mixkit.co/music/preview/mixkit-dark-cinematic-drums-570.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-hip-hop-02-738.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-tech-house-vibes-130.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-driving-ambition-32.mp3",
    "https://assets.mixkit.co/music/preview/mixkit-sleek-corporate-background-music-680.mp3",
]

# ─── Power words and emoji map ─────────────────────────────────────────────────
POWER_WORDS = {
    'never', 'always', 'shocking', 'truth', 'secret', 'lie', 'lies', 'warning',
    'insane', 'crazy', 'unbelievable', 'impossible', 'banned', 'forbidden',
    'hidden', 'exposed', 'scam', 'fraud', 'broke', 'rich', 'millionaire', 'billion',
    'trillion', 'steal', 'revealed', 'reveal', 'must', 'critical', 'dangerous',
    'massive', 'extreme', 'fake', 'manipulation', 'manipulated', 'wake', 'up',
}

EMOJI_MAP = {
    'money': '💰', 'cash': '💵', 'dollar': '💵', 'rich': '🤑', 'wealth': '💎', 'wealthy': '💎',
    'stock': '📈', 'stocks': '📈', 'invest': '💹', 'investing': '💹', 'market': '📊',
    'crypto': '₿', 'bitcoin': '₿', 'blockchain': '⛓',
    'shocking': '🤯', 'insane': '🤯', 'crazy': '😱', 'unbelievable': '😱', 'impossible': '🤯',
    'secret': '🤫', 'truth': '💡', 'revealed': '👁', 'hidden': '🙈',
    'bank': '🏦', 'billion': '💰', 'million': '💰', 'trillion': '💰',
    'ai': '🤖', 'artificial': '🤖', 'technology': '💻',
    'fire': '🔥', 'hot': '🔥', 'viral': '🔥',
}


def log(msg):
    print(f"[processor] {msg}", flush=True)


# ─── Download helpers ─────────────────────────────────────────────────────────

def stream_download(url, dest, retries=3):
    for attempt in range(retries):
        try:
            with requests.get(url, stream=True, timeout=60, allow_redirects=True) as r:
                r.raise_for_status()
                with open(dest, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
            if os.path.exists(dest) and os.path.getsize(dest) > 1024:
                return True
        except Exception as exc:
            log(f"Download attempt {attempt + 1} failed ({url[:60]}): {exc}")
            time.sleep(2)
    return False


def _download_clip(args):
    url, dest = args
    return dest if stream_download(url, dest) else None


# ─── Audio duration ───────────────────────────────────────────────────────────

def get_duration(path):
    try:
        r = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', path],
            capture_output=True, text=True, timeout=20,
        )
        if r.returncode == 0:
            val = r.stdout.strip()
            return float(val) if val else None
    except Exception:
        pass
    return None


# ─── Caption parsing ──────────────────────────────────────────────────────────

def parse_captions(script, audio_duration, fps=FPS):
    """
    Returns list of CaptionWord dicts with emphasis + emoji fields.
    Uses char-proportional timing. Detects:
    - red: numbers, $amounts, percentages
    - yellow: POWER_WORDS
    - emoji: EMOJI_MAP lookup
    """
    words = re.findall(r'\S+', script)
    if not words:
        return []

    char_counts = [max(1, len(w)) for w in words]
    total_chars = sum(char_counts)
    char_rate = audio_duration / total_chars  # seconds per character

    # Clamp each word's raw duration, then rescale so total == audio_duration
    raw = [max(0.08, min(0.9, c * char_rate)) for c in char_counts]
    scale = audio_duration / sum(raw)
    durs = [d * scale for d in raw]

    captions = []
    frame = 0
    for word, dur in zip(words, durs):
        end_frame = frame + max(2, round(dur * fps))
        clean = re.sub(r'[^a-z0-9%$]', '', word.lower())

        # Emphasis detection
        if re.search(r'\$[\d,]+|\d+%|\d+[KMBkmb]', word):
            emphasis = 'red'
        elif clean in POWER_WORDS:
            emphasis = 'yellow'
        else:
            emphasis = None

        # Emoji
        emoji = EMOJI_MAP.get(clean, '')

        captions.append({
            'text': word,
            'startFrame': frame,
            'endFrame': end_frame,
            'emphasis': emphasis,
            'emoji': emoji,
        })
        frame = end_frame

    return captions


def detect_hook_text(script, title):
    """First punchy fragment of the script (≤7 words), or fallback to title."""
    first = re.split(r'[.!?]', script.strip())[0].strip()
    words = first.split()[:7]
    text = ' '.join(words) if len(words) >= 3 else title[:60]
    return text or 'SHOCKING MONEY FACTS'


def detect_stat_charts(captions):
    """Return up to 3 StatChart entries for animated number counters."""
    charts = []
    for w in captions:
        text = w['text']
        m = re.search(
            r'\$?([\d,]+(?:\.\d+)?)\s*(k|m|b|t|thousand|million|billion|%)?',
            text, re.IGNORECASE
        )
        if m:
            try:
                val = float(m.group(1).replace(',', ''))
                suffix = (m.group(2) or '').lower()
                mult = {
                    'k': 1e3, 'm': 1e6, 'b': 1e9, 't': 1e12,
                    'thousand': 1e3, 'million': 1e6, 'billion': 1e9,
                }.get(suffix, 1)
                val *= mult
                if val > 99:
                    unit = '%' if '%' in text else ('$' if '$' in text else '')
                    charts.append({
                        'frame': w['startFrame'],
                        'value': min(int(val), 9999999),
                        'unit': unit,
                        'label': text,
                    })
            except ValueError:
                pass
        if len(charts) >= 3:
            break
    return charts


def calculate_cut_frames(total_frames, fps=FPS):
    """
    Mirror the Remotion component's pacing logic to know where cuts happen.
    Returns list of frame numbers where cuts occur.
    """
    cuts = []
    f = 0
    while f < HOOK_FRAMES:
        cuts.append(f)
        f += HOOK_CLIP_DUR
    while f < total_frames:
        cuts.append(f)
        f += MAIN_CLIP_DUR + TRANSITION_DUR
    return cuts


# ─── SFX ─────────────────────────────────────────────────────────────────────

def download_sfx(sfx_dir, freesound_key=None):
    """
    Download SFX. Try freesound.org if key available, else Mixkit CDN.
    Returns dict {sfx_type: 'sfx/filename.ext'} for successful downloads.
    """
    MIXKIT = {
        'whoosh': [
            'https://assets.mixkit.co/sfx/preview/mixkit-fast-double-air-woosh-2817.wav',
            'https://assets.mixkit.co/sfx/preview/mixkit-air-swoosh-1000.wav',
            'https://assets.mixkit.co/sfx/preview/mixkit-fast-small-whoosh-1-785.wav',
        ],
        'ching': [
            'https://assets.mixkit.co/sfx/preview/mixkit-coins-in-a-bag-3-1173.wav',
            'https://assets.mixkit.co/sfx/preview/mixkit-coin-win-notification-1992.wav',
            'https://assets.mixkit.co/sfx/preview/mixkit-cash-register-cha-ching-1040.wav',
        ],
        'bass': [
            'https://assets.mixkit.co/sfx/preview/mixkit-cinematic-bass-hit-2808.mp3',
            'https://assets.mixkit.co/sfx/preview/mixkit-deep-bass-drop-2818.mp3',
        ],
    }

    os.makedirs(sfx_dir, exist_ok=True)
    available = {}

    # Try freesound if key is provided
    if freesound_key:
        FREESOUND_QUERIES = {
            'whoosh': 'whoosh swipe transition',
            'ching': 'cash register coin money',
            'bass': 'cinematic bass hit impact',
        }
        for sfx_type, query in FREESOUND_QUERIES.items():
            try:
                resp = requests.get(
                    'https://freesound.org/apiv2/search/text/',
                    params={
                        'query': query,
                        'fields': 'id,name,previews',
                        'filter': 'duration:[0.1 TO 3.0]',
                        'page_size': 5,
                        'token': freesound_key,
                    },
                    timeout=15,
                )
                if resp.status_code == 200:
                    results = resp.json().get('results', [])
                    for sound in results[:3]:
                        preview_url = sound.get('previews', {}).get('preview-hq-mp3', '')
                        if preview_url:
                            dest = os.path.join(sfx_dir, f'{sfx_type}.mp3')
                            if stream_download(preview_url, dest):
                                available[sfx_type] = f'sfx/{sfx_type}.mp3'
                                log(f'SFX {sfx_type}: freesound OK ({os.path.getsize(dest)//1024}KB)')
                                break
            except Exception as exc:
                log(f'Freesound {sfx_type} failed: {exc}')

    # Mixkit fallback for anything not yet downloaded
    for sfx_type, urls in MIXKIT.items():
        if sfx_type in available:
            continue
        for url in urls:
            ext = '.mp3' if url.endswith('.mp3') else '.wav'
            dest = os.path.join(sfx_dir, f'{sfx_type}{ext}')
            if stream_download(url, dest):
                available[sfx_type] = f'sfx/{sfx_type}{ext}'
                log(f'SFX {sfx_type}: OK ({os.path.getsize(dest)//1024}KB)')
                break
        else:
            log(f'SFX {sfx_type}: all URLs failed')

    return available


def generate_sfx_events(captions, cut_frames, available_sfx):
    """Schedule SFX at cuts and on money/power words."""
    events = []
    whoosh = available_sfx.get('whoosh', '')
    ching = available_sfx.get('ching', '')
    bass = available_sfx.get('bass', '')

    # Whoosh 2 frames before each cut (swoosh-then-cut feel)
    if whoosh:
        for f in cut_frames[1:]:  # skip first frame (video start)
            events.append({'frame': max(0, f - 2), 'src': whoosh, 'volume': 0.65})

    # Ching on dollar/large number captions
    if ching:
        for w in captions:
            t = w['text']
            if '$' in t or re.search(r'\d+[KMBkmb]|million|billion', t, re.IGNORECASE):
                events.append({'frame': w['startFrame'], 'src': ching, 'volume': 0.55})

    # Bass hit on yellow emphasis words
    if bass:
        for w in captions:
            if w.get('emphasis') == 'yellow':
                events.append({'frame': w['startFrame'], 'src': bass, 'volume': 0.35})

    # Deduplicate by frame (keep highest volume if collision)
    by_frame = {}
    for e in events:
        f = e['frame']
        if f not in by_frame or e['volume'] > by_frame[f]['volume']:
            by_frame[f] = e
    return sorted(by_frame.values(), key=lambda x: x['frame'])


# ─── Music download ───────────────────────────────────────────────────────────

def download_music_ccmixter(dest, music_type='hype'):
    """Try ccmixter.org free music API."""
    tags_map = {
        'hype': 'hip-hop,electronic',
        'suspense': 'ambient,dark',
        'motivational': 'piano,uplifting',
    }
    tags = tags_map.get(music_type, tags_map['hype'])
    try:
        resp = requests.get(
            'http://ccmixter.org/api/query',
            params={'f': 'json', 'type': 'sample', 'tags': tags, 'limit': 6, 'ord': 'rand'},
            timeout=20,
        )
        if resp.status_code == 200:
            tracks = resp.json()
            if isinstance(tracks, list):
                random.shuffle(tracks)
                for track in tracks[:4]:
                    url = track.get('fileip') or track.get('file_download') or ''
                    if url and url.startswith('http') and any(
                        url.endswith(ext) for ext in ['.mp3', '.ogg', '.wav']
                    ):
                        if stream_download(url, dest):
                            log(f'Music: ccmixter "{track.get("upload_name", "")}"')
                            return True
    except Exception as exc:
        log(f'ccmixter attempt failed: {exc}')
    return False


def detect_music_type(script):
    """Determine music type based on script keywords."""
    lower = script.lower()
    hype_words = {'crypto', 'shock', 'insane', 'crazy', 'unbelievable', 'scam', 'fraud',
                  'shocking', 'exposed', 'banned', 'forbidden', 'billion', 'trillion'}
    if any(w in lower for w in hype_words):
        return 'hype'
    suspense_words = {'warning', 'dangerous', 'hidden', 'secret', 'lie', 'lies', 'manipulation'}
    if any(w in lower for w in suspense_words):
        return 'suspense'
    return 'motivational'


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    audio_url       = os.environ.get('AUDIO_URL', '').strip()
    stock_urls_json = os.environ.get('STOCK_VIDEO_URLS', '[]').strip()
    title           = os.environ.get('TITLE', 'WealthShock').strip()
    script          = os.environ.get('SCRIPT', '').strip()
    freesound_key   = os.environ.get('FREESOUND_API_KEY', '').strip() or None

    if not audio_url:
        log('ERROR: AUDIO_URL is required')
        sys.exit(1)

    try:
        stock_urls = json.loads(stock_urls_json)
        if not isinstance(stock_urls, list) or not stock_urls:
            raise ValueError('empty list')
    except Exception:
        log('ERROR: STOCK_VIDEO_URLS must be a non-empty JSON array')
        sys.exit(1)

    # ── Prepare asset directories ─────────────────────────────────────────────
    os.makedirs(CLIPS_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)
    os.makedirs(SFX_DIR, exist_ok=True)

    # ── Step 1: Voiceover ─────────────────────────────────────────────────────
    voice_path = os.path.join(AUDIO_DIR, 'voice.mp3')
    log(f'Downloading voiceover ({audio_url[:60]}...)')
    if not stream_download(audio_url, voice_path):
        log('ERROR: Voiceover download failed')
        sys.exit(1)
    log(f'Voiceover: {os.path.getsize(voice_path) // 1024} KB')

    audio_dur = get_duration(voice_path) or 60.0
    log(f'Audio duration: {audio_dur:.2f}s')

    # ── Step 2: Background music ──────────────────────────────────────────────
    music_path = os.path.join(AUDIO_DIR, 'music.mp3')
    music_ok = False

    music_type = detect_music_type(script)
    log(f'Music type: {music_type}')

    # Try ccmixter first
    if download_music_ccmixter(music_path, music_type):
        music_ok = True
    else:
        # Fallback: Mixkit
        urls = MUSIC_URLS[:]
        random.shuffle(urls)
        for url in urls:
            if stream_download(url, music_path):
                log(f'Music: Mixkit fallback ({os.path.getsize(music_path) // 1024} KB)')
                music_ok = True
                break
        if not music_ok:
            log('Warning: all music downloads failed — proceeding without music')

    # ── Step 3: Stock clips in parallel ──────────────────────────────────────
    log(f'Downloading {len(stock_urls)} stock clips...')
    clip_args = [
        (url, os.path.join(CLIPS_DIR, f'clip_{i:02d}.mp4'))
        for i, url in enumerate(stock_urls)
    ]

    clip_files = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for result in pool.map(_download_clip, clip_args):
            if result:
                clip_files.append(os.path.basename(result))

    clip_files.sort()
    log(f'Downloaded {len(clip_files)}/{len(stock_urls)} clips')

    if not clip_files:
        log('ERROR: No clips downloaded')
        sys.exit(1)

    # ── Step 4: SFX ───────────────────────────────────────────────────────────
    log('Downloading SFX...')
    available_sfx = download_sfx(SFX_DIR, freesound_key=freesound_key)
    log(f'SFX available: {list(available_sfx.keys())}')

    # ── Step 5: Caption timing ────────────────────────────────────────────────
    captions = parse_captions(script, audio_dur) if script else []
    log(f'Captions: {len(captions)} words')

    # ── Step 6: Hook text ─────────────────────────────────────────────────────
    hook_text = detect_hook_text(script, title) if script else title[:60] or 'SHOCKING MONEY FACTS'
    log(f'Hook text: "{hook_text}"')

    # ── Step 7: Total frame count ─────────────────────────────────────────────
    # Cover audio duration with phase-aware pacing
    audio_frames = int(audio_dur * FPS)
    # Phase 1: HOOK_FRAMES (90)
    # Phase 2: remaining, using MAIN_CLIP_DUR + TRANSITION_DUR per unit
    unit = MAIN_CLIP_DUR + TRANSITION_DUR
    phase2_frames = audio_frames - HOOK_FRAMES
    if phase2_frames > 0:
        num_units = (phase2_frames // unit) + 4
        total_frames = HOOK_FRAMES + num_units * unit
    else:
        total_frames = HOOK_FRAMES + unit * 4
    total_frames = max(total_frames, audio_frames + FPS * 3)

    # ── Step 8: Cut frames → SFX events ──────────────────────────────────────
    cut_frames = calculate_cut_frames(total_frames)
    sfx_events = generate_sfx_events(captions, cut_frames, available_sfx)
    log(f'SFX events: {len(sfx_events)}')

    # ── Step 9: Stat charts ───────────────────────────────────────────────────
    stat_charts = detect_stat_charts(captions)
    log(f'Stat charts: {len(stat_charts)}')

    # ── Step 10: Write props ──────────────────────────────────────────────────
    props = {
        'audioFile': 'audio/voice.mp3',
        'musicFile': 'audio/music.mp3' if music_ok else '',
        'clipFiles': [f'clips/{f}' for f in clip_files],
        'captions': captions,
        'durationInFrames': total_frames,
        'hookText': hook_text,
        'sfxEvents': sfx_events,
        'statCharts': stat_charts,
    }
    with open(PROPS_FILE, 'w') as f:
        json.dump(props, f, indent=2)
    log(
        f'Props: {len(clip_files)} clips, {len(captions)} words, '
        f'{total_frames} frames, {len(sfx_events)} sfx, {len(stat_charts)} stats'
    )

    # ── Step 11: Remotion render ──────────────────────────────────────────────
    log('Starting Remotion render (node render.js)...')
    result = subprocess.run(
        ['node', 'render.js'],
        cwd=REMOTION_DIR,
        timeout=540,  # 9 min — leaves 1 min buffer in 10-min job
    )

    if result.returncode != 0:
        log(f'ERROR: Remotion render exited with code {result.returncode}')
        sys.exit(1)

    if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) < 1024:
        log('ERROR: final_video.mp4 not produced or empty')
        sys.exit(1)

    size_mb = os.path.getsize(OUTPUT_FILE) / 1024 / 1024
    log(f'SUCCESS: final_video.mp4 ({size_mb:.1f} MB)')

    # Cleanup staged assets (GHA workspace is ephemeral, but be tidy)
    for d in [CLIPS_DIR, AUDIO_DIR, SFX_DIR]:
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)


if __name__ == '__main__':
    main()
