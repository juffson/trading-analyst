---
name: quant-backtest
description: 优先通过 Codex/ChatGPT 的 Longbridge connector/app `quant_run` 工具运行 QuantScript 量化脚本；连接器不可用时回退 Longbridge CLI/OpenAPI。返回回测报告、图表数据和执行事件，支持动态调参、参数敏感性分析。当用户想运行 QuantScript 策略、进行量化回测、获取夏普比率/最大回撤/胜率、动态调整参数对比效果，或需要 plot 输出数据时使用。即使用户只说"跑一下这个策略"、"帮我回测 RSI 策略"、"调一下参数再测"，也应触发。
---

# Quant Backtest Skill

优先通过 Longbridge connector/app 运行 QuantScript，返回完整回测结果。

## Agent 平台兼容与路径

先从当前 `SKILL.md` 的绝对路径解析其所在目录，记为 `SKILL_DIR`。Skill 可能由 Agent 安装器放到平台自己的目录，也可能由用户手动安装；不要猜测固定位置或依赖当前工作目录。本文所有 `scripts/...` 和 `references/...` 路径都相对于 `SKILL_DIR`，执行时使用绝对路径。

## 数据源选择

按以下顺序路由：

1. 当前会话有 `longbridge_quant_run` 时直接调用，参数为 `symbol`、`start`、`end`，可选 `period`、`script`、`input`。`input` 是与 `input.*()` 顺序一致的 JSON 数组字符串。
2. connector/app 不可用、未授权或调用失败时，回退 `scripts/run_script.py`；脚本自动选择 CLI/OpenAPI。
3. 发生回退时简短告知用户原因，不要求已授权 connector 的用户重复配置 API key。

**回退模式控制：**
```bash
LONGBRIDGE_MODE=cli  python3 "$SKILL_DIR/scripts/run_script.py" ...   # 强制 CLI
LONGBRIDGE_MODE=api  python3 "$SKILL_DIR/scripts/run_script.py" ...   # 强制 API
# 不设置: 有 LONGPORT_ACCESS_TOKEN → api，否则 → cli
```

**API 模式 base URL：** 默认 `https://openapi.longbridge.cn`，可通过 `LONGPORT_OPENAPI_HTTP_URL` 覆盖。

`run_script.py` 会自行检测认证模式，不依赖另一个 Skill 的脚本。

## 运行流程

1. 编写 QuantScript 脚本（见下方语法说明）
2. 优先调用 `longbridge_quant_run`；不可用时调用 `scripts/run_script.py`
3. 检查 `code == 0`
4. 解析 `data` 中的三份 JSON 数据
5. 展示结果

### connector/app 调用

调用 `longbridge_quant_run`：

```text
symbol="TSLA.US"
start="2024-01-01"
end="2024-12-31"
period="day"
script="<完整 QuantScript>"
input="[14,70,30]"  # 可选
```

### CLI/OpenAPI 回退示例

```bash
# 从文件运行
python3 scripts/run_script.py \
  --script /tmp/strategy.pine \
  --counter TSLA.US \
  --start 2024-01-01 \
  --end 2024-12-31

# 直接传脚本内容
python3 scripts/run_script.py \
  --script-text '//@version=6
strategy("RSI", overlay=false, initial_capital=100000)
length = input.int(14, title="Length")
rsiValue = ta.rsi(close, length)
if ta.crossunder(rsiValue, 30)
    strategy.entry("Long", strategy.long, qty=100)
if ta.crossover(rsiValue, 70)
    strategy.close("Long")
plot(rsiValue, title="RSI", color=color.purple)' \
  --counter TSLA.US \
  --start 2024-01-01 \
  --end 2024-12-31 \
  --out /tmp/result.json

# 动态调参
python3 scripts/run_script.py \
  --script /tmp/strategy.pine \
  --counter 700.HK \
  --start 2023-01-01 \
  --end 2024-12-31 \
  --inputs "[21, 80, 20]"

# 保存结果并绘图
python3 scripts/run_script.py --script /tmp/s.pine --counter AAPL.US \
  --start 2024-01-01 --end 2024-12-31 --out /tmp/result.json
python3 scripts/chart.py /tmp/result.json --all
```

## 编写 QuantScript

### 必要格式

```pine
//@version=6
strategy("策略名称", overlay=false, initial_capital=100000)

// input 参数可通过 inputs_json 动态覆盖（按声明顺序，从 index 0 开始）
length = input.int(14, title="RSI Length")    // index 0
overbought = input.int(70, title="Overbought") // index 1
oversold = input.int(30, title="Oversold")     // index 2

rsiValue = ta.rsi(close, length)

if ta.crossunder(rsiValue, oversold)
    strategy.entry("Long", strategy.long, qty=100)
if ta.crossover(rsiValue, overbought)
    strategy.close("Long")

plot(rsiValue, title="RSI", color=color.purple)
hline(overbought, color=color.red)
hline(oversold, color=color.green)
```

