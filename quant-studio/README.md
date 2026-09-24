# Quant Lab（quant-studio）

本地 Rust web 应用 + Python 自动化运行器，是本仓库根下的一个独立子项目（自己的 Cargo 工程，不是
Claude Skill）。UI 按 LBUS 5.0 暗色设计系统（Quant Lab / 量化研究工作台）呈现，功能保持不变：
管理因子/策略片段库，跑 Longbridge 回测，看图，持续跟踪同一个因子在不同时间点测出来的表现；
`auto-trader/` 已收进本目录，负责「研究→回测→模拟交易」的 cron 循环。和 `../skills/` 是
工具 vs Skill 的关系——工具读 Skill 的脚本/输出格式，不修改 Skill 目录。

## 目录

```
quant-studio/
├── src/                 Rust web 服务（axum + longport）
├── static/              前端（vanilla JS + lightweight-charts）
├── data/                运行时产出（performance / company-analysis / trade-review，不提交）
└── auto-trader/         自动化运行器（Python，可单独 cron）
    ├── scripts/           cron 入口：信号 → 计划 → 风控 → 预览下单
    ├── pipeline/          阶段状态机（researching→calibrating→paper_trading）
    ├── strategy-kit/      因子库唯一源 strategy_kit.json + Python CLI
    ├── config/            标的配置（strategies.json 不提交）
    └── scheduling/        cron / launchd 示例
```

## 配置自己的 Longbridge 账号

仓库不含任何真实凭据。首次使用需要自己申请并配置，**不要**把真实 key/token 写进仓库任何文件
（`.env` 和 `auto-trader/config/strategies.json` 已在 `.gitignore`）。

