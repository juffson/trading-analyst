# Longbridge OpenAPI /v1/quant/run_script 接口参考

## 连接配置

- **Base URL**: `https://openapi.longbridge.cn`（可通过 `LONGPORT_OPENAPI_HTTP_URL` 覆盖）
- **路径**: `POST /v1/quant/run_script`
- **完整 URL**: `https://openapi.longbridge.cn/v1/quant/run_script`

## 认证

与 longport Python SDK 共用同一套环境变量：

| 变量 | 说明 |
|------|------|
| `LONGPORT_APP_KEY` | App Key |
| `LONGPORT_APP_SECRET` | App Secret（用于请求签名） |
| `LONGPORT_ACCESS_TOKEN` | Access Token |

**请求签名（Headers）：**

| Header | 值 |
|--------|---|
| `Content-Type` | `application/json; charset=utf-8` |
| `X-Api-Key` | `{LONGPORT_APP_KEY}` |
| `Authorization` | `Bearer {LONGPORT_ACCESS_TOKEN}` |
| `X-Timestamp` | Unix 秒时间戳（字符串） |
| `X-Api-Signature` | HMAC-SHA256(`app_secret`, `timestamp + app_key + access_token + body`) |

签名实现见 `scripts/run_script.py` 中的 `_make_headers()`。

## 请求格式

```json
{
  "script": "//@version=6\nstrategy(...)\n...",
  "counter_id": "ST/US/TSLA",
  "start_time": 1704067200,
  "end_time": 1735689600,
  "line_type": 1000,
  "input_json": "[14, 70, 30]"
}
```

### 请求字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `script` | string | 是 | QuantScript 脚本，必须以 `//@version=6` 开头 |
| `counter_id` | string | 是 | 标的代码，`ST/REGION/TICKER` 格式 |
| `start_time` | int64 | 是 | 开始时间，Unix 秒级时间戳 |
| `end_time` | int64 | 是 | 结束时间，Unix 秒级时间戳 |
| `line_type` | int32 | 是 | K线类型（1000=日K, 2000=周K, 3000=月K, 4000=年K） |
| `input_json` | string | 否 | input 覆盖值，JSON 数组字符串，按 `input.*()` 声明顺序，`null` 跳过 |

**注意：** `initial_capital`、`qty`、`commission`、`slippage` 等参数由脚本内 `strategy()` 声明控制，不在请求里传。

## 响应格式

```json
{
  "code": 0,
  "message": "",
  "data": {
    "report_json": "{ ... }",
    "events_json": "[ ... ]",
    "chart_json": "{ ... }"
  }
}
```

### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | 状态码，0=成功 |
| `message` | string | 错误信息（失败时） |
| `data.report_json` | string | 策略报告 JSON（性能指标 + 订单列表） |
| `data.events_json` | string | VM 执行事件 JSON |
| `data.chart_json` | string | 图表输出 JSON（plot/hline/plotshape） |

### 状态码

| code | 说明 |
|------|------|
| 0 | OK |
| 1 | UnknownError |
| 2 | InvalidArgument（脚本语法错误等） |
| 3 | NotFound |
| 4 | UnAuthorize（认证失败） |
| 5 | PermissionDenied |
| 6 | ResourceExhausted |
| 7 | FailedPrecondition |
| 8 | Internal |
| 9 | Unavailable |

## curl 调用模板

```bash
APP_KEY="your_app_key"
APP_SECRET="your_app_secret"
ACCESS_TOKEN="your_access_token"
TIMESTAMP=$(date +%s)

BODY='{
  "script": "//@version=6\nstrategy(\"RSI\", overlay=false, initial_capital=100000)\nlength = input.int(14)\nrsiValue = ta.rsi(close, length)\nif ta.crossunder(rsiValue, 30)\n    strategy.entry(\"Long\", strategy.long, qty=100)\nif ta.crossover(rsiValue, 70)\n    strategy.close(\"Long\")",
  "counter_id": "ST/US/TSLA",
  "start_time": 1704067200,
  "end_time": 1735689600,
  "line_type": 1000
}'

SIGN_STR="${TIMESTAMP}${APP_KEY}${ACCESS_TOKEN}${BODY}"
SIGNATURE=$(echo -n "$SIGN_STR" | openssl dgst -sha256 -hmac "$APP_SECRET" | awk '{print $2}')

curl -s -X POST 'https://openapi.longbridge.cn/v1/quant/run_script' \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "X-Api-Key: ${APP_KEY}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-Timestamp: ${TIMESTAMP}" \
  -H "X-Api-Signature: ${SIGNATURE}" \
  -d "$BODY"
```

## report_json 结构

```
{
  config: { initialCapital, commissionType, commissionValue, slippage, pyramiding }
  performanceAll: {
    netProfit, netProfitPercent,
    buyHoldReturn, buyHoldReturnPercent,
    maxDrawdown, maxDrawdownPercent,
    sharpeRatio, sortinoRatio,
    totalClosedTrades, numWinningTrades, numLosingTrades,
    percentProfitable, profitFactor,
    avgTrade, avgWinningTrade, avgLosingTrade, ratioAvgWinLoss,
    largestWinningTrade, largestLosingTrade,
    grossProfit, grossLoss, commissionPaid, maxRunup
  }
  performanceLong:  { 同上 }
  performanceShort: { 同上 }
  closedTrades: [{
    tradeNum, entryId, entrySide, entryPrice, entryTime,
    exitPrice, exitTime, quantity, profit, profitPercent,
    maxRunup, maxDrawdown, commission
  }]
  openTrades: [{ 同上 }]
  equityCurve:   [float]   每 bar 权益值
  drawdownCurve: [float]   每 bar 回撤值
  buyHoldCurve:  [float]   买入持有对照
  dailyReturns:  [{ date, returnPercent }]
  tradingRange:  { startTime, endTime }   毫秒时间戳
}
```

## chart_json 结构

```
{
  series_graphs: {
    "0": { Plot: { title, series: [float], colors: [int], style, line_width, histbase } }
    "1": { Plot: { ... } }
    ...
  }
  filled_orders: {
    "bar_index": [{ order_id, price, quantity }]   // 正数=买入, 负数=卖出
  }
  bar_colors: { colors: [int|null] }
  graphs: dict|null
  graph_id_counter: int
}
```

## inputs_json 示例

```pine
// 脚本中的 input 声明（index 从 0 开始）：
length    = input.int(14, title="RSI Length")    // index 0
overbought = input.int(70, title="Overbought")   // index 1
oversold   = input.int(30, title="Oversold")     // index 2
```

| inputs_json | 效果 |
|-------------|------|
| `"[21, 80, 20]"` | 全部覆盖 |
| `"[21]"` | 只改 length=21 |
| `"[null, null, 20]"` | 只改 oversold=20 |
| 不传 | 全用默认值 |
