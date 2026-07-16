"""本地 JSON 状态存储：持仓快照、已执行信号去重、当日下单计数。

不提交到 git（见 ../.gitignore）——纯运行时数据，一个标的一个文件：state/<SYMBOL>.json。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"


def _path(symbol):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / f"{symbol}.json"


def load(symbol):
    path = _path(symbol)
    if not path.exists():
        return {
            "symbol": symbol,
            "positions": {"shares": 0, "cost_basis": 0.0},
            "executed_signals": [],
            # realized_pnl_pct 需要接实时报价做 mark-to-market 才能算准，
            # 目前骨架里不会自动填——见 ../README.md「已知局限」
            "daily": {"date": None, "orders_count": 0, "realized_pnl_pct": 0.0},
            "updated_at": None,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save(symbol, state):
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    _path(symbol).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def reset_daily_if_new_day(state, today):
    """today: 'YYYY-MM-DD' 字符串"""
    if state.get("daily", {}).get("date") != today:
        state["daily"] = {"date": today, "orders_count": 0, "realized_pnl_pct": 0.0}
    return state


def is_signal_executed(state, signal_key):
    return signal_key in state.get("executed_signals", [])


def mark_signal_executed(state, signal_key, keep_last=200):
    signals = state.setdefault("executed_signals", [])
    signals.append(signal_key)
    # 只保留最近 N 条，避免文件无限增长
    state["executed_signals"] = signals[-keep_last:]
