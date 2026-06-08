"""
WealthShock Analytics Engine — self-learning performance optimizer.

Persists performance_db.json to the GitHub repo (via Contents API) so data
survives Render worker restarts. Falls back to local file if GH credentials
are missing.
"""

import base64
import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

# ─── Topic and style classifiers ─────────────────────────────────────────────

TOPIC_CATEGORIES = {
    "crypto":        ["crypto", "bitcoin", "blockchain", "defi", "ethereum", "nft", "altcoin"],
    "investing":     ["invest", "stock", "market", "trading", "nasdaq", "s&p", "etf", "portfolio", "dividend"],
    "wealth":        ["rich", "wealth", "millionaire", "billionaire", "passive income", "luxury"],
    "money_habits":  ["budget", "save", "saving", "spending", "habit", "frugal", "debt", "credit"],
    "ai_finance":    ["ai", "artificial intelligence", "automation", "chatgpt", "openai"],
    "side_hustles":  ["hustle", "side", "freelance", "business", "entrepreneur", "startup"],
}


def categorize_topic(topic: str) -> str:
    lower = topic.lower()
    for category, keywords in TOPIC_CATEGORIES.items():
        if any(kw in lower for kw in keywords):
            return category
    return "money_general"


def classify_title_style(title: str) -> str:
    t = title.lower()
    if "?" in title:
        return "question"
    if re.search(r'\b\d+\b', title) and any(w in t for w in ["top", "ways", "things", "reasons", "steps", "facts"]):
        return "number_list"
    if any(w in t for w in ["secret", "truth", "hidden", "nobody", "they don't", "real reason"]):
        return "secret_reveal"
    if any(w in t for w in ["shocking", "insane", "crazy", "unbelievable", "mind-blowing"]):
        return "shocking_statement"
    return "statement"


def duration_to_bucket(seconds: float) -> str:
    if seconds <= 45:
        return "30-45"
    if seconds <= 62:
        return "45-60"
    return "60-75"


# ─── Default DB structure ─────────────────────────────────────────────────────

_DEFAULT = {
    "videos": [],
    "topic_performance": {},
    "timing_performance": {},
    "length_performance": {},
    "title_pattern_performance": {},
    "alerts_triggered": [],
    "failed_strategies": [],
    "weekly_reports": [],
    "last_analysis": None,
    "learnings": {},
}


