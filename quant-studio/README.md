# Quant Studio

本地 Rust web 应用，是 `trading-analyst` 下的一个独立子项目（自己的 Cargo 工程，不是
Claude Skill）：管理因子/策略片段库，跑 Longbridge 回测，看图，持续跟踪同一个因子在
不同时间点测出来的表现。跟 `../auto-trader` 是两个独立工具——auto-trader 负责
"研究→回测→模拟交易"的自动化循环，quant-studio 负责"人工浏览因子、跑图、看效果"，
两边共用同一份因子定义（`data/strategy_kit.json` 是从
`../auto-trader/strategy-kit/strategy_kit.json` 复制的种子拷贝，各自维护，暂时靠手动同步）。

## 跑起来

```bash
export LONGPORT_APP_KEY=...
export LONGPORT_APP_SECRET=...
export LONGPORT_ACCESS_TOKEN=...
cargo run
# 浏览器打开 http://127.0.0.1:4870
```

## 架构

- **后端**：`axum` web 服务，直接用官方 `longport` Rust crate 的
  `HttpClient::from_apikey_env()` 认证，POST `/v2/quant/run_script`（`language: 1` = Pine，
  navi 目前没有策略语义，用不了——调研记录见 auto-trader 那边）。字段命名/鉴权和
  `../auto-trader/scripts/signal_from_backtest.py`（Python 版）保持一致。
- **因子库**：`src/factors.rs` 读 `data/strategy_kit.json`，`render_paired`/`compose`
  逻辑照抄 Python 版的 `strategy_kit.py`。**多加了一步**：自动给每个 setup 变量加一行
  `plot()`（Python 版没有，因为 auto-trader 不需要看图，只读 report_json）。
- **图表数据**：`/v2/quant/run_script` 返回的 `events_json` 里每根 bar 自带完整 OHLCV
  （`barStart.candlestick`），直接当真实K线用，不用额外接 QuoteContext/WebSocket。
  `chart_json.seriesGraphs` 给指标线数值（按 bar_index 排列，不带时间，要用 events 里的
  bar 时间对齐）。**这些字段名都是实测出来的，不是文档写的那样**——比如是驼峰
  `barStart`/`barIndex` 不是文档暗示的 `BarStart`/`bar_index`，`chart_json.filledOrders`
  不是 `filled_orders`。改这块代码前建议先重新拿真实响应核对一遍，这个接口的字段命名不稳。
- **前端**：`static/index.html`，vanilla JS + `lightweight-charts@4.1.3`（CDN），侧边栏导航
  六个页面（因子库 / 回测 / 模拟交易 / 实盘交易 / 公司分析 / 交易记录分析），不是单页平铺——
  因子库负责选因子，回测页负责跑图看结果：
  - **模拟交易**：读 `../auto-trader/config/strategies.json` + `watchlist/*/{plans.jsonl,
    stage.json, backtest_report.json, trade_log.jsonl}` + `state/*.json`（本机文件，同一台
    机器直接读，不走 HTTP）——待处理信号统计、状态分布、每个标的的阶段流转历史、calibrating
    定的回测基线、模拟成交记录，跟 auto-trader 自己的 `render_dashboard.py` 是同一套数据源。
    auto-trader 还没跑过就是空的，不是 bug。
  - **实盘交易**：直接调 `longport::trade::TradeContext::stock_positions()` /
    `today_orders()` 查 Longbridge 账户当前真实持仓和今日委托——auto-trader 里
    `executor.confirm_order()` 只能手动调，没有自动路径产生"实盘成交记录"这种本地数据可读，
    所以换成直接问券商账户，这样"实盘交易"才是真实定义，不看是谁下的单。
  - **公司分析 / 交易记录分析**：把 `../company-deep-dive` / `../trading-analyst` 这两个
    Claude Skill 接进页面——但**不会**在后台无人值守跑 `claude -p`。这两个 skill 要跑起来需要
    Bash/网络/子 agent 权限，没有 TTY 就没法逐条确认，唯一能不卡住的办法是
    `--permission-mode bypassPermissions`（完全绕过审批），让一个点了页面按钮就能触发的进程
    完全免确认地跑最多 30 分钟、花掉真金白银的 API 调用——这个口子太大，所以设计成"生成命令，
    人工跑"：页面拼好完整的 `claude -p "..."` 命令（`prompt` 里直接把 SKILL.md 的绝对路径写清楚，
    让 claude 自己读文件按流程执行，不依赖 skill 自动发现/匹配——这两个 skill 没有装进
    `~/.claude/skills/`），你自己复制到终端里跑、交互式确认每一步，跑完回页面点"检查是否已完成"，
    从磁盘上找 `deep_dive.html`/`review.html` 展示。任务状态和历史记录都是文件系统驱动
    （`data/company-analysis/<slug>/`、`data/trade-review/<slug>/`），没有内存态、没有后台进程。
    交易记录分析会把当前模拟交易 + 实盘账户记录（复用上面两个页面的数据源）整理成
    `records.json` 喂给 skill，让它做"无基线复盘"（没有走 skill 自己 `plan_io.py` 存的历史
    plan）。**这两个页面都只读、只生成命令，没有下单入口、没有自动执行入口。**
- **持续跟踪效果**：每次跑回测都会往 `data/performance/<factor_key>.jsonl` 追加一条
  `{ts, symbol, metrics}` 快照——同一个因子在不同时间测，能看出表现有没有漂移。

## 已知限制 / 没做的事

- **没有视觉验证**：这个环境里没有浏览器，前端 JS 只做了"接口返回的数据结构对不对"的验证
  （用 curl 核对过 `/api/backtest` 的 `candles`/`bar_times`/`chart.seriesGraphs` 都能对齐），
  没有真人眼看过图表渲染出来是什么样——排查 UI 问题时从这个假设开始。
- 因子库是从 auto-trader 复制的种子拷贝，两边手动同步，没有做自动同步机制。
- 没做鉴权/多用户——就是本机单人用的本地工具，`127.0.0.1` 绑定，别暴露到公网。
- Navi 脚本语言暂不支持（`language: 0`）——它没有 `strategy.entry`/`strategy.close` 语义，
  接不上现在这套"因子=开仓条件+平仓条件"的模型。
