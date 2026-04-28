#!/usr/bin/env python3
"""
通过 Longbridge quant run 执行 QuantScript 回测，支持两种模式：

  CLI 模式（默认）：调用 `longbridge quant run`，与 trading-analyst 共用同一套认证
  API 模式：直接 HTTP POST https://openapi.longbridge.cn/v1/quant/run_script

模式选择：
  LONGBRIDGE_MODE=cli   强制 CLI 模式
  LONGBRIDGE_MODE=api   强制 API 模式（需配置 LONGPORT_ACCESS_TOKEN）
  不设置               自动检测：LONGPORT_ACCESS_TOKEN 存在 → api，否则 → cli

API 模式需要的环境变量：
  LONGPORT_APP_KEY        App Key
  LONGPORT_APP_SECRET     App Secret（签名用）
  LONGPORT_ACCESS_TOKEN   Access Token（从 open.longbridge.cn 开发者后台获取）
  LONGPORT_OPENAPI_HTTP_URL  可选，覆盖 base URL（默认 https://openapi.longbridge.cn）

用法：
  python3 scripts/run_script.py \\
    --script path/to/strategy.pine \\
    --counter TSLA.US \\
    --start 2024-01-01 --end 2024-12-31 \\
    [--period day] [--inputs "[21, 80, 20]"] [--out /tmp/result.json]

  python3 scripts/run_script.py \\
    --script-text '//@version=6 ...' \\
    --counter 700.HK --start 2023-01-01 --end 2024-12-31

符号格式：TSLA.US / 700.HK / 600519.SH / 000001.SZ（CLI 格式，API 模式自动转换）
"""

import argparse
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


# ─── 模式检测 ─────────────────────────────────────────────────────────────────

def _has_cli():
    return shutil.which("longbridge") is not None


def _has_api():
    return bool(os.environ.get("LONGPORT_APP_KEY") and
                os.environ.get("LONGPORT_APP_SECRET") and
                os.environ.get("LONGPORT_ACCESS_TOKEN"))


def detect_mode():
    override = os.environ.get("LONGBRIDGE_MODE", "").lower()
    if override == "cli":
        if not _has_cli():
            _fail("LONGBRIDGE_MODE=cli 但找不到 longbridge 命令")
        return "cli"
    if override == "api":
        if not _has_api():
            _fail("LONGBRIDGE_MODE=api 但缺少 LONGPORT_APP_KEY/SECRET/ACCESS_TOKEN")
        return "api"
    if _has_api():
        return "api"
    if _has_cli():
        return "cli"
    _fail("找不到可用数据源。请安装 longbridge CLI 或配置 LONGPORT_APP_KEY/SECRET/ACCESS_TOKEN")


def _fail(msg):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(1)


# ─── 符号格式转换 ─────────────────────────────────────────────────────────────
# CLI 格式: TSLA.US / 700.HK / 600519.SH / 000001.SZ
# API 格式: ST/US/TSLA / ST/HK/700 / ST/SH/600519 / ST/SZ/000001

_MARKET_MAP = {"US": "US", "HK": "HK", "SH": "SH", "SZ": "SZ", "SG": "SG"}


def to_counter_id(symbol):
    """TSLA.US → ST/US/TSLA"""
    if symbol.startswith("ST/"):
        return symbol  # 已经是 API 格式
    parts = symbol.rsplit(".", 1)
    if len(parts) != 2:
        _fail(f"无法解析符号格式: {symbol}，请用 TSLA.US / 700.HK / 600519.SH")
    code, market = parts
    market = market.upper()
    if market not in _MARKET_MAP:
        _fail(f"不支持的市场: {market}")
    return f"ST/{_MARKET_MAP[market]}/{code}"


# ─── 周期转换 ─────────────────────────────────────────────────────────────────

_PERIOD_TO_LINE_TYPE = {
    "day": 1000, "week": 2000, "month": 3000, "year": 4000,
    "1h": 60, "30m": 30, "15m": 15, "5m": 5, "1m": 1,
}


def to_line_type(period):
    lt = _PERIOD_TO_LINE_TYPE.get(period)
    if lt is None:
        _fail(f"不支持的 period: {period}")
    return lt


# ─── 日期转时间戳 ─────────────────────────────────────────────────────────────

