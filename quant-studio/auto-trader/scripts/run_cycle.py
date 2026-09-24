#!/usr/bin/env python3
"""auto-trader 的 cron 入口。对 config/strategies.json 里每个非 disabled 的标的:

1. signal_from_backtest：滚动跑该标的已验证的 QuantScript（直接调 longbridge CLI），
   取 report_json.closedTrades/openTrades 里「今天」新出现的信号
2. 去重：同一天同样的信号不会重复生成计划（state_store）
3. plan_store：把信号变成一份 status=pending 的交易计划，写入 watchlist/<symbol>/plans.jsonl
   —— 这一步永远发生，不受 execution_mode 影响，保证"有信号就留痕"
4. execution_mode 决定是否往下走:
     "disabled"     跳过该标的，连回测都不跑
     "signal_only"  只产出计划，不碰 risk_guard / executor（默认值，最安全）
     "auto_paper"   继续走 risk_guard 风控 → executor 预览下单（仍然只是 --dry-run 预览）
   没有 "auto_live" 这个选项——真实下单只能手动调用 executor.confirm_order()，
   不会因为改一个配置字符串就自动发生。
5. report.emit 写运行日志

不含任何 LLM 调用——决策全部来自 calibrating 阶段已经验证过的规则策略。
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor  # noqa: E402
import plan_store  # noqa: E402
import report  # noqa: E402
import risk_guard  # noqa: E402
import state_store  # noqa: E402
from signal_from_backtest import BacktestError, latest_signals, run_rolling_backtest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "strategies.json"
WATCHLIST_DIR = REPO_ROOT / "watchlist"

VALID_EXECUTION_MODES = {"disabled", "signal_only", "auto_paper"}


def load_config():
    if not CONFIG_PATH.exists():
        return {"symbols": {}}
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def append_trade_log(symbol, record):
    symbol_dir = WATCHLIST_DIR / symbol
    symbol_dir.mkdir(parents=True, exist_ok=True)
    log_file = symbol_dir / "trade_log.jsonl"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_symbol(symbol, cfg):
    execution_mode = cfg.get("execution_mode", "signal_only")
    if execution_mode not in VALID_EXECUTION_MODES:
        report.emit(symbol, "error", {
            "stage": "config",
            "message": f"未知 execution_mode={execution_mode!r}，只接受 {sorted(VALID_EXECUTION_MODES)}；"
                       "没有 auto_live——真实下单必须手动调用 executor.confirm_order()",
        })
        return

    strategy_file = REPO_ROOT / cfg["strategy_file"]
    if not strategy_file.exists():
        report.emit(symbol, "error", {"stage": "config", "message": f"策略文件不存在: {strategy_file}"})
        return

    period = cfg.get("period", "day")
    lookback_days = cfg.get("lookback_days", 90)
    inputs = cfg.get("inputs")
    strategy_name = cfg.get("strategy_name", strategy_file.stem)
    risk_cfg = cfg.get("risk", {})

    try:
        strategy_report, _events, end_date = run_rolling_backtest(
            strategy_file, symbol, lookback_days=lookback_days, period=period, inputs=inputs
        )
    except BacktestError as exc:
        report.emit(symbol, "error", {"stage": "backtest", "message": str(exc)})
        return

    signals = latest_signals(strategy_report, end_date)
    if not signals:
        report.emit(symbol, "signal", {"message": "无新信号", "end_date": end_date.isoformat()})
        return

    today = end_date.isoformat()
    state = state_store.load(symbol)
    state = state_store.reset_daily_if_new_day(state, today)

    for sig in signals:
        side = sig["side"]
        qty = int(abs(sig["qty"])) if sig.get("qty") is not None else None
        price = sig.get("price")
        # time_ms 精确到毫秒，天然唯一——比"日期+价格+数量"更不容易误判重复/漏判
        signal_key = f"{symbol}:{sig.get('time_ms')}:{side}"

        # 去重发生在「生成计划」这一步之前——同一天同样的信号只生成一次计划，
        # 不管 execution_mode 是什么，避免 cron 每 15 分钟都重复写一份一样的 pending 计划。
        if state_store.is_signal_executed(state, signal_key):
            continue
        state_store.mark_signal_executed(state, signal_key)

        plan = plan_store.create_plan(
            symbol, strategy_name, side, qty, price, signal_key,
        )
        report.emit(symbol, "plan", {"plan_id": plan["id"], "side": side, "qty": qty, "price": price})

        if execution_mode == "signal_only":
            continue  # 停在这里——只留下 pending 状态的交易计划，等你自己看

        # execution_mode == "auto_paper"：继续走风控 + 预览下单
        decision = risk_guard.check(side, qty, price, risk_cfg, state)
        if not decision.allowed:
            plan_store.update_status(symbol, plan, "rejected", note=decision.reason)
            report.emit(symbol, "risk_block", {"plan_id": plan["id"], "reason": decision.reason})
            continue

        result = executor.place_order(symbol, side, qty, price)
        plan_store.update_status(symbol, plan, "executed", note="auto_paper 预览下单")
        append_trade_log(symbol, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "plan_id": plan["id"], "side": side, "qty": qty, "price": price,
            "result": result,
        })
        report.emit(symbol, "order", {"plan_id": plan["id"], "side": side, "qty": qty, "price": price})

        state["positions"]["shares"] = state["positions"].get("shares", 0) + (qty if side == "buy" else -qty)
        state["daily"]["orders_count"] = state["daily"].get("orders_count", 0) + 1

    state_store.save(symbol, state)


def main():
    parser = argparse.ArgumentParser(description="auto-trader 单轮运行")
    parser.add_argument("--symbol", help="只跑指定标的（默认跑 config 里所有标的）")
    args = parser.parse_args()

    cfg = load_config()
    symbols = cfg.get("symbols") or {}
    if args.symbol:
        symbols = {args.symbol: symbols[args.symbol]} if args.symbol in symbols else {}

    if not symbols:
        print("config/strategies.json 里没有可跑的标的（先从 strategies.example.json 复制一份并填好配置）",
              file=sys.stderr)
        return

    for symbol, symbol_cfg in symbols.items():
        if not symbol_cfg or symbol_cfg.get("execution_mode") == "disabled":
            continue
        process_symbol(symbol, symbol_cfg)


if __name__ == "__main__":
    main()