### 要点
- 第一行必须是 `//@version=6`
- `strategy()` 声明 initial_capital、commission 等（**不**在请求里传）
- `input.*()` 声明可调参数，可在运行时通过 `--inputs` 覆盖
- `plot()` / `plotshape()` / `hline()` 输出会体现在 `chart_json`

## inputs_json 动态调参

按 `input.*()` 声明顺序覆盖，用 `null` 跳过不改的参数：

| `--inputs` | 效果 |
|-----------|------|
| `"[21, 80, 20]"` | length=21, overbought=80, oversold=20 |
| `"[21]"` | 只改 length=21 |
| `"[null, null, 20]"` | 只改 oversold=20 |
| 不传 | 全用脚本默认值 |

## 结果解析与展示

响应 `data` 包含三个 JSON 字符串字段。解析后挑选对用户最有价值的内容展示，不要全部机械输出。

### report_json — 策略报告

只有 `strategy` 脚本才有有意义的报告。

关键字段：
```
performanceAll:
  netProfit / netProfitPercent     净利润
  buyHoldReturn / buyHoldReturnPercent  买入持有对照
  maxDrawdown / maxDrawdownPercent 最大回撤
  sharpeRatio / sortinoRatio       夏普/索提诺比率
  totalClosedTrades                总交易次数
  percentProfitable                胜率
  profitFactor                     盈亏比
  avgWinningTrade / avgLosingTrade 平均盈亏
closedTrades: [{entryPrice, exitPrice, entryTime, exitTime, profit, profitPercent}]
equityCurve: [float]    权益曲线
drawdownCurve: [float]  回撤曲线
buyHoldCurve: [float]   买入持有对照
```

**展示建议：**
- 必展示：netProfit/netProfitPercent、maxDrawdown、sharpeRatio、totalClosedTrades、percentProfitable
- 有交易时展示：profitFactor、avgWin/avgLoss、前几笔 closedTrades
- 多空都有时分别展示 performanceLong / performanceShort
- 指标保留3位小数，金额保留2位小数

### chart_json — 图表输出

`plot()` / `hline()` / `plotshape()` 产生的数据，indicator 脚本的核心输出。

```
series_graphs: {
  "0": { Plot: { title, series: [float], style, ... } }
  ...
}
filled_orders: { "bar_index": [{ order_id, price, quantity }] }  // 正数=买 负数=卖
```

**展示建议：**
- 列出所有 plot 线条的 title、最新值、最大/最小/均值
- filled_orders 按时间顺序列出买卖点

### events_json — 执行事件

默认不展示。有 Alert 事件时提取 message。可告知"共执行 N 个 bar"。

## 终端图表绘制

connector 返回结果优先直接总结；只有用户要求终端图表且已把结果整理为 `chart.py` 支持的 JSON 结构时才使用本地绘图。回退模式先安装依赖：`pip3 install plotext`。

```bash
# 绘制 chart_json 所有 plot 线条（默认）
python3 scripts/chart.py /tmp/result.json

# 权益曲线 + 买入持有对照
python3 scripts/chart.py /tmp/result.json --equity

# 权益曲线 + 标注买卖点
python3 scripts/chart.py /tmp/result.json --trades

# 回撤曲线
python3 scripts/chart.py /tmp/result.json --drawdown

# 全部
python3 scripts/chart.py /tmp/result.json --all

# 只绘制指定线条
python3 scripts/chart.py /tmp/result.json --plots "MACD,Signal"
```

用户不要求绘图时无需执行此步骤。

## 标的代码格式

与 longbridge CLI 保持一致（`CODE.MARKET` 格式）：

| 市场 | 格式 | 示例 |
|------|------|------|
| 美股 | `TICKER.US` | `TSLA.US` |
| 港股 | `CODE.HK` | `700.HK` |
| A股(上交所) | `CODE.SH` | `600519.SH` |
| A股(深交所) | `CODE.SZ` | `000001.SZ` |

## K线周期（--period）

| 值 | 说明 |
|----|------|
| `day` | 日线（默认） |
| `week` | 周线 |
| `month` | 月线 |
| `1h` | 1小时 |
| `30m` / `15m` / `5m` / `1m` | 分钟线 |

## API 接口详情

见 [references/api.md](references/api.md)。