1. **注册 / 登录 Longbridge 账户**（已开户则跳过），确保有要分析的市场行情权限。
2. **申请 OpenAPI**：打开 [Longbridge 开发者平台](https://open.longbridge.com/docs)（open.longbridge.com），
   按 Getting Started 创建应用、申请 OpenAPI 权限，拿到 `App Key` + `App Secret`。
   行情（Quote）和交易（Trade）权限是**分开申请**的：只跑回测/看行情开 Quote 就够；
   要用「实盘交易」页查持仓/委托，或 auto-trader 预览下单，再开 Trade。
3. **生成 Access Token**：在开发者平台按文档用 App Key/Secret 换取 `Access Token`
   （JWT，会过期，过期后重新生成再更新环境变量）。
4. **写进本机环境**（二选一）：

   ```bash
   # 方式 A：写进 shell 配置（~/.zshrc 等）
   export LONGPORT_APP_KEY="你的_app_key"
   export LONGPORT_APP_SECRET="你的_app_secret"
   export LONGPORT_ACCESS_TOKEN="你的_access_token"
   # 国际站域名；本机解析不了 openapi.longport.cn 时必设
   export LONGPORT_HTTP_URL="https://openapi.longportapp.com"

   # 方式 B：项目下的 .env（不提交），每次手动 source 或写进启动脚本
   cat > .env <<'EOF'
   export LONGPORT_APP_KEY="你的_app_key"
   export LONGPORT_APP_SECRET="你的_app_secret"
   export LONGPORT_ACCESS_TOKEN="你的_access_token"
   export LONGPORT_HTTP_URL="https://openapi.longportapp.com"
   EOF
   source .env
   ```

5. **验证**：

   ```bash
   python3 ../skills/trading-analyst/scripts/lb_client.py detect
   # active_mode 应为 api；然后 cargo run，打开页面跑一次回测
   ```

凭证问题排查见 [`../skills/trading-analyst/references/longbridge-api.md`](../skills/trading-analyst/references/longbridge-api.md)。

## 跑起来

```bash
# 先完成上面的账号配置
cargo run
# 浏览器打开 http://127.0.0.1:4870
```

自动化循环（可选，独立 cron）：

```bash
cp auto-trader/config/strategies.example.json auto-trader/config/strategies.json
python3 auto-trader/scripts/run_cycle.py
python3 auto-trader/scripts/render_dashboard.py
```

## 架构

- **后端**：`axum` web 服务，直接用官方 `longport` Rust crate 的
  `HttpClient::from_apikey_env()` 认证，POST `/v2/quant/run_script`（`language: 1` = Pine，
  navi 目前没有策略语义，用不了——调研记录见 `auto-trader/`）。字段命名/鉴权和
  `auto-trader/scripts/signal_from_backtest.py`（Python 版）保持一致。
- **因子库**：全仓唯一一份 `auto-trader/strategy-kit/strategy_kit.json`。
  `src/factors.rs` 的 `render_paired`/`compose` 逻辑和 Python CLI `strategy_kit.py` 一致。
  **多加了一步**：自动给每个 setup 变量加一行 `plot()`（Python 版没有，因为 auto-trader
  不需要看图，只读 report_json）。
- **图表数据**：`/v2/quant/run_script` 返回的 `events_json` 里每根 bar 自带完整 OHLCV
  （`barStart.candlestick`），直接当真实K线用，不用额外接 QuoteContext/WebSocket。
  `chart_json.seriesGraphs` 给指标线数值（按 bar_index 排列，不带时间，要用 events 里的
  bar 时间对齐）。**这些字段名都是实测出来的，不是文档写的那样**——比如是驼峰
  `barStart`/`barIndex` 不是文档暗示的 `BarStart`/`bar_index`，`chart_json.filledOrders`
  不是 `filled_orders`。改这块代码前建议先重新拿真实响应核对一遍，这个接口的字段命名不稳。
- **`report_json.dailyReturns` 不是"每日收益率"**（踩过一次，记在这）：它是**累计已实现
  盈亏 %** 的阶梯序列，只在有平仓/开仓的那天写值、其余全是 0，而且同一个值会在平仓日和
  下一次开仓日各出现一次。实测 AAPL.US 两年那次 632 条里只有 12 条非 0，值正好是
  `closedTrades[].cumulativeProfitPercent`，最后一个等于 `performanceAll.netProfitPercent`。
  当日收益率去求和/复利会算出 55% 这种离谱数字（真实 7.695%）。**月度收益一律从
  `equityCurve` 折算**（逐 bar 真实净值，含浮动盈亏，长度和 candles 一致）。
- **前端**：`static/index.html`，vanilla JS + `lightweight-charts@4.1.3`（CDN），侧边栏导航
  六个页面（因子库 / 回测 / 模拟交易 / 实盘交易 / 公司分析 / 交易记录分析）。视觉套用
  LBUS 5.0 暗色 token（`--lb-*`，品牌色 mint `#00F0C4`，绿涨红跌），信息架构保持原有功能：
  - **回测页**：左边 sticky 参数面板，右边结果区。6 个头条指标 tile **不进 tab**（切到哪一页
    都要能看见这次跑出来是好是坏），下面是二级 tab，一次只显示一块——东西全纵向堆在一页
    会把价格图挤到首屏外面去：
    - **图表**（默认）——价格/买卖点、净值·回撤·买入持有、指标线三张图
    - **全部指标**——`performanceAll`/`Long`/`Short` 三栏对照，按「收益 / 风险 / 交易」分成
      3 张小表、宽屏并排两列；38 个字段铺了 31 个，剩下 7 个是已展示百分比的货币版孪生
    - **成交明细**——`closedTrades`+`openTrades` 逐笔表，点表头排序（空值恒排最后），
      含最大浮盈/浮亏和持仓 bar 数；tab 上带笔数
    - **月度收益**——一行一年、12 列月份 + 全年合计的热力图，色阶按本次最大绝对值归一
    - **回测历史**——曲线可切 8 个指标（Sharpe / Sortino / 净利 / 回撤 / 胜率 /
      Profit Factor / 平均每笔 / 买入持有）+ 历史明细表
    - **脚本**——这次发给 `/v2/quant/run_script` 的完整 QuantScript 原文

    切 tab 时要重新量图表宽度：lightweight-charts 在 `display:none` 的容器里量到的宽度是 0，
    画出来是一条缝。同理 ResizeObserver 挂在一直可见的结果区上，**不能**挂图表自己的卡片——
    卡片在没激活的 tab 里宽度是 0，一次回调就能把所有图压成 0 宽再也回不来。
  - **模拟交易**：读 `auto-trader/config/strategies.json` + `watchlist/*/{plans.jsonl,
    stage.json, backtest_report.json, trade_log.jsonl}` + `state/*.json`（本机文件，同一台
    机器直接读，不走 HTTP）——待处理信号统计、状态分布、每个标的的阶段流转历史、calibrating
    定的回测基线、模拟成交记录，跟 auto-trader 自己的 `render_dashboard.py` 是同一套数据源。
    auto-trader 还没跑过就是空的，不是 bug。
  - **实盘交易**：直接调 `longport::trade::TradeContext::stock_positions()` /
    `today_orders()` 查 Longbridge 账户当前真实持仓和今日委托——auto-trader 里
    `executor.confirm_order()` 只能手动调，没有自动路径产生"实盘成交记录"这种本地数据可读，
    所以换成直接问券商账户，这样"实盘交易"才是真实定义，不看是谁下的单。
  - **公司分析 / 交易记录分析**：把 `../skills/company-deep-dive` / `../skills/trading-analyst` 这两个
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
  `metrics` 现在记 10 个字段（原本 6 个，后加了 sortino / profit_factor / avg_trade_pct /
  buy_hold_return_pct）。**加字段只能往后追加，不能改名或删旧的**：老快照里没有新字段，
  前端按 null 显示 `—`，那条历史不会崩但会断在这个指标上。
- **`factor_key` 是配对的规范 key**：固定取 long 那一边、去掉 `_long` 后缀。`breakout` 和
  `bearish_divergence` 是一对，从哪一边点进去都落到 `breakout.jsonl`，历史才不会分家。

## 已知限制 / 没做的事

- **没有视觉验证**：开发环境里没有浏览器时只验了数据层；本机有浏览器后可打开
  `http://127.0.0.1:4870` 验收。UI 是「保留现有功能只换皮」——套了 LBUS 视觉，
  **没有**做成设计稿里那套 IC 质量监控 / 新建因子 / 发起回测任务流（那是另一套信息架构）。
- **`longbridge` CLI 也能跑同一个接口**：`longbridge quant run <SYMBOL> --start .. --end .. --script ..
  --format json` 返回的就是同样的 `report_json`/`events_json`/`chart_json` 三件套（实测
  0.24.0 版；它默认不带 chart，`chart_json` 是空串）。核对字段命名、或者不想开服务就想看一眼
  某个脚本的回测结果时，用它比写 curl 快。
- 没做鉴权/多用户——就是本机单人用的本地工具，`127.0.0.1` 绑定，别暴露到公网。
- Navi 脚本语言暂不支持（`language: 0`）——它没有 `strategy.entry`/`strategy.close` 语义，
  接不上现在这套"因子=开仓条件+平仓条件"的模型。
- auto-trader 侧的已知局限见 `auto-trader/README.md`（当日亏损熔断的 mark-to-market 等）。
