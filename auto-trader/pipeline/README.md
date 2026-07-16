# pipeline/ — 标的生命周期状态机

管理每只标的从"研究"到"稳定模拟交易"的完整流转，聚合 `../../company-deep-dive`、
`../../quant-backtest`、`../../trading-analyst` 三个 Skill 的产出。**不修改这三个 Skill 目录本身**——
本目录只依赖它们已有的脚本和输出格式，通过下面约定的方式触发。

## 阶段

| 阶段 | 当前触发方式 | 产出 | 进阶条件 |
|---|---|---|---|
| `researching` | headless 调用 `company-deep-dive` | `watchlist/<symbol>/deep_dive.json` + `.html` | 评分比例（`score_total/score_max`）≥ `DEEP_DIVE_MIN_SCORE_RATIO`（见 `advance_stage.py` 顶部常量） |
| `calibrating` | headless 调用 `quant-backtest` | `watchlist/<symbol>/strategy.pine` + `backtest_report.json` | 夏普比率、最大回撤、胜率同时达标 |
| `paper_trading` | `../scripts/run_cycle.py`（cron，规则策略，不调 LLM） | `watchlist/<symbol>/trade_log.jsonl` | 无自动进阶——由周期性复盘决定去留 |
| （复盘，周期性，发生在 `paper_trading` 期间） | headless 调用 `trading-analyst` 模式5 | `watchlist/<symbol>/review_<date>.json` | 连续 N 次 `verdict: on_track` → 标记 `ready_for_live_promotion`；出现 `off_track` → 退回 `calibrating` |
| `promoted_live` | **人工手动执行，本仓库不提供自动化** | — | — |

进阶门槛的具体数字都是保守默认值，在 `advance_stage.py` 顶部常量里，按需调整。

## 当前的 skill 调用方式：headless Claude Code

这是过渡方案——用 `claude -p "..."`（headless 模式）触发已有的 Skill，Skill 本身不用改。
之后计划替换成直接调 Claude API 写专门的 agent/服务，不再依赖 Claude Code CLI（见各 Skill 目录里
`references/` 下的说明和 `../README.md` 的"已知局限"）。

```bash
# researching：触发 company-deep-dive，同时要求补充一份给 pipeline 读的精简摘要
claude -p "使用 company-deep-dive skill 深度研究 ${SYMBOL}。
完整仪表盘照常存 auto-trader/watchlist/${SYMBOL}/deep_dive.html；
另外在 auto-trader/watchlist/${SYMBOL}/deep_dive.json 写一份精简摘要，字段:
symbol, score_total, score_max, verdict, buy_price_low, buy_price_high, generated_at"

# calibrating：先看 auto-trader/strategy-kit/ 内置策略片段库有没有现成的，没有再让 quant-backtest 现场写
claude -p "先看 auto-trader/strategy-kit/strategy_kit.json（跑
python3 auto-trader/strategy-kit/strategy_kit.py list 看可用片段），能用内置片段
（单个 render 或者两个 compose）就优先用，用 auto-trader/strategy-kit/strategy_kit.py
render/compose 生成 QuantScript，不用自己现场写。
只有内置片段都不合适时才用 quant-backtest skill 现场写一个新策略（可尝试均线/RSI/MACD 等，
可以多试几组参数对比夏普比率和最大回撤）。用 quant-backtest 对 ${SYMBOL} 跑一遍选定的脚本，
确认达标后把 QuantScript 存到 auto-trader/watchlist/${SYMBOL}/strategy.pine；把关键回测指标存到
auto-trader/watchlist/${SYMBOL}/backtest_report.json，字段:
symbol, strategy_file, inputs, sharpe_ratio, max_drawdown_pct, win_rate_pct, total_trades, generated_at"

# 复盘（周期性，比如每周跑一次）：触发 trading-analyst 模式5，对比模拟交易记录
claude -p "使用 trading-analyst skill 对 ${SYMBOL} 做跟进复盘。
模拟持仓/成交记录在 auto-trader/watchlist/${SYMBOL}/trade_log.jsonl，
策略基线在 auto-trader/watchlist/${SYMBOL}/backtest_report.json。
复盘产出正常存 review_<date>.html 和完整 JSON；
另外在 review_<date>.json 顶层加一个 verdict 字段（on_track | watch | off_track），
供 pipeline/advance_stage.py 自动判断是否需要退回重新调参，或者可以考虑转实盘。"
```

## 判定阶段进阶

```bash
python3 pipeline/advance_stage.py TSLA.US
```

这个脚本只读文件、只判断、只更新 `stage.json`——**不会**自己触发下一阶段的 skill 调用，
也**不会**自动转实盘。转实盘永远是你自己看到 `ready_for_live_promotion: true` 之后手动决定的事。
