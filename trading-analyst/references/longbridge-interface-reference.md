# Longbridge connector/app 接口参考

Codex 或 ChatGPT 暴露 Longbridge 工具时，直接调用接口，不经 shell 包装，也不要求用户提供 LongPort API 密钥。工具在客户端里通常显示为 `longbridge_*`；底层全名可能带 MCP 命名空间，按末尾工具名匹配。

官方文档：

- 总入口：<https://open.longbridge.com/docs>
- MCP 服务：<https://open.longbridge.com/docs/mcp>
- 实时报价：<https://open.longbridge.com/docs/quote/pull/quote>
- 历史 K 线：<https://open.longbridge.com/docs/quote/pull/history-candlestick>
- 计算指标：<https://open.longbridge.com/docs/quote/pull/calc-index>

本文记录 trading-analyst 使用的 connector 请求参数、返回字段和调用约束。权威顺序为：**当前会话工具声明 > 官方在线文档 > 本地参考 > CLI/OpenAPI 回退文档**。当前声明与本文不同时使用当前声明，并在结果中记录差异。

## 0. 能力发现、授权与限流

- 官方 MCP 服务地址：全球 `https://mcp.longbridge.com`，中国大陆可用 `https://mcp.longbridge.cn`。
- MCP 使用 OAuth 2.1；由客户端管理凭证，不要求用户把 API key 或 token 粘贴给 Agent。
- 工具由客户端连接后自动发现。可用工具取决于地区、账户等级和 OAuth scopes，不能仅根据本文假设某工具存在。
- ChatGPT 的 Longbridge App 不提供下单等交易写工具。Codex 或其他客户端是否提供写工具，以当前工具列表和授权范围为准。
- OpenAPI Quote 限制为每秒不超过 10 次且并发不超过 5；Trade 限制为 30 秒不超过 30 次，连续请求间隔不少于 0.02 秒。SDK 可能替 Quote 自动限流，但交易请求需要调用方控制。
- 调用失败若表现为未授权或工具缺失，先说明权限/客户端差异；不要把它误判成“该市场没有数据”。

## 1. 行情与证券信息

### `longbridge_quote`

请求：

```json
{"symbols": ["AAPL.US", "700.HK"]}
```

返回每个标的的 `last_done`、`prev_close`、`open`、`high`、`low`、`volume`、`turnover`、`change_rate`、`change_value`、`trade_status`、`timestamp`。

用途：Trade View 的 `analysis_price` 必须优先取本接口的最新成交价，并保留 `timestamp`。批量标的放进同一次 `symbols` 调用。

### `longbridge_static_info`

请求：`{"symbols":["AAPL.US"]}`。

返回 `symbol`、`name_cn`、`name_en`、`exchange`、`type`、`lot_size`、`listed_date`、`delisted`。

用途：校验标准代码、市场、最小交易单位和退市状态。下单数量必须满足 `lot_size`。

### `longbridge_candlesticks`

```json
{
  "symbol": "AAPL.US",
  "period": "day",
  "count": 120,
  "forward_adjust": false,
  "trade_sessions": "all"
}
```

参数：

- `period`：`1m` / `5m` / `15m` / `30m` / `60m` / `day` / `week` / `month` / `year`；默认 `day`。
- `count`：默认 100，最大 1000。
- `forward_adjust`：是否前复权，默认 `false`。同一份分析的日线和周线保持一致复权口径。
- `trade_sessions`：`intraday` 仅常规时段；`all` 包含盘前盘后，默认 `all`。

返回 OHLCV K 线数组。传给 `calc_indicators.py` 时只保留每根 K 线的 `open/high/low/close/volume`，按时间升序排列。

### `longbridge_calc_indexes`

```json
{
  "symbols": ["AAPL.US"],
  "indexes": ["LastDone", "ChangeRate", "Volume", "TurnoverRate", "PeTtmRatio", "PbRatio", "DividendRatioTtm", "TotalMarketValue"]
}
```

`indexes` 省略时默认返回常用价格、涨跌、成交量、PE、PB、股息率、换手率和总市值。常用扩展字段包括 `YtdChangeRate`、`FiveDayChangeRate`、`TenDayChangeRate`、`HalfYearChangeRate`、`VolumeRatio` 和 `Amplitude`。

## 2. 资金与基本面

### `longbridge_capital_distribution`

请求：`{"symbol":"700.HK"}`。

返回 `capital_in`、`capital_out`、`timestamp`。用于大中小单资金分布；不同市场可能缺少部分档位，缺失值不要补零。

### `longbridge_capital_flow`

