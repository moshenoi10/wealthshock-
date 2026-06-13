import json
from collections import deque
from datetime import datetime, timezone
from typing import List

import config

MAX_IN_MEMORY = 500


class TradeLogger:
    def __init__(self):
        self._trades: deque = deque(maxlen=MAX_IN_MEMORY)
        self._load()

    def _load(self):
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        if config.TRADES_FILE.exists():
            try:
                data = json.loads(config.TRADES_FILE.read_text())
                for t in data[-MAX_IN_MEMORY:]:
                    self._trades.append(t)
            except Exception:
                pass

    def log_trade(self, trade: dict):
        trade.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
        self._trades.append(trade)
        self._flush()
        self._console(trade)

    def _flush(self):
        try:
            config.TRADES_FILE.write_text(json.dumps(list(self._trades), indent=2))
        except Exception as exc:
            print(f"[LOGGER] Write error: {exc}")

    def _console(self, t: dict):
        pnl_str = f" PnL=${t['pnl']:.4f}" if t.get("pnl") is not None else ""
        print(
            f"[TRADE] {t.get('strategy','?'):<20} | "
            f"{t.get('side','?')} {t.get('size',0):.2f} @ {t.get('price',0):.4f}"
            f"{pnl_str} | {t.get('market_question','')[:50]}"
        )

    def get_recent_trades(self, limit: int = 50) -> List[dict]:
        return list(self._trades)[-limit:]

    def daily_summary(self) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        today_trades = [t for t in self._trades if (t.get("logged_at") or "").startswith(today)]
        closed = [t for t in today_trades if t.get("pnl") is not None]
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        wins = [t for t in closed if t.get("pnl", 0) > 0]

        by_strategy: dict = {}
        for t in closed:
            s = t.get("strategy", "unknown")
            e = by_strategy.setdefault(s, {"pnl": 0.0, "count": 0, "wins": 0})
            e["pnl"] += t.get("pnl", 0)
            e["count"] += 1
            if t.get("pnl", 0) > 0:
                e["wins"] += 1

        return {
            "date": today,
            "total_trades": len(today_trades),
            "closed_trades": len(closed),
            "total_pnl": round(total_pnl, 4),
            "win_rate": round(len(wins) / len(closed), 4) if closed else 0,
            "by_strategy": by_strategy,
        }
