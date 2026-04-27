# Longbridge OpenAPI 参考

本文件说明如何通过 OpenAPI（Python SDK）模式替代 CLI 获取数据，以及两种模式的差异。

## 快速开始

### 安装 Python SDK

```bash
pip install longport
```

### 配置认证

在 https://open.longportapp.com 申请开发者权限，获取以下三个凭证并设为环境变量：

```bash
export LONGPORT_APP_KEY="your_app_key"
export LONGPORT_APP_SECRET="your_app_secret"
export LONGPORT_ACCESS_TOKEN="your_access_token"
```

可以写入 `~/.zshrc` / `~/.bashrc` 持久化。

### 验证配置

```bash
python3 scripts/lb_client.py detect
```

`detect` 会分别报告行情权限和交易权限：

```json
{
  "active_mode": "api",
  "api_available": true,
  "api_quote_permission": true,
  "api_trade_permission": true
}
```

如果 `api_trade_permission: false`，说明当前 token 只有行情权限。需要前往
https://open.longportapp.com → 开发者中心 → 权限申请，勾选 **Trade** 权限并重新生成 ACCESS_TOKEN。

> 行情权限（Quote）和交易权限（Trade）是独立申请的。`detect` 检测失败时会在 `api_trade_permission_hint` 字段给出详细提示。

---

## 切换数据源模式

| 方式 | 说明 |
|------|------|
| `LONGBRIDGE_MODE=api` | 强制 API 模式 |
| `LONGBRIDGE_MODE=cli` | 强制 CLI 模式 |
| 不设置（默认） | 自动检测：优先 API，其次 CLI |

在 session 里告知用户当前模式：
```bash
python3 scripts/lb_client.py detect
```

---

## lb_client.py 命令对照表

所有命令输出均为 JSON，字段名与 CLI 标准化后一致，可直接传入 `calc_indicators.py`。

| lb_client 命令 | 等效 CLI 命令 | API 可用 | 说明 |
|---------------|-------------|---------|------|
| `quote AAPL.US` | `longbridge quote AAPL.US` | ✅ | 实时报价（last_done/high/low/pre/post） |
| `kline AAPL.US --period day --count 60` | `longbridge kline AAPL.US --period day --count 60 --format json` | ✅ | OHLCV 数组，直接兼容 calc_indicators.py |
| `calc-index AAPL.US` | `longbridge calc-index AAPL.US` | ✅ | PE TTM / PB / 换手率 / 总市值 |
| `capital AAPL.US` | `longbridge capital AAPL.US` | ✅ | 当日大/中/小单流入流出 |
| `capital AAPL.US --flow` | `longbridge capital AAPL.US --flow` | ✅ | 分时累计净流入 |
| `static AAPL.US` | `longbridge static AAPL.US` | ✅ | EPS/BPS/股本/股息 |
| `positions` | `longbridge positions` | ✅ | 当前持仓（含成本、盈亏） |
| `orders --history --start 2026-01-01` | `longbridge order --history --start 2026-01-01` | ✅ | 历史委托 |
| `executions --history --start 2026-01-01` | `longbridge order executions --history --start 2026-01-01` | ✅ | 历史成交 |
| `institution-rating AAPL.US` | `longbridge institution-rating AAPL.US` | ⚠️ CLI 兜底 | API SDK 未暴露此接口 |
| `forecast-eps AAPL.US` | `longbridge forecast-eps AAPL.US` | ⚠️ CLI 兜底 | API SDK 未暴露此接口 |
| `news AAPL.US` | `longbridge news AAPL.US` | ⚠️ CLI 兜底 | API SDK 未暴露此接口 |

**⚠️ CLI 兜底**：API 模式下，`lb_client.py` 会自动检查本机是否也安装了 CLI；有则转发给 CLI，无则返回 `{"ok": false, "cli_fallback_required": true}`。遇到这种情况需要告知用户该命令在纯 API 模式下不可用，建议安装 CLI 或从其他渠道（WebSearch）补充数据。

---

## 标准化输出格式

### quote
```json
{
  "ok": true,
  "mode": "api",
  "data": [{
    "symbol": "AAPL.US",
    "last_done": 187.23,
    "prev_close": 184.50,
    "open": 185.00,
    "high": 188.50,
    "low": 184.20,
    "volume": 52341234,
    "turnover": 9824500.00,
    "trade_status": 0,
    "pre_market": {"price": 186.00, "volume": 234000},
    "post_market": {"price": 187.50, "volume": 145000}
  }]
}
```

### kline（与 calc_indicators.py 直接兼容）
```json
{
  "ok": true,
  "mode": "api",
  "symbol": "AAPL.US",
  "period": "day",
  "count": 60,
  "data": [
    {"open": 185.0, "high": 188.5, "low": 184.2, "close": 187.2,
     "volume": 52341234, "turnover": 9824500.0, "timestamp": 1714435200}
  ]
}
```

提取给 `calc_indicators.py` 的方法：
```bash
python3 scripts/lb_client.py kline AAPL.US --period day --count 60 \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d['data']))" \
  | python3 scripts/calc_indicators.py
```

### calc-index
```json
{
  "ok": true,
  "mode": "api",
  "data": [{
    "symbol": "AAPL.US",
    "pe_ttm": 28.5,
    "pb": 42.1,
    "turnover_rate": 0.85,
    "total_market_value": 2850000000000
  }]
}
```

### capital
```json
{
  "ok": true,
  "mode": "api",
  "flow": false,
  "data": {
    "symbol": "AAPL.US",
    "capital_in":  {"large": 935400000, "medium": 234100000, "small": 56700000},
    "capital_out": {"large": 1180000000, "medium": 289000000, "small": 62000000}
  }
}
```

### positions
```json
{
  "ok": true,
  "mode": "api",
  "data": [{
    "symbol": "AAPL.US",
    "quantity": 100,
    "available_quantity": 100,
    "avg_cost": 250.00,
    "currency": "USD",
    "market_value": 18723.00,
    "unrealized_pnl": -6277.00
  }]
}
```

---

## 常见问题

**Q: API 模式和 CLI 模式的数据一样吗？**
基本一致，底层都接 LongPort 服务。主要差异是 API 模式下 `institution-rating` / `forecast-eps` / `news` 需要 CLI 兜底或改用 WebSearch。

**Q: API 的 rate limit 是多少？**
行情接口：每秒 10 次、最多 5 路并发。K 线：30 秒内 60 次。分析时串行调用即可，不需要特别控速。

**Q: A 股的持仓能查吗？**
和 CLI 一样，`positions` 只返回港美股账户持仓，A 股需手动告知代码/数量/成本。

**Q: 环境变量没生效怎么办？**
```bash
python3 -c "import os; print(os.environ.get('LONGPORT_APP_KEY', 'NOT SET'))"
```
如果显示 NOT SET，检查 shell 配置或在启动 Claude Code 前先 `export` 相关变量。