请求：`{"symbol":"700.HK"}`。

返回当日 `items[]`，每项包含 `timestamp`、`inflow`、`outflow`、`net_flow`。该接口是当日时间序列，不可当成多日资金历史。

### 基本面接口

| 工具 | 请求 | 主要返回/用途 |
|---|---|---|
| `longbridge_company` | `symbol` | 名称、交易所、行业、CEO、员工、成立年份、网站、市值 |
| `longbridge_valuation` | `symbol` | 估值概览与同行比较，读取 `metrics` |
| `longbridge_institution_rating` | `symbol` | `analyst`、`instratings`；记录覆盖期和更新时间 |
| `longbridge_forecast_eps` | `symbol` | EPS 预测历史 `items[]`；空数组属于正常缺数 |
| `longbridge_industry_peers` | 行业 `BK/...` counter_id | 行业层级、股票数量、日涨跌和 YTD 变化 |

`longbridge_industry_peers` 的参数不是普通股票代码。先从支持的行业排名接口取得 `BK/...` counter_id；拿不到时不要把股票代码直接传入。

## 3. 资讯接口

### `longbridge_news`

请求：`{"symbol":"AAPL.US"}`。

返回 `items[]`：`id`、`title`、`source`、`publish_time`、`summary`、`url`、`related_symbols[]`。用于建立 Driver 时保留发布时间、来源、URL 和关联标的。

### `longbridge_news_search`

请求：`{"keyword":"Apple earnings guidance","limit":20}`。

返回 `news_list[]`：`id`、`title`、`description`、`source_name`、`publish_at`、`score`。`score` 只表示检索排序，不可作为 `driver_strength`。重要事实仍需用公司、交易所或监管披露核验。

## 4. 账户、持仓与成交

### `longbridge_stock_positions`

无参数，返回当前股票持仓 `list[]`。仅代表接口当前可见的账户和市场；A 股持仓不可见时让用户提供数量与成本，不得推断为零仓位。

### `longbridge_account_balance`

请求可省略；按币种过滤时使用 `{"currency":"USD"}`。返回 `balances[]`：`currency`、`total_cash`、`max_finance_amount`、`remaining_finance_amount`、`risk_level`、`margin_call`。

不要跨币种直接相加；需要组合汇总时先取得汇率，并注明换算时点。

### 今日订单与成交

- `longbridge_today_orders({"symbol":"AAPL.US"})` 返回 `orders[]`：`order_id`、`symbol`、`side`、`order_type`、`status`、`quantity`、`price`、`submitted_at`、`executed_quantity`、`executed_price`。
- `longbridge_today_executions({"symbol":"AAPL.US"})` 返回 `executions[]`：`order_id`、`symbol`、`side`、`quantity`、`price`、`trade_done_at`；也可用 `order_id` 过滤。

订单用于判断计划是否提交，成交用于判断是否真实执行。不能仅凭订单状态推算成交价格。

### 历史订单与成交

```json
{
  "symbol": "AAPL.US",
  "start_at": "2026-04-05T00:00:00+08:00",
  "end_at": "2026-05-15T23:59:59+08:00"
}
```

- `longbridge_history_orders` 返回历史 `orders[]`，不含今日。
- `longbridge_history_executions` 返回历史 `executions[]`。
- `start_at` / `end_at` 必须是 RFC3339，并显式带时区。
- 跨到今天的复盘需要把历史接口与今日接口合并，按 `order_id + trade_done_at` 去重。

## 5. 调用与降级规则

1. 报价、日线、周线、估值、评级、资金和资讯互不依赖时并行调用。
2. 保存原始 `timestamp`、报告期、币种和 URL；报告展示统一的 `as_of`，但不抹掉各数据自己的时间。
3. 接口不存在、未授权、返回错误或关键字段为空时，只对缺失部分回退 CLI/OpenAPI。
4. 返回空数组与调用失败不同：空数组标记“暂无覆盖”，错误标记失败原因。
5. 账户、订单和成交属于私有数据，不通过网页搜索替代。
6. 交易写工具采用能力探测：若当前暴露官方 `submit_order` / `replace_order` / `cancel_order`，可在用户明确确认后直接调用；若未暴露，回退 `lb_client.py`。两种通道都必须遵守 `SKILL.md` 的预览 → 明确确认 → 执行流程。
7. 不确定工具参数时先读取当前工具声明；声明仍不足时查询官方文档对应页面，不猜测字段名或枚举值。
8. 官方文档可能更新。涉及新市场、权限、枚举、限流或交易规则时，以请求当日的官方页面为准。
