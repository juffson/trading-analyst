#!/usr/bin/env python3
"""
lb_client.py — Longbridge 统一数据客户端

同时支持两种数据源，对外暴露同一套子命令和相同的 JSON 输出格式：
  - CLI 模式：调用 `longbridge` 命令行工具（需已登录）
  - API 模式：调用 longport Python SDK（需配置 LONGPORT_* 环境变量）

自动检测顺序
  1. LONGBRIDGE_MODE=cli|api 环境变量（强制指定）
  2. longport 已安装 + LONGPORT_APP_KEY 存在 → api
  3. longbridge CLI 可用 → cli
  4. 两者都不可用 → 报错

子命令（输出均为 JSON）
  detect                    检测当前可用模式
  quote     <symbol>...     实时报价
  kline     <symbol> --period <p> --count <n> [--adjust none|forward|backward]
  calc-index <symbol>...    估值指标（PE/PB/换手率/总市值）
  capital   <symbol>        资金分布（大/中/小单流入流出）
  capital   <symbol> --flow 分时资金净流入
  static    <symbol>...     基础信息（EPS/BPS/股本/股息）
  positions                 当前持仓（仅港美股）
  orders    [--history --start YYYY-MM-DD] [--symbol <s>]  订单列表
  executions [--history --start YYYY-MM-DD] [--symbol <s>] 成交记录

CLI-only 命令（API 模式下需要 CLI 兜底，否则返回 ok=false）
  institution-rating <symbol>
  forecast-eps <symbol>
  news <symbol> [--count n]

用法示例
  python3 lb_client.py detect
  python3 lb_client.py quote AAPL.US
  python3 lb_client.py kline AAPL.US --period day --count 60
  python3 lb_client.py calc-index AAPL.US
  python3 lb_client.py capital AAPL.US
  python3 lb_client.py capital AAPL.US --flow
  python3 lb_client.py static AAPL.US
  python3 lb_client.py positions
  python3 lb_client.py orders --history --start 2026-01-01
  python3 lb_client.py executions --history --start 2026-01-01
  LONGBRIDGE_MODE=cli python3 lb_client.py quote AAPL.US   # 强制 CLI
  LONGBRIDGE_MODE=api python3 lb_client.py quote AAPL.US   # 强制 API
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, date


# ─── 工具函数 ──────────────────────────────────────────────────────────────────

def out(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(0)


def fail(msg, **extra):
    result = {"ok": False, "error": msg}
    result.update(extra)
    print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(1)


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ─── 模式检测 ──────────────────────────────────────────────────────────────────

def _has_api():
    try:
        import longport  # noqa: F401
        return bool(os.environ.get("LONGPORT_APP_KEY"))
    except ImportError:
        return False


def _has_cli():
    return shutil.which("longbridge") is not None


def detect_mode():
    explicit = os.environ.get("LONGBRIDGE_MODE", "").lower()
    if explicit in ("cli", "api"):
        has = _has_api() if explicit == "api" else _has_cli()
        if not has:
            fail(f"LONGBRIDGE_MODE={explicit} 但对应工具不可用",
                 hint="api 需要 pip install longport + LONGPORT_APP_KEY/SECRET/ACCESS_TOKEN；cli 需要 longbridge 命令")
        return explicit
    if _has_api():
        return "api"
    if _has_cli():
        return "cli"
    return None


# ─── CLI 模式实现 ──────────────────────────────────────────────────────────────

def cli_run(cmd_args, parse_json=True):
    """运行 longbridge 子命令，返回 parsed JSON 或原始文本。"""
    proc = subprocess.run(
        ["longbridge"] + cmd_args,
        capture_output=True, text=True
    )
    if proc.returncode != 0:
        fail(f"longbridge 命令失败: {proc.stderr.strip()}",
             cmd=cmd_args, returncode=proc.returncode)
    if not parse_json:
        return proc.stdout
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        # CLI 有时返回带换行/注释的 JSON，尝试容错
        text = proc.stdout.strip()
        try:
            return json.loads(text)
        except Exception:
            fail("CLI 输出不是合法 JSON", raw_output=proc.stdout[:500])


def cli_quote(symbols):
    data = cli_run(["quote"] + symbols + ["--format", "json"])
    # longbridge quote --format json 返回数组，主价格字段名为 "last"
    result = []
    rows = data if isinstance(data, list) else [data]
    for r in rows:
        pre = r.get("pre_market_quote") or r.get("pre_market")
        post = r.get("post_market_quote") or r.get("post_market")
        result.append({
            "symbol": r.get("symbol"),
            "last_done": to_float(r.get("last") or r.get("last_done") or r.get("current")),
            "prev_close": to_float(r.get("prev_close")),
            "open": to_float(r.get("open")),
            "high": to_float(r.get("high")),
            "low": to_float(r.get("low")),
            "volume": to_int(r.get("volume")),
            "turnover": to_float(r.get("turnover")),
            "trade_status": 0 if r.get("status") == "Normal" else r.get("trade_status", 0),
            "pre_market": {"price": to_float(pre.get("last")), "volume": to_int(pre.get("volume"))} if pre else None,
            "post_market": {"price": to_float(post.get("last")), "volume": to_int(post.get("volume"))} if post else None,
        })
    return result


PERIOD_MAP_CLI = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "day": "day", "week": "week", "month": "month", "year": "year",
}


def cli_kline(symbol, period, count, adjust):
    args = ["kline", symbol, "--period", PERIOD_MAP_CLI.get(period, period),
            "--count", str(count), "--format", "json"]
    if adjust and adjust != "none":
        args += ["--adjust", adjust]
    data = cli_run(args)
    # 标准化为 calc_indicators 期望格式
    rows = data if isinstance(data, list) else (data.get("candlesticks") or data.get("data") or [])
    return [_norm_candle(r) for r in rows]


def _norm_candle(r):
    return {
        "open": to_float(r.get("open")),
        "high": to_float(r.get("high")),
        "low": to_float(r.get("low")),
        "close": to_float(r.get("close")),
        "volume": to_int(r.get("volume")),
        "turnover": to_float(r.get("turnover")),
        "timestamp": to_int(r.get("timestamp") or r.get("time")),
    }


def cli_calc_index(symbols):
    data = cli_run(["calc-index"] + symbols + ["--format", "json"])
    rows = data if isinstance(data, list) else [data]
    result = []
    for r in rows:
        result.append({
            "symbol": r.get("symbol"),
            "pe_ttm": to_float(r.get("pe_ttm_ratio") or r.get("pe_ttm") or r.get("pe")),
            "pb": to_float(r.get("pb_ratio") or r.get("pb")),
            "turnover_rate": to_float(r.get("turnover_rate")),
            "total_market_value": to_float(r.get("total_market_value") or r.get("market_cap")),
        })
    return result


def cli_capital(symbol, flow=False):
    args = ["capital", symbol, "--format", "json"]
    if flow:
        args.append("--flow")
    data = cli_run(args)
    if flow:
        rows = data if isinstance(data, list) else data.get("items") or [data]
        return [{"timestamp": to_int(r.get("timestamp")),
                 "inflow": to_float(r.get("inflow") or r.get("net_inflow"))} for r in rows]
    return {
        "symbol": symbol,
        "timestamp": to_int(data.get("timestamp")),
        "capital_in": {
            "large": to_float((data.get("capital_in") or {}).get("large") or data.get("large_buy")),
            "medium": to_float((data.get("capital_in") or {}).get("medium") or data.get("medium_buy")),
            "small": to_float((data.get("capital_in") or {}).get("small") or data.get("small_buy")),
        },
        "capital_out": {
            "large": to_float((data.get("capital_out") or {}).get("large") or data.get("large_sell")),
            "medium": to_float((data.get("capital_out") or {}).get("medium") or data.get("medium_sell")),
            "small": to_float((data.get("capital_out") or {}).get("small") or data.get("small_sell")),
        },
    }


def cli_static(symbols):
    data = cli_run(["static"] + symbols + ["--format", "json"])
    rows = data if isinstance(data, list) else [data]
    return [_norm_static(r) for r in rows]


def _norm_static(r):
    return {
        "symbol": r.get("symbol"),
        "name": r.get("name") or r.get("name_cn"),
        "exchange": r.get("exchange"),
        "currency": r.get("currency"),
        "lot_size": to_int(r.get("lot_size")),
        "total_shares": to_int(r.get("total_shares")),
        "circulating_shares": to_int(r.get("circulating_shares")),
        "eps": to_float(r.get("eps")),
        "eps_ttm": to_float(r.get("eps_ttm")),
        "bps": to_float(r.get("bps")),
        "dividend_yield": to_float(r.get("dividend_yield")),
    }


def cli_positions():
    data = cli_run(["positions", "--format", "json"])
    rows = data if isinstance(data, list) else (data.get("positions") or data.get("list") or [data])
    return [_norm_position(r) for r in rows]


def _norm_position(r):
    return {
        "symbol": r.get("symbol"),
        "quantity": to_int(r.get("quantity") or r.get("qty")),
        "available_quantity": to_int(r.get("available_quantity") or r.get("available_qty") or r.get("sellable_qty")),
        "avg_cost": to_float(r.get("cost_price") or r.get("avg_price") or r.get("avg_cost")),
        "currency": r.get("currency"),
        "market_value": to_float(r.get("market_value")),
        "unrealized_pnl": to_float(r.get("unrealized_pnl") or r.get("pnl")),
        "unrealized_pnl_pct": to_float(r.get("unrealized_pnl_pct") or r.get("pnl_pct")),
    }


def cli_orders(history=False, start=None, symbol=None):
    args = ["order"]
    if history:
        args.append("--history")
    if start:
        args += ["--start", start]
    if symbol:
        args += ["--symbol", symbol]
    args += ["--format", "json"]
    data = cli_run(args)
    rows = data if isinstance(data, list) else (data.get("orders") or data.get("list") or [data])
    return [_norm_order(r) for r in rows]


def _norm_order(r):
    return {
        "order_id": r.get("order_id") or r.get("id"),
        "symbol": r.get("symbol"),
        "side": r.get("side"),
        "order_type": r.get("order_type"),
        "status": r.get("status"),
        "price": to_float(r.get("price")),
        "executed_price": to_float(r.get("executed_price")),
        "quantity": to_int(r.get("quantity") or r.get("qty")),
        "executed_quantity": to_int(r.get("executed_quantity") or r.get("executed_qty")),
        "submitted_at": r.get("submitted_at"),
        "updated_at": r.get("updated_at"),
        "remark": r.get("remark"),
    }


def cli_executions(history=False, start=None, symbol=None):
    args = ["order", "executions"]
    if history:
        args.append("--history")
    if start:
        args += ["--start", start]
    if symbol:
        args += ["--symbol", symbol]
    args += ["--format", "json"]
    data = cli_run(args)
    rows = data if isinstance(data, list) else (data.get("executions") or data.get("list") or [data])
    return [_norm_exec(r) for r in rows]


def _norm_exec(r):
    return {
        "order_id": r.get("order_id"),
        "trade_id": r.get("trade_id") or r.get("execution_id"),
        "symbol": r.get("symbol"),
        "side": r.get("side"),
        "price": to_float(r.get("price")),
        "quantity": to_int(r.get("quantity") or r.get("qty")),
        "timestamp": r.get("trade_done_at") or r.get("timestamp"),
    }


# ─── API 模式实现 ──────────────────────────────────────────────────────────────

PERIOD_MAP_API = {
    "1m": None, "5m": None, "15m": None, "30m": None, "1h": None,
    "day": None, "week": None, "month": None,
}


def _get_api_period(period_str):
    try:
        from longport.openapi import Period
        mapping = {
            "1m": Period.Min_1, "5m": Period.Min_5,
            "15m": Period.Min_15, "30m": Period.Min_30,
            "1h": Period.Hour,
            "day": Period.Day, "week": Period.Week, "month": Period.Month,
        }
        p = mapping.get(period_str)
        if p is None:
            fail(f"API 模式不支持 period={period_str}，支持: {list(mapping.keys())}")
        return p
    except ImportError:
        fail("longport 未安装，请 pip install longport")


def _get_api_adjust(adjust_str):
    try:
        from longport.openapi import AdjustType
        mapping = {
            "none": AdjustType.NoAdjust,
            "forward": AdjustType.ForwardAdjust,
            "backward": AdjustType.BackwardAdjust,
        }
        return mapping.get(adjust_str, AdjustType.NoAdjust)
    except ImportError:
        fail("longport 未安装")


def _api_ctx():
    """返回 (quote_ctx, trade_ctx)，按需懒初始化。"""
    try:
        from longport.openapi import QuoteContext, TradeContext, Config
        config = Config.from_env()
        return QuoteContext(config), TradeContext(config)
    except ImportError:
        fail("longport 未安装，请 pip install longport")
    except Exception as e:
        fail(f"OpenAPI 初始化失败: {e}",
             hint="检查 LONGPORT_APP_KEY / LONGPORT_APP_SECRET / LONGPORT_ACCESS_TOKEN 是否正确")


def api_quote(symbols):
    qctx, _ = _api_ctx()
    resp = qctx.quote(symbols)
    result = []
    for r in (resp if isinstance(resp, list) else [resp]):
        pre = getattr(r, "pre_market_quote", None)
        post = getattr(r, "post_market_quote", None)
        result.append({
            "symbol": str(r.symbol),
            "last_done": to_float(r.last_done),
            "prev_close": to_float(r.prev_close),
            "open": to_float(r.open),
            "high": to_float(r.high),
            "low": to_float(r.low),
            "volume": to_int(r.volume),
            "turnover": to_float(r.turnover),
            "trade_status": int(r.trade_status) if r.trade_status is not None else 0,
            "pre_market": {"price": to_float(pre.last_done), "volume": to_int(pre.volume)} if pre else None,
            "post_market": {"price": to_float(post.last_done), "volume": to_int(post.volume)} if post else None,
        })
    return result


def api_kline(symbol, period, count, adjust):
    qctx, _ = _api_ctx()
    p = _get_api_period(period)
    adj = _get_api_adjust(adjust)
    resp = qctx.history_candlesticks_by_offset(symbol, p, adj, False, count)
    candles = resp if isinstance(resp, list) else [resp]
    return [_norm_candle({
        "open": str(c.open), "high": str(c.high), "low": str(c.low), "close": str(c.close),
        "volume": c.volume, "turnover": str(c.turnover),
        "timestamp": int(c.timestamp.timestamp()) if hasattr(c.timestamp, "timestamp") else c.timestamp,
    }) for c in candles]


def api_calc_index(symbols):
    try:
        from longport.openapi import CalcIndex
        calc_fields = [
            CalcIndex.PeTtmRatio, CalcIndex.PbRatio,
            CalcIndex.TurnoverRate, CalcIndex.TotalMarketValue,
        ]
    except (ImportError, AttributeError):
        calc_fields = [1, 2, 3, 4]

    qctx, _ = _api_ctx()
    resp = qctx.calc_indexes(symbols, calc_fields)
    rows = resp if isinstance(resp, list) else [resp]
    result = []
    for r in rows:
        result.append({
            "symbol": str(r.symbol),
            "pe_ttm": to_float(getattr(r, "pe_ttm_ratio", None)),
            "pb": to_float(getattr(r, "pb_ratio", None)),
            "turnover_rate": to_float(getattr(r, "turnover_rate", None)),
            "total_market_value": to_float(getattr(r, "total_market_value", None)),
        })
    return result


def api_capital(symbol, flow=False):
    qctx, _ = _api_ctx()
    if flow:
        resp = qctx.capital_flow(symbol)
        rows = resp if isinstance(resp, list) else [resp]
        return [{"timestamp": int(r.timestamp.timestamp()) if hasattr(r.timestamp, "timestamp") else r.timestamp,
                 "inflow": to_float(r.inflow)} for r in rows]
    resp = qctx.capital_distribution(symbol)
    ci = resp.capital_in
    co = resp.capital_out
    return {
        "symbol": symbol,
        "timestamp": int(resp.timestamp.timestamp()) if hasattr(resp.timestamp, "timestamp") else None,
        "capital_in": {
            "large": to_float(ci.large), "medium": to_float(ci.medium), "small": to_float(ci.small),
        },
        "capital_out": {
            "large": to_float(co.large), "medium": to_float(co.medium), "small": to_float(co.small),
        },
    }


def api_static(symbols):
    qctx, _ = _api_ctx()
    resp = qctx.static_info(symbols)
    rows = resp if isinstance(resp, list) else [resp]
    return [_norm_static({
        "symbol": str(r.symbol),
        "name": getattr(r, "name_cn", None) or getattr(r, "name", None),
        "exchange": str(r.exchange) if hasattr(r, "exchange") else None,
        "currency": getattr(r, "currency", None),
        "lot_size": getattr(r, "lot_size", None),
        "total_shares": getattr(r, "total_shares", None),
        "circulating_shares": getattr(r, "circulating_shares", None),
        "eps": getattr(r, "eps", None),
        "eps_ttm": getattr(r, "eps_ttm", None),
        "bps": getattr(r, "bps", None),
        "dividend_yield": getattr(r, "dividend_yield", None),
    }) for r in rows]


def api_positions():
    _, tctx = _api_ctx()
    resp = tctx.stock_positions()
    positions = []
    channels = resp.channels if hasattr(resp, "channels") else (resp if isinstance(resp, list) else [resp])
    for ch in channels:
        stocks = ch.positions if hasattr(ch, "positions") else (ch if isinstance(ch, list) else [])
        for r in stocks:
            positions.append(_norm_position({
                "symbol": str(r.symbol),
                "quantity": getattr(r, "quantity", None),
                "available_quantity": getattr(r, "available_quantity", None),
                "avg_cost": getattr(r, "cost_price", None),
                "currency": getattr(r, "currency", None),
                "market_value": getattr(r, "market_value", None),
            }))
    return positions


def api_orders(history=False, start=None, symbol=None):
    _, tctx = _api_ctx()
    kwargs = {}
    if symbol:
        kwargs["symbol"] = symbol
    if start:
        try:
            from datetime import timezone
            dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            kwargs["start_at"] = dt
        except Exception:
            pass
    if history:
        resp = tctx.history_orders(**kwargs)
    else:
        resp = tctx.today_orders(**kwargs)
    rows = resp if isinstance(resp, list) else (getattr(resp, "orders", [resp]))
    return [_norm_order({
        "order_id": str(r.order_id),
        "symbol": str(r.symbol),
        "side": str(r.side) if hasattr(r, "side") else None,
        "order_type": str(r.order_type) if hasattr(r, "order_type") else None,
        "status": str(r.status) if hasattr(r, "status") else None,
        "price": str(getattr(r, "price", 0)),
        "executed_price": str(getattr(r, "executed_price", 0)),
        "quantity": getattr(r, "quantity", None),
        "executed_quantity": getattr(r, "executed_quantity", None),
        "submitted_at": str(getattr(r, "submitted_at", "")),
        "updated_at": str(getattr(r, "updated_at", "")),
        "remark": getattr(r, "remark", None),
    }) for r in rows]


def api_executions(history=False, start=None, symbol=None):
    _, tctx = _api_ctx()
    kwargs = {}
    if symbol:
        kwargs["symbol"] = symbol
    if start:
        try:
            from datetime import timezone
            dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            kwargs["start_at"] = dt
        except Exception:
            pass
    if history:
        resp = tctx.history_executions(**kwargs)
    else:
        resp = tctx.today_executions(**kwargs)
    rows = resp if isinstance(resp, list) else (getattr(resp, "executions", [resp]))
    return [_norm_exec({
        "order_id": str(getattr(r, "order_id", "")),
        "trade_id": str(getattr(r, "trade_id", "")),
        "symbol": str(r.symbol),
        "side": str(r.side) if hasattr(r, "side") else None,
        "price": str(getattr(r, "price", 0)),
        "quantity": getattr(r, "quantity", None),
        "trade_done_at": str(getattr(r, "trade_done_at", "")),
    }) for r in rows]


# ─── CLI-only fallback ────────────────────────────────────────────────────────

def cli_only_fallback(subcmd, args_extra):
    """对 API 模式下不支持的命令，尝试 CLI 兜底；都没有就返回 ok=false。"""
    if _has_cli():
        return cli_run([subcmd] + args_extra + ["--format", "json"])
    return {"ok": False, "cli_fallback_required": True,
            "error": f"`{subcmd}` 仅 CLI 支持，API 模式下需要同时安装 longbridge CLI 作为兜底"}


# ─── 分发 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Longbridge 统一数据客户端（CLI / OpenAPI 双模式）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("subcmd", help="子命令")
    parser.add_argument("symbols", nargs="*", help="标的代码（可多个）")
    parser.add_argument("--period", default="day", help="K 线周期：1m/5m/15m/30m/1h/day/week/month")
    parser.add_argument("--count", type=int, default=60, help="K 线条数")
    parser.add_argument("--adjust", default="none", choices=["none", "forward", "backward"], help="复权类型")
    parser.add_argument("--flow", action="store_true", help="资金分时流入流出（capital 命令专用）")
    parser.add_argument("--history", action="store_true", help="历史数据（orders/executions 专用）")
    parser.add_argument("--start", help="历史起始日期 YYYY-MM-DD")
    parser.add_argument("--symbol", help="按标的筛选（orders/executions 专用）")

    args = parser.parse_args()
    subcmd = args.subcmd.lower()
    mode = detect_mode()

    # ── detect ───────────────────────────────────────────────────────────────
    if subcmd == "detect":
        out({
            "active_mode": mode,
            "cli_available": _has_cli(),
            "api_available": _has_api(),
            "env_override": os.environ.get("LONGBRIDGE_MODE"),
        })

    if mode is None:
        fail("找不到可用的数据源",
             hint="请安装 longbridge CLI（https://github.com/longbridge/longbridge-terminal）"
                  " 或配置 OpenAPI（pip install longport + LONGPORT_APP_KEY/SECRET/ACCESS_TOKEN）")

    symbols = args.symbols

    # ── quote ────────────────────────────────────────────────────────────────
    if subcmd == "quote":
        if not symbols:
            fail("quote 需要至少一个标的代码")
        result = api_quote(symbols) if mode == "api" else cli_quote(symbols)
        out({"ok": True, "mode": mode, "data": result})

    # ── kline ────────────────────────────────────────────────────────────────
    elif subcmd == "kline":
        if not symbols:
            fail("kline 需要标的代码")
        result = (api_kline(symbols[0], args.period, args.count, args.adjust)
                  if mode == "api"
                  else cli_kline(symbols[0], args.period, args.count, args.adjust))
        out({"ok": True, "mode": mode, "symbol": symbols[0],
             "period": args.period, "count": len(result), "data": result})

    # ── calc-index ───────────────────────────────────────────────────────────
    elif subcmd in ("calc-index", "calc_index"):
        if not symbols:
            fail("calc-index 需要至少一个标的代码")
        result = api_calc_index(symbols) if mode == "api" else cli_calc_index(symbols)
        out({"ok": True, "mode": mode, "data": result})

    # ── capital ──────────────────────────────────────────────────────────────
    elif subcmd == "capital":
        if not symbols:
            fail("capital 需要标的代码")
        result = (api_capital(symbols[0], args.flow)
                  if mode == "api"
                  else cli_capital(symbols[0], args.flow))
        out({"ok": True, "mode": mode, "flow": args.flow, "data": result})

    # ── static ───────────────────────────────────────────────────────────────
    elif subcmd == "static":
        if not symbols:
            fail("static 需要至少一个标的代码")
        result = api_static(symbols) if mode == "api" else cli_static(symbols)
        out({"ok": True, "mode": mode, "data": result})

    # ── positions ────────────────────────────────────────────────────────────
    elif subcmd == "positions":
        result = api_positions() if mode == "api" else cli_positions()
        out({"ok": True, "mode": mode, "data": result})

    # ── orders ───────────────────────────────────────────────────────────────
    elif subcmd == "orders":
        result = (api_orders(args.history, args.start, args.symbol)
                  if mode == "api"
                  else cli_orders(args.history, args.start, args.symbol))
        out({"ok": True, "mode": mode, "data": result})

    # ── executions ───────────────────────────────────────────────────────────
    elif subcmd == "executions":
        result = (api_executions(args.history, args.start, args.symbol)
                  if mode == "api"
                  else cli_executions(args.history, args.start, args.symbol))
        out({"ok": True, "mode": mode, "data": result})

    # ── CLI-only 命令（institution-rating / forecast-eps / news 等） ──────────
    elif subcmd in ("institution-rating", "forecast-eps", "news",
                    "financial-report", "consensus", "valuation"):
        if mode == "api":
            result = cli_only_fallback(subcmd, symbols)
        else:
            result = cli_run([subcmd] + symbols + ["--format", "json"])
        out({"ok": True, "mode": mode, "cli_fallback": (mode == "api" and _has_cli()), "data": result})

    else:
        # 未知命令：直接透传给 CLI（仅 CLI 模式有效）
        if mode != "cli":
            fail(f"未知命令 `{subcmd}`，API 模式下只支持内置子命令")
        result = cli_run([subcmd] + symbols + (["--format", "json"] if "--format" not in symbols else []))
        out({"ok": True, "mode": "cli", "passthrough": True, "data": result})


if __name__ == "__main__":
    main()
