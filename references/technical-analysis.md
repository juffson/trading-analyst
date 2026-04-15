# 技术指标计算方法参考

## 均线 (Moving Average)

```python
def sma(arr, period, idx=None):
    """简单移动平均"""
    if idx is None: idx = len(arr) - 1
    if idx < period - 1: return None
    return sum(arr[idx-period+1:idx+1]) / period
```

标准周期: MA5, MA10, MA20, MA30, MA60

判断:
- 多头排列: MA5 > MA10 > MA20 > MA60，价格在均线上方
- 空头排列: MA60 > MA5 > MA10 > MA20，价格在均线下方
- 均线方向: 当前 MA 值与前一日比较，上升为多，下降为空

## MACD

```python
def ema_list(arr, period):
    """指数移动平均"""
    ema = [arr[0]]
    k = 2 / (period + 1)
    for i in range(1, len(arr)):
        ema.append(arr[i] * k + ema[-1] * (1 - k))
    return ema

ema12 = ema_list(closes, 12)
ema26 = ema_list(closes, 26)
dif = [ema12[i] - ema26[i] for i in range(n)]
dea = ema_list(dif, 9)
macd_hist = [(dif[i] - dea[i]) * 2 for i in range(n)]
```

判断:
- DIF > DEA: 多头 (金叉发生时为买入信号)
- DIF < DEA: 空头 (死叉发生时为卖出信号)
- DIF > 0: 中期偏多; DIF < 0: 中期偏空
- 红柱缩短: 多头动能衰减; 绿柱缩短: 空头动能衰减

## RSI

```python
def calc_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))
```

标准周期: RSI6 (短线), RSI14 (中线)

判断:
- < 20: 严重超卖 | < 30: 超卖 | < 40: 偏弱
- 40-60: 中性
- > 60: 偏强 | > 70: 超买 | > 80: 严重超买

## KDJ

```python
def calc_kdj(highs, lows, closes, n=9):
    k_values, d_values = [50], [50]
    for i in range(n-1, len(closes)):
        period_high = max(highs[i-n+1:i+1])
        period_low = min(lows[i-n+1:i+1])
        rsv = (closes[i] - period_low) / (period_high - period_low) * 100 if period_high != period_low else 50
        k = 2/3 * k_values[-1] + 1/3 * rsv
        d = 2/3 * d_values[-1] + 1/3 * k
        k_values.append(k)
        d_values.append(d)
    j = 3 * k_values[-1] - 2 * d_values[-1]
    return k_values[-1], d_values[-1], j
```

判断:
- J < 0: 极度超卖，反弹概率大
- K < 20 且 D < 20: 超卖区
- K > D: 短线偏多 (金叉为买入信号)
- K < D: 短线偏空 (死叉为卖出信号)
- K > 80 且 D > 80: 超买区

## 布林带 (Bollinger Bands)

```python
def calc_boll(closes, period=20, num_std=2):
    recent = closes[-period:]
    mid = sum(recent) / period
    std = (sum((x - mid)**2 for x in recent) / period) ** 0.5
    upper = mid + num_std * std
    lower = mid - num_std * std
    position = (closes[-1] - lower) / (upper - lower) * 100  # 0-100%
    width = (upper - lower) / mid * 100  # 带宽百分比
    return upper, mid, lower, position, width
```

判断:
- 位置 > 80%: 接近上轨，短线压力
- 位置 < 20%: 接近下轨，短线支撑
- 带宽 < 10%: 收口，变盘临近
- 带宽 > 20%: 张口，波动加大

## ATR (Average True Range)

```python
def calc_atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period
```

用途: ATR/2 约为做T日内单边波动空间参考

## 斐波那契回撤

```python
swing_high = max(highs[-30:])   # 近30日最高
swing_low = min(lows[-30:])     # 近30日最低
fib_range = swing_high - swing_low

levels = {
    '0%': swing_low,
    '23.6%': swing_low + fib_range * 0.236,
    '38.2%': swing_low + fib_range * 0.382,
    '50%': swing_low + fib_range * 0.5,
    '61.8%': swing_low + fib_range * 0.618,
    '78.6%': swing_low + fib_range * 0.786,
    '100%': swing_high,
}
```

38.2% 和 61.8% 是最重要的回撤位，经常与均线支撑/阻力重合时信号更强。

## 筹码密集区 (成交量加权)

```python
from collections import defaultdict
price_vol = defaultdict(int)
for i in range(-30, 0):
    bucket = round(closes[i])  # 按整数价位分桶
    price_vol[bucket] += volumes[i]

# 按成交量排序，取前5-8个价位
dense_zones = sorted(price_vol.items(), key=lambda x: -x[1])[:8]
```

成交量最大的价位区间是筹码密集区，反弹到此处面临解套抛压。

## 综合评分

选取 8 个独立信号进行多空投票:

| 信号 | 多 | 空 |
|------|----|----|
| MA5趋势 | MA5 上升 | MA5 下降 |
| MA20趋势 | MA20 上升 | MA20 下降 |
| 价格 vs MA20 | 价格 > MA20 | 价格 < MA20 |
| MACD柱 | 红柱 | 绿柱 |
| MACD方向 | 柱状放大 | 柱状缩小 |
| RSI14 | > 50 | < 50 |
| KDJ | K > D | K < D |
| 布林位置 | > 50% | < 50% |

- 6多以上: 偏多
- 4-5多: 中性偏多/中性偏空
- 2多以下: 偏空
