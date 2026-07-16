#!/usr/bin/env python3
"""阶段判定器：不做分析，只读各阶段已产出的文件，判断是否达到进阶门槛。

不满足就原地不动，等下一轮再判断；满足就把 stage.json 推进到下一阶段。
从 paper_trading 到 promoted_live 永远需要人工确认——本脚本只会把
ready_for_live_promotion 标成 true，不会自己执行任何转实盘操作。

用法:
    python3 advance_stage.py <SYMBOL>
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

WATCHLIST_DIR = Path(__file__).resolve().parent.parent / "watchlist"

# ── 进阶门槛（保守默认值，按需调整）────────────────────────────────────
DEEP_DIVE_MIN_SCORE_RATIO = 0.65   # score_total / score_max 达到这个比例才进 calibrating
BACKTEST_MIN_SHARPE = 1.0
BACKTEST_MAX_DRAWDOWN_PCT = 20.0
BACKTEST_MIN_WIN_RATE_PCT = 45.0
REVIEW_STABLE_COUNT_FOR_LIVE = 4  # 连续多少次复盘 on_track 才提请转实盘


def _load_json(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _stage_path(symbol):
    return WATCHLIST_DIR / symbol / "stage.json"


def load_stage(symbol):
    data = _load_json(_stage_path(symbol))
    if data is None:
        data = {"symbol": symbol, "stage": "researching", "history": []}
    return data


def save_stage(symbol, data):
    path = _stage_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _advance(data, new_stage, reason):
    data["stage"] = new_stage
    data.setdefault("history", []).append({
        "stage": new_stage,
        "entered_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
    })


def evaluate(symbol):
    symbol_dir = WATCHLIST_DIR / symbol
    data = load_stage(symbol)
    stage = data["stage"]

    if stage == "researching":
        deep_dive = _load_json(symbol_dir / "deep_dive.json")
        if not deep_dive:
            return data, "等待 company-deep-dive 产出 deep_dive.json"
        ratio = deep_dive.get("score_total", 0) / max(deep_dive.get("score_max", 1), 1)
        if ratio >= DEEP_DIVE_MIN_SCORE_RATIO:
            _advance(data, "calibrating", f"深度分析评分比例 {ratio:.2f} 达标（阈值 {DEEP_DIVE_MIN_SCORE_RATIO}）")
        else:
            return data, f"深度分析评分比例 {ratio:.2f} 未达 {DEEP_DIVE_MIN_SCORE_RATIO}，停留在 researching"

    elif stage == "calibrating":
        backtest = _load_json(symbol_dir / "backtest_report.json")
        if not backtest:
            return data, "等待 quant-backtest 产出 backtest_report.json"
        ok = (
            backtest.get("sharpe_ratio", 0) >= BACKTEST_MIN_SHARPE
            and backtest.get("max_drawdown_pct", 100) <= BACKTEST_MAX_DRAWDOWN_PCT
            and backtest.get("win_rate_pct", 0) >= BACKTEST_MIN_WIN_RATE_PCT
        )
        if ok:
            _advance(
                data, "paper_trading",
                "回测指标达标（夏普/回撤/胜率均通过）——记得在 config/strategies.json 里手动补上该标的的配置",
            )
        else:
            return data, "回测指标未达标，停留在 calibrating（调参或换策略重跑）"

    elif stage == "paper_trading":
        reviews = sorted(symbol_dir.glob("review_*.json"))
        if not reviews:
            return data, "模拟交易运行中，等待 trading-analyst 模式5 生成首次复盘"

        recent = reviews[-REVIEW_STABLE_COUNT_FOR_LIVE:]
        verdicts = [(_load_json(r) or {}).get("verdict") for r in recent]

        if verdicts and verdicts[-1] == "off_track":
            _advance(data, "calibrating", "最近一次复盘 verdict=off_track，退回 calibrating 重新调参")
        elif len(recent) >= REVIEW_STABLE_COUNT_FOR_LIVE and all(v == "on_track" for v in verdicts):
            data["ready_for_live_promotion"] = True
            return data, (
                f"连续 {REVIEW_STABLE_COUNT_FOR_LIVE} 次复盘 on_track——"
                "可以考虑转实盘，但需要你自己手动确认，本脚本不会自动执行"
            )
        else:
            return data, "模拟交易运行中，持续复盘观察"

    elif stage == "promoted_live":
        return data, "已转实盘——本脚本不再管理该标的的阶段流转"

    else:
        return data, f"未知阶段: {stage}"

    return data, f"已推进到: {data['stage']}"


def main():
    parser = argparse.ArgumentParser(description="判断某标的是否达到下一阶段门槛")
    parser.add_argument("symbol")
    args = parser.parse_args()

    data, message = evaluate(args.symbol)
    save_stage(args.symbol, data)
    print(message)


if __name__ == "__main__":
    main()
