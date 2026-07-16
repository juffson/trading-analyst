"""本轮运行的结构化日志。

当前占位实现：把每个事件追加写到 logs/YYYY-MM-DD.jsonl。
后续计划：替换成直接调 Claude API，把当天的 signal/order/risk_block/error 记录
整理成人类可读的播报或异常解释，再推送出去（Lark/push notification 等）——
不再经过 headless `claude -p`，那部分接口稳定后再接。
"""
import json
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def emit(symbol, event_type, payload):
    """event_type: 'signal' | 'order' | 'risk_block' | 'error'"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "type": event_type,
        "payload": payload,
    }
    log_file = LOG_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
