"""滚动跑 Longbridge 的 `/v1/quant/run_script`，取「今天」新出现的信号。

用 `longport` SDK 的 `HttpClient` 直接发请求，不依赖外部 `longbridge` CLI 二进制：

- `longport` 官方 Python SDK 没有专门的 quant/backtest 方法，但暴露了一个通用的
  `HttpClient`（`HttpClient.from_apikey_env()`），可以对 OpenAPI 后端发任意认证请求——
  直接 POST `/v1/quant/run_script`，鉴权走 SDK 自己内部的实现（和 quote/trade 共用，已验证
  有效），不用再单独安装 + 配置 PATH 的 longbridge-terminal 二进制（早期版本就是这样做的，
  见 git 历史；这个版本改用纯 Python，部署更简单）。
- 请求体字段名（`counter_id`/`start_time`/`end_time`/`script`/`inputs_json`/`line_type`/
  `exclude_chart`）来自 longbridge-terminal 的 PR #118 源码；`counter_id`/`line_type` 的转换
  逻辑和 `../../quant-backtest/scripts/run_script.py` 里写的一致（保持同样的映射，但不做
  跨目录 import，避免和那个 skill 产生代码耦合）。
- `exclude_chart` 必须显式传 `False`——不传时 `chart_json` 会是空字符串；传 `False` 后
  `chart_json.filledOrders`（注意是驼峰，不是 SKILL.md 文档里写的 `filled_orders`）才会有
  数据，已实测确认。不过信号判断仍然优先用 `report_json.closedTrades`/`openTrades`——
  它们带精确的 `entryTime`/`exitTime`（epoch 毫秒），比 `filledOrders` 的 bar_index 更直接，
  不需要额外做 bar_index → 日期的映射。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from longport.openapi import HttpClient

_MARKET_MAP = {"US": "US", "HK": "HK", "SH": "SH", "SZ": "SZ", "SG": "SG"}
_PERIOD_TO_LINE_TYPE = {
    "day": 1000, "week": 2000, "month": 3000, "year": 4000,
    "1h": 60, "30m": 30, "15m": 15, "5m": 5, "1m": 1,
}


class BacktestError(RuntimeError):
    pass


def _to_counter_id(symbol):
    """TSLA.US → ST/US/TSLA"""
    if symbol.startswith("ST/"):
        return symbol
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2:
        raise BacktestError(f"无法解析符号格式: {symbol}，请用 TSLA.US / 700.HK / 600519.SH")
    code, market = parts
    market = market.upper()
    if market not in _MARKET_MAP:
        raise BacktestError(f"不支持的市场: {market}")
    return f"ST/{_MARKET_MAP[market]}/{code}"


def _to_line_type(period):
    lt = _PERIOD_TO_LINE_TYPE.get(period)
    if lt is None:
        raise BacktestError(f"不支持的 period: {period}，可用值: {sorted(_PERIOD_TO_LINE_TYPE)}")
    return lt


def run_rolling_backtest(script_path, symbol, lookback_days=90, period="day", inputs=None, end_date=None):
    """跑 [end_date - lookback_days, end_date] 区间的回测（默认 end_date = 今天 UTC）。

    返回 (report, events, end_date)：report/events 是 report_json/events_json 解析后的
    dict/list。
    """
    end = end_date or datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)
    script = Path(script_path).read_text(encoding="utf-8")

    start_ts = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    end_ts = int(datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc).timestamp())

    body = {
        "counter_id": _to_counter_id(symbol),
        "start_time": start_ts,
        "end_time": end_ts,
        "script": script,
        "inputs_json": inputs or "[]",
        "line_type": _to_line_type(period),
        "exclude_chart": False,
    }

    try:
        client = HttpClient.from_apikey_env()
        resp = client.request("post", "/v1/quant/run_script", body=body)
    except Exception as exc:  # longport 抛的是它自己的 OpenApiException，统一包装成 BacktestError
        raise BacktestError(f"/v1/quant/run_script 调用失败: {exc}") from exc

    report = json.loads(resp["report_json"]) if resp.get("report_json") else {}
    events = json.loads(resp["events_json"]) if resp.get("events_json") else []
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
