# watchlist/ — 每个标的的跨阶段共享状态

除本 README 外，本目录的运行时数据不提交到 git（见 `../../.gitignore`）；系统会按需创建 `watchlist/<SYMBOL>/`。

```
watchlist/<SYMBOL>/
  stage.json            当前阶段 + 流转历史（由 pipeline/advance_stage.py 维护）
  deep_dive.json        company-deep-dive 产出的精简摘要（供 advance_stage.py 判定用）
  deep_dive.html        company-deep-dive 完整决策仪表盘
  strategy.pine         quant-backtest 选中的 QuantScript，原样喂给 signal_from_backtest.py
  backtest_report.json  quant-backtest 的关键回测指标（校准阶段的基线，供后续复盘对比）
  plans.jsonl           run_cycle.py 每次发现新信号都会写一条交易计划（见下方「交易计划」）
  trade_log.jsonl       execution_mode=auto_paper 时，实际预览下单后追加的逐笔记录
  review_<date>.json    trading-analyst 模式5 复盘产出的结构化数据
  review_<date>.html    trading-analyst 模式5 复盘报告（人读）
```

## 交易计划（`plans.jsonl`）

数据模型参考同级仓库 `signal-hub`（不在本仓库内）的 Signal 结构（`id` / `status` / `symbol` /
`strategy_name` / `created_at`·`updated_at` / `summary`），但不是它的 Kafka/gRPC/状态机——
这里只是本地 JSONL，一个标的一份，追加写入。每次 `run_cycle.py` 发现新信号，**不管
`execution_mode` 是什么**都会先生成一条 `status: "pending"` 的计划，代码见
`../scripts/plan_store.py`。

```json
{"event": "plan_created", "plan": {
  "id": "plan_TSLA.US_1752570000000",
  "symbol": "TSLA.US",
  "strategy_name": "rsi_example",
  "side": "buy",
  "qty": 100,
  "price": 187.23,
  "signal_key": "2026-07-15:TSLA.US:buy:100:187.23",
  "status": "pending",
  "summary": "rsi_example 触发 buy 信号：qty=100 price=187.23",
  "created_at": "2026-07-15T13:30:00+00:00",
  "updated_at": "2026-07-15T13:30:00+00:00"
}}
```

状态流转（对应 signal-hub 的 pending/active/filter_by_manual，改成交易场景）:

| status | 含义 |
|---|---|
| `pending` | 已生成，等待处理。`execution_mode: signal_only`（默认）时永远停在这一步——你自己看 `plans.jsonl` 决定要不要手动下单 |
| `rejected` | `execution_mode: auto_paper` 时被 `risk_guard` 拦下（对应 signal-hub 的 `filter_by_manual`） |
| `executed` | `execution_mode: auto_paper` 时已调用 `executor.place_order()` 预览下单（仍然只是 `--dry-run`，不是真实成交） |

按 `id` 去重只保留每个计划最新一条记录，用 `plan_store.load_latest_plans(symbol)`。

## 各 JSON 的最小字段约定

这些字段是 `pipeline/advance_stage.py` 判断进阶用的最小集合——headless 触发对应 skill 时，
要在 prompt 里明确要求它额外写出这份精简 JSON（skill 本身的完整产出格式不变）。

### `stage.json`

```json
{
  "symbol": "TSLA.US",
  "stage": "researching",
  "history": [
    {"stage": "researching", "entered_at": "2026-07-15T09:00:00+08:00", "reason": "手动加入"}
  ]
}
```

### `deep_dive.json`

```json
{
  "symbol": "TSLA.US",
  "score_total": 42,
  "score_max": 55,
  "verdict": "优质公司等买点",
  "buy_price_low": 180.0,
  "buy_price_high": 210.0,
  "generated_at": "2026-07-15T09:30:00+08:00"
}
```

### `backtest_report.json`

```json
{
  "symbol": "TSLA.US",
  "strategy_file": "strategy.pine",
  "inputs": "[14,70,30]",
  "sharpe_ratio": 1.35,
  "max_drawdown_pct": 12.4,
  "win_rate_pct": 58.0,
  "total_trades": 24,
  "generated_at": "2026-07-15T10:00:00+08:00"
}
```

### `review_<date>.json`

在 `trading-analyst` 自己的 Review JSON schema（见 `../../../skills/trading-analyst/references/plan-schema.md`）之外，
额外加一个顶层 `verdict` 字段，供 `advance_stage.py` 自动判断：

```json
{
  "verdict": "on_track"
}
```

`verdict` 取值：`on_track`（符合预期，继续跑）/ `watch`（需要留意，暂不改变阶段）/ `off_track`（明显走样，退回 calibrating 重新调参）。
