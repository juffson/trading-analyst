"""滚动跑 Longbridge 的 `longbridge quant run` CLI，取「今天」新出现的信号。

为什么直接调 `longbridge` 二进制，不通过 ../../quant-backtest/scripts/run_script.py：

- 实测过 run_script.py 的「API 模式」（手写 HTTP 签名调 /v1/quant/run_script）稳定 401，
  官方 longport SDK（同款凭据在 quote/trade 上完全正常）里也根本没有这块能力——这条路径没打通。
- run_script.py 的「CLI 模式」响应解析假设了 `{code, data: {report_json, chart_json,
  events_json}}` 的包装结构，但实测 `longbridge quant run --format json` 返回的是**没有包装**的
  `{report_json, chart_json, events_json}`——那段解析代码 (`result.get("data", {})`) 会静默拿到
  空 dict，report/chart/events 全部变空，且不会报错。这是 quant-backtest skill 自身的 bug，
  按约定「skill不动」这里不改它，改成 auto-trader 自己直接调同一个 CLI 命令，用实测过的真实
  响应结构解析。
- `chart_json` 实测无论脚本有没有触发信号都恒为空字符串——不用它判断信号。改用
  `report_json.closedTrades` / `openTrades`，两者都带精确的 `entryTime`/`exitTime`
  （epoch 毫秒），比 chart_json.filled_orders 的 bar_index 更可靠，也不需要额外做
  bar_index → 日期的映射。
"""
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

_FALLBACK_BINARY = str(Path.home() / ".local" / "bin" / "longbridge")


class BacktestError(RuntimeError):
    pass


def _resolve_binary():
    found = shutil.which("longbridge")
    if found:
        return found
    if Path(_FALLBACK_BINARY).exists():
        return _FALLBACK_BINARY
    raise BacktestError(
        "找不到 longbridge CLI——需要先装好 https://github.com/longbridge/longbridge-terminal "
        "并确保 `longbridge` 在 PATH 里（cron/launchd 的 PATH 可能和交互 shell 不一样，"
        f"装完建议确认一下绝对路径，当前 fallback 找的是 {_FALLBACK_BINARY}）"
    )


def run_rolling_backtest(script_path, symbol, lookback_days=90, period="day", inputs=None, end_date=None):
    """跑 [end_date - lookback_days, end_date] 区间的回测（默认 end_date = 今天 UTC）。

    返回 (report, events, end_date)：report/events 是 report_json/events_json 解析后的
    dict/list。chart_json 目前恒为空，不解析。
    """
    binary = _resolve_binary()
    end = end_date or datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    script = Path(script_path).read_text(encoding="utf-8")

    cmd = [
        binary, "quant", "run", symbol,
        "--start", start.isoformat(),
        "--end", end.isoformat(),
        "--period", period,
        "--script", script,
        "--format", "json",
    ]
    if inputs:
        cmd += ["--input", inputs]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise BacktestError(f"longbridge quant run 失败: {proc.stderr.strip()}")

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise BacktestError(f"响应不是合法 JSON: {exc}；原始输出: {proc.stdout[:500]}") from exc

    report = json.loads(result["report_json"]) if result.get("report_json") else {}
    events = json.loads(result["events_json"]) if result.get("events_json") else []
    return report, events, end


def latest_signals(report, today):
    """从 report_json 里找「今天」新出现的信号。

    - closedTrades 的 entryTime 落在今天 → 一笔开仓信号（不管这笔交易是不是当天就平仓了）
    - closedTrades 的 exitTime 落在今天 → 一笔平仓信号
    - openTrades（还没平仓的持仓）的 entryTime 落在今天 → 一笔开仓信号

    今天的判定用 UTC 自然日 [00:00, 24:00) 卡 entryTime/exitTime（epoch 毫秒）。

    返回: [{"side": "buy"|"sell", "qty": float, "price": float, "time_ms": int}, ...]
    today: date 对象（一般是 run_rolling_backtest 返回的 end_date）
    """
    day_start_ms = int(datetime(today.year, today.month, today.day, tzinfo=timezone.utc).timestamp() * 1000)
    day_end_ms = day_start_ms + 24 * 3600 * 1000

    def in_today(ts_ms):
        return ts_ms is not None and day_start_ms <= ts_ms < day_end_ms

    signals = []
    for trade in report.get("closedTrades", []):
        entry_side = trade.get("entrySide")
        if in_today(trade.get("entryTime")):
            signals.append({
                "side": "buy" if entry_side == "Long" else "sell",
                "qty": trade.get("quantity"), "price": trade.get("entryPrice"), "time_ms": trade.get("entryTime"),
            })
        if in_today(trade.get("exitTime")):
            signals.append({
                "side": "sell" if entry_side == "Long" else "buy",
                "qty": trade.get("quantity"), "price": trade.get("exitPrice"), "time_ms": trade.get("exitTime"),
            })

    for trade in report.get("openTrades", []):
        if in_today(trade.get("entryTime")):
            signals.append({
                "side": "buy" if trade.get("entrySide") == "Long" else "sell",
                "qty": trade.get("quantity"), "price": trade.get("entryPrice"), "time_ms": trade.get("entryTime"),
            })

    return signals
