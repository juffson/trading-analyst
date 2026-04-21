#!/usr/bin/env python3
"""
技术指标计算脚本
用法: echo '<json_kline_data>' | python3 calc_indicators.py
或:   python3 calc_indicators.py < kline.json
输入: longbridge kline --format json 的输出
输出: JSON 格式的完整技术指标
"""

import json
import sys
from collections import defaultdict


def sma(arr, period, idx=None):
    if idx is None:
        idx = len(arr) - 1
    if idx < period - 1:
        return None
    return sum(arr[idx - period + 1:idx + 1]) / period


def ema_list(arr, period):
    ema = [arr[0]]
    k = 2 / (period + 1)
    for i in range(1, len(arr)):
        ema.append(arr[i] * k + ema[-1] * (1 - k))
    return ema


def calc_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_kdj(highs, lows, closes, n=9):
    kv, dv = [50], [50]
    for i in range(n - 1, len(closes)):
        ph = max(highs[i - n + 1:i + 1])
        pl = min(lows[i - n + 1:i + 1])
        rsv = (closes[i] - pl) / (ph - pl) * 100 if ph != pl else 50
        k = 2 / 3 * kv[-1] + 1 / 3 * rsv
        d = 2 / 3 * dv[-1] + 1 / 3 * k
        kv.append(k)
        dv.append(d)
    j = 3 * kv[-1] - 2 * dv[-1]
    return round(kv[-1], 2), round(dv[-1], 2), round(j, 2)


def calc_atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0
    return sum(trs[-period:]) / period


