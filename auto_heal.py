#!/usr/bin/env python3
import json
import os
import re
import subprocess
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

RENDER_API_KEY = os.environ.get("RENDER_API_KEY")
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
BASE_BRANCH = os.environ.get("BASE_BRANCH", "main")

KNOWN_REQUIREMENT_FIXES = {
    "No module named 'google.cloud'": "google-cloud-texttospeech",
    "No module named 'google.cloud.texttospeech'": "google-cloud-texttospeech",
    "No module named 'pytz'": "pytz",
    "No module named 'PIL'": "Pillow",
    "No module named 'imageio_ffmpeg'": "imageio-ffmpeg",
    "No module named 'schedule'": "schedule",
    "No module named 'dotenv'": "python-dotenv",
}

ENV_PLACEHOLDER_MAP = {
    "Missing Google Cloud TTS API key": ["GOOGLE_TTS_API_KEY"],
    "Missing YouTube OAuth environment variables": ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"],
    "Missing YouTube API key for trending topics": ["YOUTUBE_API_KEY"],
    "Missing Gemini API key": ["GEMINI_API_KEY"],
    "Missing Pixabay API key": ["PIXABAY_API_KEY"],
    "Missing Render API" : ["RENDER_API_KEY"],
}

LOG_FILTER_KEYWORDS = [
    "error",
    "failed",
    "exception",
    "traceback",
    "missing",
    "unable",
    "cannot",
    "build",
    "deployment",
]


def log(message):
    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%SZ')}] {message}", flush=True)


def safe_request(method, url, **kwargs):
    headers = kwargs.pop("headers", {}) or {}
    if RENDER_API_KEY:
        headers["Authorization"] = f"Bearer {RENDER_API_KEY}"
    headers["Accept"] = "application/json"
    return requests.request(method, url, headers=headers, timeout=30, **kwargs)


def get_service_info():
    if not RENDER_SERVICE_ID:
        return None
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}"
    resp = safe_request("get", url)
    if resp.status_code != 200:
        log(f"Failed to fetch Render service info: {resp.status_code} {resp.text}")
        return None
    return resp.json()


def get_recent_events(minutes=15, limit=50):
    if not RENDER_SERVICE_ID:
        return []
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/events"
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(minutes=minutes)
    params = {
        "startTime": start_time.isoformat() + "Z",
        "endTime": end_time.isoformat() + "Z",
        "limit": limit,
    }
    resp = safe_request("get", url, params=params)
    if resp.status_code != 200:
        log(f"Failed to fetch Render events: {resp.status_code} {resp.text}")
        return []
    return resp.json() or []


def extract_text(item):
    if isinstance(item, str):
        return item
    if not isinstance(item, dict):
        return str(item)
    pieces = []
    for key in ["message", "summary", "description", "title", "details", "name"]:
        value = item.get(key)
        if value:
            pieces.append(str(value))
    if not pieces:
        pieces.append(json.dumps(item, ensure_ascii=False))
    return " ".join(pieces)


def filter_events(events):
    messages = []
    for event in events:
        text = extract_text(event)
        if any(keyword in text.lower() for keyword in LOG_FILTER_KEYWORDS):
            messages.append(text)
    return messages


def load_requirements():
    if not os.path.exists("requirements.txt"):
        return []
    with open("requirements.txt", "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.strip().startswith("#")]


def write_requirements(requirements):
    with open("requirements.txt", "w", encoding="utf-8") as handle:
        handle.write("\n".join(requirements).strip() + "\n")


def ensure_env_example(keys):
    path = ".env.example"
    existing = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            existing = [line.rstrip("\n") for line in handle]
    else:
        existing = ["# Example environment variables for WealthShock"]

    changed = False
    for key in keys:
        if not any(line.startswith(f"{key}=") for line in existing):
            existing.append(f"{key}=")
            changed = True
    if changed:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(existing).strip() + "\n")
    return changed


