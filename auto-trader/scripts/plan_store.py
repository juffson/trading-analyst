"""交易计划（Trading Plan）存储层。

数据模型参考 ../../signal-hub 的 Signal 结构（id / status / symbol / strategy_name /
created_at·updated_at / summary 等字段和 pending→active 的状态语义），但不引入它的
Kafka/gRPC/状态机基础设施——这里只是本地 JSONL 文件，一个标的一份
watchlist/<symbol>/plans.jsonl，追加写入，不做原地修改。

状态语义（对应 signal-hub 的 pending/active/filter_by_manual，改成交易场景）:
  pending   — 已生成，等待执行。config.execution_mode == "signal_only" 时永远停在这一步
  rejected  — risk_guard 拦下，不会执行（对应 signal-hub 的 filter_by_manual）
  executed  — 已调用 executor 下单（目前 executor 只会做 --dry-run 预览，不是真实成交）
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

WATCHLIST_DIR = Path(__file__).resolve().parent.parent / "watchlist"


def _plans_path(symbol):
    symbol_dir = WATCHLIST_DIR / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)
    return symbol_dir / "plans.jsonl"


def _append(symbol, event_type, plan):
    record = {"event": event_type, "plan": plan}
    with _plans_path(symbol).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def create_plan(symbol, strategy_name, side, qty, price, signal_key, analysis_price=None, summary=None):
    """生成一份 pending 状态的交易计划并追加记录，返回 plan dict。"""
    now = datetime.now(timezone.utc).isoformat()
    plan = {
        "id": f"plan_{symbol}_{int(time.time() * 1000)}",
        "symbol": symbol,
        "strategy_name": strategy_name,
        "side": side,
        "qty": qty,
        "price": price,
        "analysis_price": analysis_price,
        "signal_key": signal_key,
        "status": "pending",
        "summary": summary or f"{strategy_name} 触发 {side} 信号：qty={qty} price={price}",
        "created_at": now,
        "updated_at": now,
    }
    _append(symbol, "plan_created", plan)
    return plan


def update_status(symbol, plan, new_status, note=None):
    """在原 plan 基础上追加一条状态变更记录（不修改历史行，只追加新的一行）。"""
    updated = dict(plan)
    updated["status"] = new_status
    updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    if note:
        updated["note"] = note
    _append(symbol, "plan_status_changed", updated)
    return updated


def load_latest_plans(symbol):
    """按 plan id 去重，只保留每个 plan 最新的一条记录（供复盘/审阅时读取当前状态）。"""
    path = _plans_path(symbol)
    if not path.exists():
        return {}
    latest = {}
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            plan = entry.get("plan")
            if plan and plan.get("id"):
                latest[plan["id"]] = plan
    return latest
