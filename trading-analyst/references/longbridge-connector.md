# Longbridge connector/app 工具映射

Codex 或 ChatGPT 暴露 Longbridge 工具时，直接调用工具，不经 shell 包装，也不要求用户提供 LongPort API 密钥。工具在客户端里可能显示为 `longbridge_quote`，底层全名可能带 MCP 命名空间；按语义匹配即可。

## 核心读取工具

| 需求 | 工具 | 关键参数 |
|---|---|---|
| 实时报价 | `longbridge_quote` | `symbols: ["AAPL.US"]` |
| PE/PB/市值/换手 | `longbridge_calc_indexes` | `symbols`; `indexes` 可省略 |
| K 线 | `longbridge_candlesticks` | `symbol`, `period`, `count`, `trade_sessions` |
| 资金分布/流向 | `longbridge_capital_distribution`, `longbridge_capital_flow` | `symbol` |
| 静态证券信息 | `longbridge_static_info` | `symbols` |
| 持仓/现金 | `longbridge_stock_positions`, `longbridge_account_balance` | 无参数；余额可按 `currency` 过滤 |
| 今日订单/成交 | `longbridge_today_orders`, `longbridge_today_executions` | 可按 `symbol` 过滤 |
| 历史订单/成交 | `longbridge_history_orders`, `longbridge_history_executions` | `start_at`, `end_at` 使用 RFC3339 |
| 机构评级/EPS 预测 | `longbridge_institution_rating`, `longbridge_forecast_eps` | `symbol` |
| 个股资讯 | `longbridge_news` | `symbol` |
| 资讯搜索 | `longbridge_news_search` | `keyword`, 可选 `limit` |
| 公司/估值/同行 | `longbridge_company`, `longbridge_valuation`, `longbridge_industry_peers` | `symbol` |

## 调用原则

- 报价、日线、周线、估值、资金流等互不依赖的读取应并行发起。
- 保留工具返回的 `timestamp`、报告期和新闻 URL，报告中明确数据时点。
- K 线传给 `calc_indicators.py` 前，转换为脚本需要的 OHLCV JSON 数组；不要把 MCP 包装层或说明文字传给脚本。
- 工具返回空值时只针对缺失字段回退 CLI/OpenAPI，不要整套数据重复拉取。
- 账户、订单和成交属于私有数据，不通过网页搜索替代。
- 当前映射不假定存在交易写工具。下单和撤单按 `SKILL.md` 的两步确认流程走受支持的执行通道。