def run_command(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout.strip()


def create_branch(branch_name):
    run_command(["git", "config", "user.name", "github-actions[bot]"])
    run_command(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    run_command(["git", "fetch", "origin", BASE_BRANCH])
    run_command(["git", "checkout", "-B", branch_name])


def commit_changes(message):
    run_command(["git", "add", "requirements.txt", ".env.example"])
    run_command(["git", "commit", "-m", message])


def push_branch(branch_name):
    run_command(["git", "push", "-u", "origin", branch_name, "--force"])


def github_headers():
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def find_open_pr(branch_name):
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return None
    owner_repo = GITHUB_REPOSITORY
    url = f"https://api.github.com/repos/{owner_repo}/pulls"
    params = {"head": f"{owner_repo.split('/')[0]}:{branch_name}", "state": "open"}
    r = requests.get(url, headers=github_headers(), params=params, timeout=20)
    if r.status_code != 200:
        return None
    pulls = r.json()
    if pulls:
        return pulls[0]
    return None


def create_pull_request(branch_name, title, body):
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        log("GitHub token or repository missing, cannot create PR")
        return None
    existing = find_open_pr(branch_name)
    if existing:
        log(f"Found existing PR #{existing.get('number')} for {branch_name}")
        return existing
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/pulls"
    payload = {"title": title, "head": branch_name, "base": BASE_BRANCH, "body": body}
    r = requests.post(url, headers=github_headers(), json=payload, timeout=20)
    if r.status_code not in (200, 201):
        log(f"Failed to create PR: {r.status_code} {r.text}")
        return None
    return r.json()


def merge_pull_request(pr_number):
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        log("GitHub token or repository missing, cannot merge PR")
        return False
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/pulls/{pr_number}/merge"
    payload = {"merge_method": "squash"}
    r = requests.put(url, headers=github_headers(), json=payload, timeout=20)
    if r.status_code not in (200, 201):
        log(f"Failed to merge PR #{pr_number}: {r.status_code} {r.text}")
        return False
    log(f"Merged PR #{pr_number}")
    return True


def trigger_deploy(clear_cache=False):
    if not RENDER_SERVICE_ID:
        log("Render service ID missing, cannot trigger deploy")
        return False
    url = f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/deploys"
    payload = {"clearCache": "clear" if clear_cache else "do_not_clear"}
    resp = safe_request("post", url, json=payload)
    if resp.status_code not in (200, 201):
        log(f"Render deploy request failed: {resp.status_code} {resp.text}")
        return False
    log("Render deploy triggered successfully")
    return True


def plan_fixes(messages):
    requirements = load_requirements()
    new_requirements = set()
    missing_env_keys = set()
    for msg in messages:
        normalized = msg.replace("\n", " ")
        for pattern, requirement in KNOWN_REQUIREMENT_FIXES.items():
            if pattern in normalized:
                if requirement not in requirements:
                    new_requirements.add(requirement)
        for pattern, keys in ENV_PLACEHOLDER_MAP.items():
            if pattern in normalized:
                missing_env_keys.update(keys)
    return sorted(new_requirements), sorted(missing_env_keys)


def apply_fixes(messages):
    new_requirements, missing_env_keys = plan_fixes(messages)
    modified = False
    if new_requirements:
        requirements = load_requirements()
        for requirement in new_requirements:
            if requirement not in requirements:
                requirements.append(requirement)
                log(f"Adding missing requirement: {requirement}")
        write_requirements(requirements)
        modified = True
    if missing_env_keys:
        if ensure_env_example(missing_env_keys):
            log(f"Added env placeholders for: {', '.join(missing_env_keys)}")
            modified = True
    return modified, new_requirements, missing_env_keys


def main():
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        log("Render self-heal requires RENDER_API_KEY and RENDER_SERVICE_ID")
        return
    service_info = get_service_info()
    if not service_info:
        return
    events = get_recent_events(minutes=10, limit=80)
    messages = filter_events(events)
    if not messages:
        log("No recent Render issues found in service events")
        return

    log(f"Found {len(messages)} recent Render event messages")
    for entry in messages[:5]:
        log(entry)

    modified, new_requirements, missing_env_keys = apply_fixes(messages)
    if not modified:
        log("No auto-fixable issues detected from Render events")
        return

    branch_name = f"self-heal/render-fix-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    create_branch(branch_name)
    commit_message = "self-heal: fix Render deployment issues"
    commit_changes(commit_message)
    push_branch(branch_name)

    title = "self-heal: fix Render service deployment issues"
    body_lines = ["This automated PR includes fixes for recent Render issues detected in service events."]
    if new_requirements:
        body_lines.append(f"- Added missing dependencies: {', '.join(new_requirements)}")
    if missing_env_keys:
        body_lines.append(f"- Added environment placeholders: {', '.join(missing_env_keys)}")
    body = "\n".join(body_lines)
    pr = create_pull_request(branch_name, title, body)
    if not pr:
        log("Could not create PR after applying auto-fixes")
        return
    pr_number = pr.get("number")
    if not pr_number:
        log("PR created but could not determine PR number")
        return

    if merge_pull_request(pr_number):
        trigger_deploy(clear_cache=False)


if __name__ == "__main__":
    main()
