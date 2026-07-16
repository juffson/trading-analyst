# strategy-kit/ — 内置策略片段库

数据来源：`../../facts-hub/pkg/test/factor_lib.json`（同事仓库里的因子元数据——id、方向、
分组、`prompt_keyword` 触发描述），本目录把其中可以精确翻译成 QuantScript 的部分实现成真正
能跑的策略片段。`facts-hub` 目录本身没有改动，这里是独立的一份翻译+实现。

## 为什么不是完整脚本，而是"片段"

每个片段只存两块：
- `setup`：计算指标用的 Pine 语句（比如 `rsiValue = ta.rsi(close, rsiLength)`）
- `condition`：布尔触发条件（比如 `ta.crossunder(rsiValue, rsiOversold)`）

同一指标的 long/short 版本（如 `rsi_14_long` / `rsi_14_short`）共享同一份 `setup`，由
`strategy_kit.py render` 拼成一个完整的"开仓/平仓"策略。不同片段之间的变量名和 input
参数名在设计时就保证全局唯一（`rsiLength`/`bbLength`/`wprLength`，不是都叫 `length`），
所以任意两个片段也能用 `strategy_kit.py compose` 安全拼在一起，不会互相覆盖变量。

## 已实现的片段（都已用 `longbridge quant run` 实测跑通）

| id | 方向 | 说明 |
|---|---|---|
| `rsi_14_long` / `rsi_14_short` | long/short | RSI 超卖/超买反转 |
| `macd_12_26_9_long` / `_short` | long/short | MACD 金叉/死叉 |
| `bollinger_bands_20_long` / `_short` | long/short | 布林带均值回归（跌破下轨收回/突破上轨回落） |
| `williams_14_long` / `_short` | long/short | 威廉指标超卖/超买反转 |
| `trend_acceleration` | long | MACD 柱连续4日走高 + RSI>50（多头加速） |
| `momentum_exhaustion` | short | MACD 柱连续4日走低 + 衰竭前 RSI 曾>50（多头衰竭，配 `trend_acceleration` 用作退出） |
| `breakout` | long | MACD 柱连续4日走高 + 突破前 RSI 处于40-60震荡区（横盘后突破） |
| `bearish_divergence` | short | 价格突破布林上轨 + RSI 超买（顶部反转预期，配 `breakout` 用作退出） |

**没实现的三个**（`pullback_in_uptrend`/`false_breakout`/`momentum_crash`）：facts-hub 里的
描述涉及"支撑位""量能是否充分"这类需要人工判断的模式识别，没有给出精确的量化定义。
硬翻译成一条自己猜的规则风险很大（看起来像那个因子，实际逻辑对不上），所以先在
`strategy_kit.json` 里标了 `"implemented": false`，等你有更明确的量化标准再补。

## 用法

```bash
# 列出所有片段（含未实现的，标了 [未实现]）
python3 strategy-kit/strategy_kit.py list

# 看某个片段的完整定义（setup/condition/inputs 原始 JSON）
python3 strategy-kit/strategy_kit.py show breakout

# 同名 long/short 配对，生成一份完整策略（这是最常用的形式）
python3 strategy-kit/strategy_kit.py render rsi_14 --out /tmp/rsi_14.pine

# 任意两个片段组合（entry 开仓 / exit 平仓），比如把 breakout 的开仓信号
# 配 bearish_divergence 的平仓信号
python3 strategy-kit/strategy_kit.py compose --entry breakout --exit bearish_divergence --out /tmp/combo.pine
```

生成的 `.pine` 文件可以直接喂给 `quant-backtest`（交互式回测调参）或复制到
`watchlist/<symbol>/strategy.pine`（calibrating 阶段确定下来之后）。`templates/` 目录下
已经存了 4 个同名 long/short 配对的现成模板（`rsi_14.pine`/`macd_12_26_9.pine`/
`bollinger_bands_20.pine`/`williams_14.pine`），组合类片段（`trend_acceleration` 等）
建议按需 `compose` 生成，不预先存文件，避免和 `strategy_kit.json` 的定义脱节。

## 加新片段

编辑 `strategy_kit.json`，在 `blocks` 里加一条 `{name, direction, groups, description,
implemented, inputs, setup, condition}`。**新片段的 input 变量名和 setup 里的局部变量名
必须在整个库里全局唯一**（不要叫 `length`/`value` 这种通用名字），否则和别的片段组合时
会撞名。写完用 `strategy_kit.py render`/`compose` 生成脚本，再实际跑一次
`longbridge quant run` 确认能跑通（语法错误在这个引擎里不会报得很清楚，实测比读文档靠谱）。