def _gh_headers(gh_pat: str) -> dict:
    return {
        "Authorization": f"Bearer {gh_pat}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


class PerformanceDB:
    """
    Analytics store — reads/writes performance_db.json both locally and to the
    GitHub repo so the data survives Render's ephemeral filesystem.
    """

    def __init__(self, local_path: str, gh_pat: str = None, gh_repo: str = None):
        self.local_path = local_path
        self.gh_pat = gh_pat
        self.gh_repo = gh_repo
        self.data = self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> dict:
        # 1. Try GitHub first (source of truth across restarts)
        remote = self._load_from_github()
        if remote:
            # Also write locally so we have a local cache
            self._write_local(remote)
            return remote
        # 2. Fallback to local file
        return self._load_local()

    def _load_local(self) -> dict:
        if os.path.exists(self.local_path):
            try:
                with open(self.local_path, encoding="utf-8") as f:
                    d = json.load(f)
                    for k, v in _DEFAULT.items():
                        d.setdefault(k, type(v)() if isinstance(v, (list, dict)) else v)
                    return d
            except Exception:
                pass
        import copy
        return copy.deepcopy(_DEFAULT)

    def _write_local(self, data: dict):
        try:
            with open(self.local_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _load_from_github(self) -> dict | None:
        if not self.gh_pat or not self.gh_repo:
            return None
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{self.gh_repo}/contents/performance_db.json",
                headers=_gh_headers(self.gh_pat),
                timeout=20,
            )
            if resp.status_code == 200:
                content = base64.b64decode(resp.json()["content"]).decode("utf-8")
                d = json.loads(content)
                for k, v in _DEFAULT.items():
                    d.setdefault(k, type(v)() if isinstance(v, (list, dict)) else v)
                return d
        except Exception:
            pass
        return None

    def save(self):
        """Persist locally + push to GitHub (with [skip ci] to avoid deploy loops)."""
        self._write_local(self.data)
        self._save_to_github()

    def _save_to_github(self):
        if not self.gh_pat or not self.gh_repo:
            return
        try:
            content_b64 = base64.b64encode(
                json.dumps(self.data, indent=2, ensure_ascii=False).encode("utf-8")
            ).decode("ascii")

            # Get current SHA so we can update (not create)
            sha = None
            resp = requests.get(
                f"https://api.github.com/repos/{self.gh_repo}/contents/performance_db.json",
                headers=_gh_headers(self.gh_pat),
                timeout=20,
            )
            if resp.status_code == 200:
                sha = resp.json().get("sha")

            payload = {
                "message": "chore: update analytics db [skip ci]",
                "content": content_b64,
                "branch": "main",
            }
            if sha:
                payload["sha"] = sha

            requests.put(
                f"https://api.github.com/repos/{self.gh_repo}/contents/performance_db.json",
                headers=_gh_headers(self.gh_pat),
                json=payload,
                timeout=30,
            )
        except Exception:
            pass

    # ── Record upload ─────────────────────────────────────────────────────────

    def record_upload(
        self,
        video_id: str,
        title: str,
        topic: str,
        estimated_duration: float,
        ab_variant: str = "A",
    ):
        now = datetime.now(timezone.utc)
        entry = {
            "video_id": video_id,
            "title": title,
            "topic": topic,
            "topic_category": categorize_topic(topic),
            "upload_time": now.isoformat(),
            "day_of_week": now.strftime("%A"),
            "upload_hour_utc": now.hour,
            "timing_key": f"{now.strftime('%A')}_{now.hour}",
            "estimated_duration": estimated_duration,
            "duration_bucket": duration_to_bucket(estimated_duration),
            "title_style": classify_title_style(title),
            "ab_variant": ab_variant,
            # Metrics fetched later
            "metrics_24h_fetched": False,
            "metrics_72h_fetched": False,
            "views_24h": None,
            "views_72h": None,
            "likes_24h": None,
            "avg_view_percentage": None,
            "ctr": None,
            "alert_checked": False,
        }
        self.data["videos"].append(entry)
        self.save()
        return entry

    # ── Update metrics ────────────────────────────────────────────────────────

    def update_video_metrics(self, video_id: str, metrics: dict, checkpoint: str):
        """
        checkpoint: '24h' or '72h'
        metrics: {'views', 'likes', 'comments', 'avg_view_percentage', 'ctr'}
        """
        for v in self.data["videos"]:
            if v.get("video_id") != video_id:
                continue
            v[f"views_{checkpoint}"] = metrics.get("views", 0)
            v[f"likes_{checkpoint}"] = metrics.get("likes", 0)
            if checkpoint == "24h":
                v["avg_view_percentage"] = metrics.get("avg_view_percentage")
                v["ctr"] = metrics.get("ctr")
                v["metrics_24h_fetched"] = True
                # Update all aggregates after first real data point
                self._rebuild_aggregates()
            else:
                v["metrics_72h_fetched"] = True
                self._rebuild_aggregates()
            self.save()
            return

    def _rebuild_aggregates(self):
        """Recompute all performance tables from scratch."""
        tp, timing, length, title = {}, {}, {}, {}
        for v in self.data["videos"]:
            if v.get("views_24h") is None:
                continue
            views = v.get("views_24h", 0) or 0
            ret = v.get("avg_view_percentage") or 0
            ctr = v.get("ctr") or 0

            cat = v.get("topic_category", "money_general")
            tp.setdefault(cat, {"total": 0, "count": 0})
            tp[cat]["total"] += views
            tp[cat]["count"] += 1

            tkey = v.get("timing_key", "")
            if tkey:
                timing.setdefault(tkey, {"total": 0, "count": 0})
                timing[tkey]["total"] += views
                timing[tkey]["count"] += 1

            bucket = v.get("duration_bucket", "45-60")
            length.setdefault(bucket, {"total_views": 0, "total_ret": 0, "count": 0})
            length[bucket]["total_views"] += views
            length[bucket]["total_ret"] += ret
            length[bucket]["count"] += 1

            style = v.get("title_style", "statement")
            title.setdefault(style, {"total_views": 0, "total_ctr": 0, "count": 0})
            title[style]["total_views"] += views
            title[style]["total_ctr"] += ctr
            title[style]["count"] += 1

        for k, d in tp.items():
            d["avg_views_24h"] = d["total"] / d["count"] if d["count"] else 0
        for k, d in timing.items():
            d["avg_views"] = d["total"] / d["count"] if d["count"] else 0
        for k, d in length.items():
            d["avg_views"] = d["total_views"] / d["count"] if d["count"] else 0
            d["avg_retention"] = d["total_ret"] / d["count"] if d["count"] else 0
        for k, d in title.items():
            d["avg_views"] = d["total_views"] / d["count"] if d["count"] else 0
            d["avg_ctr"] = d["total_ctr"] / d["count"] if d["count"] else 0

        self.data["topic_performance"] = tp
        self.data["timing_performance"] = timing
        self.data["length_performance"] = length
        self.data["title_pattern_performance"] = title

    # ── Strategy getters ──────────────────────────────────────────────────────

    def get_topic_weights(self) -> dict:
        """Return {category: weight} for weighted random topic selection. Empty = use defaults."""
        tp = self.data["topic_performance"]
        return {
            cat: max(1.0, d.get("avg_views_24h", 0))
            for cat, d in tp.items()
            if d.get("count", 0) >= 2
        }

    def get_optimal_schedule(self) -> list | None:
        """Return top-4 upload slots by avg views, or None if < 3 qualified slots."""
        timing = self.data["timing_performance"]
        qualified = {k: v for k, v in timing.items() if v.get("count", 0) >= 2}
        if len(qualified) < 3:
            return None
        top4 = sorted(qualified.items(), key=lambda x: x[1]["avg_views"], reverse=True)[:4]
        result = []
        for key, stats in top4:
            parts = key.split("_")
            if len(parts) == 2:
                day, hour = parts
                result.append({"day": day, "utc": f"{int(hour):02d}:00", "score": stats["avg_views"]})
        return result or None

    def get_optimal_script_length(self) -> int:
        """Return optimal target video duration in seconds (30–75)."""
        lp = self.data["length_performance"]
        qualified = {k: v for k, v in lp.items() if v.get("count", 0) >= 2}
        if not qualified:
            return 60
        # Score = avg_retention × log(avg_views + 1) — balances completion vs reach
        best = max(
            qualified.items(),
            key=lambda x: x[1].get("avg_retention", 0) * math.log(x[1].get("avg_views", 1) + 1),
        )
        return {"30-45": 38, "45-60": 52, "60-75": 67}.get(best[0], 60)

    def get_best_title_style(self) -> str | None:
        """Return best-performing title style (need ≥ 3 data points), or None."""
        tp = self.data["title_pattern_performance"]
        qualified = {k: v for k, v in tp.items() if v.get("count", 0) >= 3}
        if not qualified:
            return None
        best = max(qualified.items(), key=lambda x: x[1].get("avg_views", 0))
        return best[0]

    def get_engagement_trend(self) -> str:
        """Return 'rising', 'stable', or 'falling' based on last 6 videos."""
        videos = sorted(
            [v for v in self.data["videos"] if v.get("views_24h") is not None],
            key=lambda v: v["upload_time"],
        )
        if len(videos) < 6:
            return "stable"
        recent_avg = sum(v.get("views_24h", 0) for v in videos[-3:]) / 3
        older_avg = sum(v.get("views_24h", 0) for v in videos[-6:-3]) / 3
        if older_avg == 0:
            return "stable"
        ratio = recent_avg / older_avg
        if ratio > 1.2:
            return "rising"
        if ratio < 0.8:
            return "falling"
        return "stable"

    def get_winning_topic_for_ab(self) -> str | None:
        """Return the single best-performing topic category across all A/B tests."""
        tp = self.data["topic_performance"]
        qualified = {k: v for k, v in tp.items() if v.get("count", 0) >= 4}
        if not qualified:
            return None
        return max(qualified.items(), key=lambda x: x[1].get("avg_views_24h", 0))[0]

    # ── Pending metrics queries ───────────────────────────────────────────────

    def get_videos_needing_metrics(self, checkpoint_hours: int) -> list:
        key = "metrics_24h_fetched" if checkpoint_hours == 24 else "metrics_72h_fetched"
        now = datetime.now(timezone.utc)
        result = []
        for v in self.data["videos"]:
            if v.get(key):
                continue
            vid_id = v.get("video_id", "")
            if not vid_id or vid_id in ("None", "null", ""):
                continue
            try:
                age_h = (now - datetime.fromisoformat(v["upload_time"])).total_seconds() / 3600
                # Accept videos in a window around the checkpoint
                if checkpoint_hours - 2 <= age_h <= checkpoint_hours + 14:
                    result.append(v)
            except Exception:
                pass
        return result

    def get_videos_needing_alert_check(self) -> list:
        return [
            v for v in self.data["videos"]
            if not v.get("alert_checked") and v.get("views_24h") is not None
        ]

    def mark_alert_checked(self, video_id: str):
        for v in self.data["videos"]:
            if v.get("video_id") == video_id:
                v["alert_checked"] = True
        self.save()

    def record_alert(self, alert_type: str, video_id: str, details: dict):
        self.data["alerts_triggered"].append({
            "type": alert_type,
            "video_id": video_id,
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        self.save()

    # ── Weekly report ─────────────────────────────────────────────────────────

    def generate_weekly_report(self) -> dict:
        videos = [v for v in self.data["videos"] if v.get("views_24h") is not None]
        if not videos:
            return {"status": "no_data"}

        total = sum(v.get("views_24h", 0) for v in videos)
        avg = total / len(videos)
        best = max(videos, key=lambda v: v.get("views_24h", 0))

        def top(table, metric):
            d = self.data.get(table, {})
            q = {k: v for k, v in d.items() if v.get("count", 0) >= 2}
            return max(q.items(), key=lambda x: x[1].get(metric, 0))[0] if q else None

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_videos": len(videos),
            "total_views_24h": total,
            "avg_views_per_video": round(avg, 1),
            "best_video": {
                "id": best.get("video_id"),
                "title": best.get("title"),
                "views_24h": best.get("views_24h"),
                "topic": best.get("topic"),
            },
            "top_topic": top("topic_performance", "avg_views_24h"),
            "best_upload_slot": top("timing_performance", "avg_views"),
            "optimal_length_s": self.get_optimal_script_length(),
            "best_title_style": self.get_best_title_style(),
            "engagement_trend": self.get_engagement_trend(),
        }

        self.data["weekly_reports"].append(report)
        self.data["last_analysis"] = datetime.now(timezone.utc).isoformat()
        self.data["learnings"] = {
            "optimal_script_length": self.get_optimal_script_length(),
            "best_title_style": self.get_best_title_style(),
            "engagement_trend": self.get_engagement_trend(),
            "data_points": len(videos),
        }
        self.save()
        return report
