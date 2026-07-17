# Auto Trader — 研究 → 回测 → 模拟交易 → 复盘 的循环

把 `../company-deep-dive`、`../quant-backtest`、`../trading-analyst` 三个 Skill 串成一个持续运转的循环，
用规则型策略（不是每轮都靠 LLM 判断）驱动模拟交易的执行决策。**这三个 Skill 目录本身不做任何修改**——
本目录只依赖它们已有的脚本和输出格式，通过 `pipeline/README.md` 里约定的方式调用。

## 循环

```
researching → calibrating → paper_trading ⇄ (复盘，周期性) → [人工确认后] promoted_live
```

详见 `pipeline/README.md`（状态机、进阶门槛、当前的 skill 调用方式）。

## 目录（按功能拆分）

| 目录 | 职责 |
|---|---|
| `config/` | 每个标的的策略绑定 + 风控参数 + `execution_mode` 开关。`strategies.json` 是你本地的真实配置（不提交），`strategies.example.json` 是模板 |
| `watchlist/` | 每个标的一份，跨阶段共享状态：深度分析摘要、策略脚本、回测基线、模拟成交记录、复盘记录（不提交，见下方隐私说明） |
| `pipeline/` | 阶段判定逻辑（`advance_stage.py`）+ 各阶段当前的 skill 调用约定 |
| `strategy-kit/` | 内置策略片段库（翻译自 `../facts-hub` 的因子定义），`strategy_kit.py` 提供 list/show/render/compose——calibrating 阶段优先从这里选现成片段，而不是每次现场重新写策略 |
| `scripts/` | cron 实际执行的机械层：拉信号 → 风控 → 执行 → 记录。**不含 LLM 调用** |
| `scheduling/` | cron / launchd 配置示例 |
| `state/`、`logs/` | 运行时产生，不提交 |

## 隐私说明（重要）

这个仓库会 push 到 `github.com/juffson/trading-analyst`，可能被其他人当 Skill 安装。
`watchlist/`、`state/`、`logs/` 和你本地的真实 `config/strategies.json` 都包含具体标的、
持仓、模拟成交这些个人数据，已经在 `.gitignore` 里排除，不会被提交。如果你想追踪某些内容
（比如想把某只标的的深度分析结果存档到仓库里），自己手动 `git add -f` 加进去。

## 安全默认值

- **`execution_mode` 是三级开关，默认 `signal_only`**（参考 `../signal-hub` 的 Signal
  `status` 字段思路——先落一份 `status: pending` 的交易计划，执行与否分开判断）:
  - `disabled` — 跳过该标的，连回测都不跑
  - `signal_only`（默认）— 只把信号写成 `watchlist/<symbol>/plans.jsonl` 里一条
    `status: pending` 的交易计划，不碰风控、不下单。你自己看计划决定要不要手动操作
  - `auto_paper` — 在此基础上继续走 `risk_guard` 风控 → `executor` 预览下单（仍然只是
    `--dry-run`，不是真实成交）
  - **没有 `auto_live`**——这个开关无法选出真实下单，`run_cycle.py` 遇到未知值会直接拒绝该标的并报错，
    不会当成"更激进的模式"去猜着执行
- `scripts/executor.py` 里 `place_order()`（预览）和 `confirm_order()`（真实下单）是分开的两个函数，
  `run_cycle.py` 里没有任何代码路径调用 `confirm_order()`——要真实下单只能自己手动调用。
- 所有风控检查（仓位上限 / 单日下单次数上限 / 当日亏损熔断）都在 `scripts/risk_guard.py`，只有
  `execution_mode: auto_paper` 才会跑到这一步，任何一项不满足就把计划标成 `rejected`，不会下单。

## 运行

```bash
# 首次使用：从模板建一份真实配置
cp auto-trader/config/strategies.example.json auto-trader/config/strategies.json
# 编辑 strategies.json，填入 calibrating 阶段产出的 strategy_file / inputs

# 手动跑一轮（测试用）
python3 auto-trader/scripts/run_cycle.py

# 只跑某个标的
python3 auto-trader/scripts/run_cycle.py --symbol TSLA.US

# 生成本地监控 dashboard（深色主题，dashboard.html，不提交/不分享，浏览器直接打开看）
python3 auto-trader/scripts/render_dashboard.py
```

定时调度见 `scheduling/crontab.example`（Linux/cron）或 `scheduling/com.user.autotrader.plist.example`（macOS launchd）——
这两份都只是示例文件，需要你手动改路径、手动安装（`crontab -e` 或 `launchctl load`），不会自动生效。

## 依赖

- Python 3.10+ + `pip install longport`（官方 OpenAPI SDK），且 `LONGPORT_APP_KEY` /
  `LONGPORT_APP_SECRET` / `LONGPORT_ACCESS_TOKEN` 已配置。`scripts/signal_from_backtest.py`
  用 `longport.openapi.HttpClient` 直接 POST `/v1/quant/run_script`，**不需要额外安装
  `longbridge-terminal` CLI 二进制**——纯 Python 依赖，部署更简单（不用操心 cron/launchd 的
  PATH），也不经过 `../quant-backtest/scripts/run_script.py`（原因见该文件顶部注释——
  那条路径实测有解析 bug）
- `scripts/executor.py` 依赖 `../trading-analyst/scripts/lb_client.py` 能正常工作（下单预览用）

## 已知局限（v1 骨架，先跑通再补）

- `scripts/risk_guard.py` 的当日亏损熔断需要 `state` 里有 mark-to-market 的 `realized_pnl_pct`，
  目前骨架里这个字段不会自动算——先占位，后续要接实时报价才能算准。
- `scripts/report.py` 目前只把运行事件写成结构化日志（`logs/YYYY-MM-DD.jsonl`）。后续会替换成
  直接调 Claude API 生成播报/异常解释，不再经过 headless `claude -p`。
- `/v1/quant/run_script` 返回的 `chart_json.filledOrders` 只有 bar_index，没有具体日期，
  所以信号判断仍然用 `report_json.closedTrades`/`openTrades`（带精确的 entryTime/exitTime）。
