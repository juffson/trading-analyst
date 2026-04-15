# Longbridge CLI 命令参考

## 股票代码格式

```
700.HK       港股
TSLA.US      美股
600519.SH    A股上海
000568.SZ    A股深圳
BTCUSD.HAS   加密货币
```

## 行情查询

```bash
# 实时报价（含盘前盘后）
longbridge quote COIN.US 9988.HK

# K线（period: 1m 5m 15m 30m 1h day week month year）
longbridge kline 603920.SH --period day --count 60
longbridge kline 603920.SH --period day --count 60 --format json  # JSON输出

# Level 2 盘口
longbridge depth COIN.US

# 逐笔成交
longbridge trades COIN.US

# 基础信息（名称、交易所、股本等）
longbridge static COIN.US
```

## 持仓 & 组合

```bash
# 股票持仓
longbridge positions
longbridge positions --format json

# 组合概览（总资产、盈亏、各标的详情）
longbridge portfolio

# 基金持仓
longbridge fund-positions
```

## 订单管理

```bash
# 今日订单
longbridge order

# 历史订单
longbridge order --history --start 2026-04-01
longbridge order --history --start 2026-04-01 --symbol COIN.US

# 今日成交
longbridge order executions

# 历史成交
longbridge order executions --history --start 2026-04-01

# 单笔订单详情
longbridge order detail <ORDER_ID>

# 下单（会要求确认）
longbridge order buy TSLA.US 100 --price 250.00
longbridge order sell TSLA.US 100 --price 260.00

# 撤单 / 改单
longbridge order cancel <ORDER_ID>
longbridge order replace <ORDER_ID> --qty 200 --price 255.00
```

## 基本面数据

```bash
# PE/PB/换手率/总市值
longbridge calc-index COIN.US

# 机构评级（共识、目标价、评级分布）
longbridge institution-rating COIN.US

# EPS 预测
longbridge forecast-eps COIN.US

# 财务报表
longbridge financial-report COIN.US

# 分红历史
longbridge dividend COIN.US

# SEC 13F 持仓（机构投资者）
longbridge investors <SYMBOL>

# 内部交易（美股）
longbridge insider-trades COIN.US
```

## 资金流向

```bash
# 当日资金分布（大单/中单/小单 流入/流出）
longbridge capital COIN.US

# 分时资金流（每分钟累计净流入）
longbridge capital COIN.US --flow

# JSON 格式
longbridge capital COIN.US --format json
longbridge capital COIN.US --flow --format json
```

## 其他

```bash
# 新闻
longbridge news COIN.US

# 监管文件
longbridge filing COIN.US

# 保证金比例
longbridge margin-ratio COIN.US

# 股东信息
longbridge shareholder COIN.US

# 持有该股的基金
longbridge fund-holder COIN.US

# 指数/ETF 成分股
longbridge constituent <SYMBOL>

# A/H 股溢价
longbridge ah-premium <SYMBOL>
```

## 通用选项

- `--format json` — JSON 输出（便于程序处理）
- `--format table` — 表格输出（默认）
- `-v` / `--verbose` — 显示请求详情
- `--lang zh-CN` / `--lang en` — 语言设置