def analyze(data):
    closes = [float(d["close"]) for d in data]
    highs = [float(d["high"]) for d in data]
    lows = [float(d["low"]) for d in data]
    volumes = [int(d["volume"]) for d in data]
    n = len(closes)

    if n < 20:
        return {"error": "Not enough data points (need >= 20)"}

    result = {"current_price": closes[-1], "data_points": n}

    # Moving Averages
    mas = {}
    for p in [5, 10, 20, 30, 60]:
        val = sma(closes, p)
        if val:
            mas["MA%d" % p] = {
                "value": round(val, 2),
                "above": closes[-1] > val,
                "distance_pct": round((closes[-1] - val) / val * 100, 2),
                "rising": val > sma(closes, p, n - 2) if sma(closes, p, n - 2) else None,
            }
    result["moving_averages"] = mas

    # MACD
    e12 = ema_list(closes, 12)
    e26 = ema_list(closes, 26)
    dif = [e12[i] - e26[i] for i in range(n)]
    dea = ema_list(dif, 9)
    hist = [(dif[i] - dea[i]) * 2 for i in range(n)]

    macd_cross = "none"
    if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
        macd_cross = "golden_cross"
    elif dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
        macd_cross = "death_cross"

    result["macd"] = {
        "dif": round(dif[-1], 3),
        "dea": round(dea[-1], 3),
        "histogram": round(hist[-1], 3),
        "histogram_color": "red" if hist[-1] > 0 else "green",
        "histogram_expanding": abs(hist[-1]) > abs(hist[-2]),
        "dif_above_zero": dif[-1] > 0,
        "dif_above_dea": dif[-1] > dea[-1],
        "cross": macd_cross,
    }

    # RSI
    rsi6 = calc_rsi(closes, 6)
    rsi14 = calc_rsi(closes, 14)

    def rsi_label(v):
        if v < 20: return "severely_oversold"
        if v < 30: return "oversold"
        if v < 40: return "weak"
        if v < 60: return "neutral"
        if v < 70: return "strong"
        if v < 80: return "overbought"
        return "severely_overbought"

    result["rsi"] = {
        "rsi6": {"value": round(rsi6, 1), "signal": rsi_label(rsi6)},
        "rsi14": {"value": round(rsi14, 1), "signal": rsi_label(rsi14)},
    }

    # KDJ
    k, d, j = calc_kdj(highs, lows, closes)
    result["kdj"] = {
        "k": k, "d": d, "j": j,
        "k_above_d": k > d,
        "oversold": j < 0,
    }

    # Bollinger Bands
    if n >= 20:
        r20 = closes[-20:]
        bm = sum(r20) / 20
        bs = (sum((x - bm) ** 2 for x in r20) / 20) ** 0.5
        bu, bl = bm + 2 * bs, bm - 2 * bs
        bp = (closes[-1] - bl) / (bu - bl) * 100 if bu != bl else 50
        bw = (bu - bl) / bm * 100

        result["bollinger"] = {
            "upper": round(bu, 2),
            "middle": round(bm, 2),
            "lower": round(bl, 2),
            "position_pct": round(bp, 1),
            "bandwidth_pct": round(bw, 1),
            "squeeze": bw < 10,
        }

    # ATR
    atr = calc_atr(highs, lows, closes)
    result["atr"] = {
        "value": round(atr, 2),
        "pct_of_price": round(atr / closes[-1] * 100, 2),
        "half_range": round(atr / 2, 2),
    }

    # Volume
    vol_5 = sum(volumes[-5:]) / 5
    vol_10 = sum(volumes[-10:]) / 10 if n >= 10 else vol_5
    vol_20 = sum(volumes[-20:]) / 20 if n >= 20 else vol_5
    result["volume"] = {
        "current": volumes[-1],
        "ma5": int(vol_5),
        "ma10": int(vol_10),
        "ma20": int(vol_20),
        "ratio_vs_ma5": round(volumes[-1] / vol_5, 2) if vol_5 > 0 else 0,
    }

    # Fibonacci
    sh = max(highs[-30:]) if n >= 30 else max(highs)
    sl = min(lows[-30:]) if n >= 30 else min(lows)
    sr = sh - sl
    fib = {}
    for label, ratio in [("0%", 0), ("23.6%", 0.236), ("38.2%", 0.382),
                          ("50%", 0.5), ("61.8%", 0.618), ("78.6%", 0.786), ("100%", 1.0)]:
        level = sl + sr * ratio
        fib[label] = round(level, 2)
    result["fibonacci"] = {
        "swing_high": round(sh, 2),
        "swing_low": round(sl, 2),
        "levels": fib,
        "nearest_level": min(fib.items(), key=lambda x: abs(x[1] - closes[-1]))[0],
    }

    # Volume Profile (chipset density)
    price_vol = defaultdict(int)
    lookback = min(30, n)
    for i in range(-lookback, 0):
        bucket = round(closes[i])
        price_vol[bucket] += volumes[i]
    dense = sorted(price_vol.items(), key=lambda x: -x[1])[:8]
    result["volume_profile"] = [{"price": p, "volume": v} for p, v in dense]

    # Composite Score
    signals = {}
    ma5_val = sma(closes, 5)
    ma5_prev = sma(closes, 5, n - 2)
    ma20_val = sma(closes, 20)
    ma20_prev = sma(closes, 20, n - 2)

    signals["MA5_trend"] = "bull" if (ma5_val and ma5_prev and ma5_val > ma5_prev) else "bear"
    signals["MA20_trend"] = "bull" if (ma20_val and ma20_prev and ma20_val > ma20_prev) else "bear"
    signals["price_vs_MA20"] = "bull" if (ma20_val and closes[-1] > ma20_val) else "bear"
    signals["MACD_hist"] = "bull" if hist[-1] > 0 else "bear"
    signals["MACD_direction"] = "bull" if hist[-1] > hist[-2] else "bear"
    signals["RSI14"] = "bull" if rsi14 > 50 else "bear"
    signals["KDJ"] = "bull" if k > d else "bear"
    signals["bollinger_pos"] = "bull" if result.get("bollinger", {}).get("position_pct", 50) > 50 else "bear"

    bull_count = sum(1 for v in signals.values() if v == "bull")
    bear_count = len(signals) - bull_count

    if bull_count >= 6:
        verdict = "bullish"
    elif bull_count >= 4:
        verdict = "slightly_bullish"
    elif bull_count >= 3:
        verdict = "slightly_bearish"
    else:
        verdict = "bearish"

    result["composite_score"] = {
        "bull": bull_count,
        "bear": bear_count,
        "verdict": verdict,
        "signals": signals,
    }

    # Recent stats
    up_days_10 = sum(1 for i in range(-10, 0) if closes[i] > closes[i - 1]) if n > 10 else 0
    result["recent_stats"] = {
        "up_days_10": up_days_10,
        "down_days_10": 10 - up_days_10,
        "high_30d": round(sh, 2),
        "low_30d": round(sl, 2),
    }

    return result


if __name__ == "__main__":
    raw = sys.stdin.read()
    data = json.loads(raw)
    result = analyze(data)
    print(json.dumps(result, ensure_ascii=False, indent=2))