def to_ts(s):
    """'2024-01-01' → Unix 秒时间戳（UTC 00:00:00）"""
    try:
        return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        try:
            return int(s)
        except ValueError:
            _fail(f"无法解析时间 '{s}'，请用 YYYY-MM-DD 或 Unix 时间戳")


# ─── CLI 模式 ─────────────────────────────────────────────────────────────────

def cli_run_script(script, symbol, start, end, period="day", inputs=None):
    args = [
        "longbridge", "quant", "run", symbol,
        "--start", start, "--end", end,
        "--period", period,
        "--script", script,
        "--format", "json",
    ]
    if inputs:
        args += ["--input", inputs]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        _fail(f"longbridge quant run 失败:\n{proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        _fail(f"CLI 输出不是合法 JSON:\n{proc.stdout[:500]}")


# ─── API 模式 ─────────────────────────────────────────────────────────────────

def _make_headers(app_key, app_secret, access_token, body_bytes):
    """Longbridge OpenAPI 标准签名。"""
    timestamp = str(int(time.time()))
    sign_str = timestamp + app_key + access_token + body_bytes.decode("utf-8")
    sig = hmac.new(
        app_secret.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-Api-Key": app_key,
        "Authorization": f"Bearer {access_token}",
        "X-Timestamp": timestamp,
        "X-Api-Signature": sig,
    }


def api_run_script(script, symbol, start, end, period="day", inputs=None):
    app_key = os.environ["LONGPORT_APP_KEY"]
    app_secret = os.environ["LONGPORT_APP_SECRET"]
    access_token = os.environ["LONGPORT_ACCESS_TOKEN"]
    base_url = os.environ.get("LONGPORT_OPENAPI_HTTP_URL", "https://openapi.longbridge.cn")

    payload = {
        "script": script,
        "counter_id": to_counter_id(symbol),
        "start_time": to_ts(start),
        "end_time": to_ts(end),
        "line_type": to_line_type(period),
        "input_json": inputs or "[]",
    }
    body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = _make_headers(app_key, app_secret, access_token, body_bytes)

    url = base_url.rstrip("/") + "/v1/quant/run_script"
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        _fail(f"HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        _fail(f"网络错误: {e.reason}")


# ─── 结果展示 ─────────────────────────────────────────────────────────────────

def _fmt(v, d=3):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def _pct(v):
    if v is None:
        return "N/A"
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return str(v)


def show_report(report):
    if not report:
        return
    pa = report.get("performanceAll") or {}
    trades = report.get("closedTrades") or []

    print("\n── 策略报告 ──────────────────────────────")
    print(f"  净利润        {_fmt(pa.get('netProfit'), 2)}  ({_pct(pa.get('netProfitPercent'))})")
    print(f"  买入持有      {_fmt(pa.get('buyHoldReturn'), 2)}  ({_pct(pa.get('buyHoldReturnPercent'))})")
    print(f"  最大回撤      {_fmt(pa.get('maxDrawdown'), 2)}  ({_pct(pa.get('maxDrawdownPercent'))})")
    print(f"  夏普比率      {_fmt(pa.get('sharpeRatio'))}")
    print(f"  索提诺比率    {_fmt(pa.get('sortinoRatio'))}")
    print(f"  总交易次数    {pa.get('totalClosedTrades', 'N/A')}")
    print(f"  胜率          {_pct(pa.get('percentProfitable'))}")
    if pa.get("totalClosedTrades"):
        print(f"  盈亏比        {_fmt(pa.get('profitFactor'))}")
        print(f"  平均盈利      {_fmt(pa.get('avgWinningTrade'), 2)}")
        print(f"  平均亏损      {_fmt(pa.get('avgLosingTrade'), 2)}")

    pl = report.get("performanceLong") or {}
    ps = report.get("performanceShort") or {}
    if pl.get("totalClosedTrades") and ps.get("totalClosedTrades"):
        print(f"\n  多头: {pl['totalClosedTrades']} 笔 | 胜率 {_pct(pl.get('percentProfitable'))} | 净利 {_fmt(pl.get('netProfit'), 2)}")
        print(f"  空头: {ps['totalClosedTrades']} 笔 | 胜率 {_pct(ps.get('percentProfitable'))} | 净利 {_fmt(ps.get('netProfit'), 2)}")

    if trades:
        print(f"\n  最近 {min(3, len(trades))} 笔成交:")
        for t in trades[-3:]:
            direction = "多" if t.get("entrySide") == "long" else "空"
            ts = t.get("entryTime", 0)
            date = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if ts else "?"
            print(f"    {date} [{direction}] 入 {_fmt(t.get('entryPrice'), 3)} → 出 {_fmt(t.get('exitPrice'), 3)}  {_pct(t.get('profitPercent'))}")


def show_chart(chart):
    if not chart:
        return
    sg = chart.get("series_graphs") or {}
    if not sg:
        return
    print("\n── 图表输出 ──────────────────────────────")
    for idx in sorted(sg.keys(), key=lambda x: int(x)):
        entry = sg[idx]
        plot_type = next(iter(entry))
        inner = entry[plot_type]
        title = inner.get("title", f"plot-{idx}")
        series = inner.get("series") or []
        style = inner.get("style", "Line")
        vals = [v for v in series if v is not None]
        if vals:
            print(f"  {title} [{style}]  最新={_fmt(vals[-1], 4)}  范围=[{_fmt(min(vals), 4)}, {_fmt(max(vals), 4)}]  均值={_fmt(sum(vals)/len(vals), 4)}  ({len(vals)} bars)")
        else:
            print(f"  {title} [{style}]  (无数据)")

    filled = chart.get("filled_orders") or {}
    if filled:
        buys = sum(1 for ords in filled.values() for o in ords if o.get("quantity", 0) > 0)
        sells = sum(1 for ords in filled.values() for o in ords if o.get("quantity", 0) < 0)
        print(f"\n  成交信号: 买入 {buys} 次 / 卖出 {sells} 次")


def show_events_summary(events):
    if not events:
        return
    bars = sum(1 for e in events if e == "BarEnd")
    alerts = [e["Alert"]["message"] for e in events if isinstance(e, dict) and "Alert" in e]
    print(f"\n── 执行概要 ──────────────────────────────")
    print(f"  共执行 {bars} 个 bar")
    if alerts:
        print(f"  Alert ({len(alerts)} 条):")
        for msg in alerts[:10]:
            print(f"    • {msg}")
        if len(alerts) > 10:
            print(f"    ... 还有 {len(alerts) - 10} 条")


# ─── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Longbridge QuantScript 回测（CLI / API 双模式）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--script", help="QuantScript 脚本文件路径")
    group.add_argument("--script-text", help="QuantScript 脚本内容")
    parser.add_argument("--counter", required=True,
                        help="标的代码，如 TSLA.US / 700.HK / 600519.SH")
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD 或 Unix 时间戳")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD 或 Unix 时间戳")
    parser.add_argument("--period", default="day",
                        choices=["1m","5m","15m","30m","1h","day","week","month","year"],
                        help="K线周期（默认 day）")
    parser.add_argument("--inputs", default=None,
                        help='inputs 覆盖，JSON 数组字符串，如 "[21, 80, 20]"')
    parser.add_argument("--out", default=None, help="保存原始响应 JSON 到文件（供 chart.py 使用）")
    parser.add_argument("--raw", action="store_true", help="只打印原始 JSON")
    args = parser.parse_args()

    if args.script:
        try:
            with open(args.script, "r", encoding="utf-8") as f:
                script = f.read()
        except FileNotFoundError:
            _fail(f"找不到脚本文件 '{args.script}'")
    else:
        script = args.script_text

    if not script.strip().startswith("//@version="):
        print("警告: 脚本第一行应为 //@version=6", file=sys.stderr)

    mode = detect_mode()
    print(f"运行中 [{mode.upper()}]: {args.counter} [{args.start} → {args.end}] period={args.period}", file=sys.stderr)

    if mode == "cli":
        result = cli_run_script(script, args.counter, args.start, args.end, args.period, args.inputs)
    else:
        result = api_run_script(script, args.counter, args.start, args.end, args.period, args.inputs)

    if result.get("code", 0) != 0:
        _fail(f"API 错误 {result.get('code')}: {result.get('message')}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"结果已保存: {args.out}", file=sys.stderr)

    if args.raw:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    data = result.get("data", {})
    report = json.loads(data["report_json"]) if data.get("report_json") else {}
    chart = json.loads(data["chart_json"]) if data.get("chart_json") else {}
    events = json.loads(data["events_json"]) if data.get("events_json") else []

    show_report(report)
    show_chart(chart)
    show_events_summary(events)

    if not report and not chart:
        print("\n(无可展示的结果，请检查脚本内容)")
    print()


if __name__ == "__main__":
    main()
