# Longbridge CLI 命令参考

> 基于 https://github.com/longbridge/longbridge-terminal 源码整理，共 57 个顶级命令

## 股票代码格式

```
700.HK       港股
TSLA.US      美股
601398.SH    A股上海
000001.SZ    A股深圳
BTCUSD.HAS   加密货币
```

## 通用选项 (所有命令可用)

| 选项 | 说明 | 默认值 |
|------|------|--------|
| `--format <pretty\|json>` | 输出格式 (pretty 等同 table) | pretty |
| `-v` / `--verbose` | 显示请求详情 | false |
| `--lang <zh-CN\|en>` | 内容语言 | 系统 LANG |

---

## 一、认证 & 系统

```bash
# 登录 (设备授权流 / 浏览器 OAuth)
longbridge login
longbridge login --auth-code           # 浏览器 OAuth 流程

# 登出
longbridge logout

# 认证状态 (token 有效性、账户信息、行情权限)
longbridge auth status

# 检查连通性 (token、API、延迟)
longbridge check

# 更新 CLI
longbridge update
longbridge update --release-notes      # 查看更新日志

# 全屏 TUI (看盘、K线图、组合、Vim 键绑定)
longbridge tui
```

---

## 二、行情 - 报价

```bash
# 实时报价 (含盘前盘后)
longbridge quote COIN.US 9988.HK 700.HK

# Level 2 盘口
longbridge depth COIN.US

# 经纪商队列 (仅港股)
longbridge brokers 700.HK

# 逐笔成交
longbridge trades COIN.US --count 20

# 分时线
longbridge intraday COIN.US
longbridge intraday COIN.US --date 2026-04-14    # 历史分时

# 基础信息 (名称、交易所、股本、EPS、BPS)
longbridge static COIN.US 700.HK
```

---

## 三、行情 - K 线

```bash
# K线 (period: 1m 5m 15m 30m 1h day week month)
longbridge kline 603920.SH --period day --count 60
longbridge kline 603920.SH --period day --count 60 --format json
longbridge kline COIN.US --period 1m --count 400 --adjust none

# 历史K线 (按日期范围)
longbridge kline history 603920.SH --period day --start 2026-01-01 --end 2026-04-15

# 选项
# --period    1m|5m|15m|30m|1h|day|week|month (默认 day)
# --count     数量 (默认 100)
# --adjust    复权: none|forward|backward (默认 none)
# --session   交易时段 (默认 intraday)
```

---

## 四、行情 - 指数 & 资金

```bash
# 财务指标 (PE/PB/换手率/总市值)
longbridge calc-index COIN.US
longbridge calc-index COIN.US --fields pe,pb,dps_rate,turnover_rate,total_market_value

# 资金分布 (大单/中单/小单 流入流出)
longbridge capital COIN.US

# 分时资金流 (每分钟累计净流入)
longbridge capital COIN.US --flow

# 市场温度 (情绪指数)
longbridge market-temp HK
longbridge market-temp HK --history --start 2026-01-01 --end 2026-04-15

# 交易统计
longbridge trade-stats COIN.US

# 市场异动
longbridge anomaly --market HK --count 50
longbridge anomaly --symbol 700.HK
```

---

## 五、行情 - 交易时段 & 市场状态

```bash
# 交易时段表
longbridge trading session

# 交易日历
longbridge trading days HK --start 2026-04-01 --end 2026-04-30

# 市场开收盘状态
longbridge market-status

# 证券列表
longbridge security-list US
longbridge security-list HK

# 券商参与者列表
longbridge participants

# WebSocket 订阅状态
longbridge subscriptions
```

---

## 六、行情 - 指数成分 & A/H 溢价

```bash
# 指数/ETF 成分股
longbridge constituent HSI.HK --limit 50 --sort change --order desc

# A/H 股溢价
longbridge ah-premium 9988.HK --kline-type day --count 100

# A/H 盘中溢价
longbridge ah-premium intraday 9988.HK

# 经纪商持仓 (仅港股)
longbridge broker-holding 700.HK
longbridge broker-holding detail 700.HK
longbridge broker-holding daily 700.HK --broker <PARTI_NO>
```

---

## 七、期权 & 窝轮

```bash
# 期权报价
longbridge option quote AAPL240119C190000

# 期权链
longbridge option chain AAPL.US
longbridge option chain AAPL.US --date 2026-06-20

# 窝轮列表
longbridge warrant 700.HK

# 窝轮报价
longbridge warrant quote <WARRANT_SYMBOL>

# 窝轮发行商
longbridge warrant issuers
```

---

## 八、基本面

```bash
# 财务报表 (利润表、资产负债表、现金流)
longbridge financial-report COIN.US

# 机构评级 (共识、目标价、评级分布)
longbridge institution-rating COIN.US
longbridge institution-rating detail COIN.US

# EPS 预测
longbridge forecast-eps COIN.US

# 财务共识
longbridge consensus COIN.US

# 估值 (PE/PB 历史)
longbridge valuation COIN.US
longbridge valuation COIN.US --history --indicator pe --range 1y

# 行业估值对比
longbridge industry-valuation COIN.US --currency USD
longbridge industry-valuation dist COIN.US

# 分红
longbridge dividend COIN.US
longbridge dividend detail COIN.US

# 财经日历 (财报、分红、IPO 等)
longbridge finance-calendar earnings --market US --start 2026-04-15 --count 100

# 经营评估
longbridge operating COIN.US
```

---

## 九、公司信息

```bash
# 公司概况
longbridge company COIN.US

# 管理层/高管
longbridge executive COIN.US

# 企业行动 (分拆、合并等)
longbridge corp-action COIN.US

# 投资者关系
longbridge invest-relation COIN.US

# 股东信息
longbridge shareholder COIN.US --range all --sort chg --order desc

# 持有该股的基金/ETF
longbridge fund-holder COIN.US --count 20
```

---

## 十、SEC 数据 (美股)

```bash
# 内部交易 (Form 4)
longbridge insider-trades COIN.US --count 20

# 机构投资者 (13F)
longbridge investors                        # 机构列表
longbridge investors <CIK> --top 50         # 某机构持仓
longbridge investors changes <CIK> --top 50 # 季度持仓变动
```

---

## 十一、新闻 & 内容

```bash
# 新闻
longbridge news COIN.US --count 20
longbridge news detail <NEWS_ID>        # 全文 (Markdown 格式)

# 监管文件
longbridge filing COIN.US --count 20
longbridge filing detail COIN.US <ID>
longbridge filing detail COIN.US <ID> --list-files   # 列出附件

# 社区话题
longbridge topic COIN.US --count 20
longbridge topic detail <TOPIC_ID>
longbridge topic mine --page 1 --size 50
longbridge topic create --title "标题" --body "内容" --tickers COIN.US
longbridge topic replies <TOPIC_ID>
longbridge topic create-reply <TOPIC_ID> --body "回复内容"
```

---

## 十二、持仓 & 组合

```bash
# 股票持仓
longbridge positions
longbridge positions --format json

# 基金持仓
longbridge fund-positions

# 组合概览 (总资产、盈亏、各标的详情)
longbridge portfolio

# 账户资产/余额
longbridge assets
longbridge assets --currency USD

# 现金流记录
longbridge cash-flow --start 2026-01-01 --end 2026-04-15

# 保证金比例
longbridge margin-ratio COIN.US

# 最大可买/卖数量
longbridge max-qty COIN.US --side buy --price 185.00

# 汇率
longbridge exchange-rate
```

---

## 十三、订单管理

```bash
# 今日订单
longbridge order

# 历史订单
longbridge order --history --start 2026-04-01
longbridge order --history --start 2026-04-01 --symbol COIN.US

# 订单详情
longbridge order detail <ORDER_ID>

# 成交记录
longbridge order executions
longbridge order executions --history --start 2026-04-01

# 买入
longbridge order buy COIN.US 5 --price 185.00
longbridge order buy COIN.US 5 --price 185.00 \
  --order-type LO \          # LO|MO|ELO|ALO 等
  --tif day \                # day|gtc
  --outside-rth true \       # 允许盘前盘后
  --trigger-price 180.00 \   # 触发价 (止损单)
  --trailing-amount 2.00 \   # 追踪止损金额
  --trailing-percent 3 \     # 追踪止损百分比
  --limit-offset 0.50 \      # 限价偏移
  --expire-date 2026-05-01 \ # 过期日
  --remark "做T低吸" \       # 备注
  --yes                      # 跳过确认

# 卖出 (选项同买入)
longbridge order sell COIN.US 5 --price 190.00

# 撤单
longbridge order cancel <ORDER_ID> --yes

# 改单
longbridge order replace <ORDER_ID> --qty 10 --price 186.00 --yes
```

---

## 十四、盈亏分析

```bash
# 总览
longbridge profit-analysis

# 个股盈亏明细
longbridge profit-analysis detail COIN.US --start 2026-01-01 --end 2026-04-15

# 按市场分析
longbridge profit-analysis by-market US --start 2026-01-01 --currency USD
```

---

## 十五、账单 & 报表

```bash
# 报表列表
longbridge statement --type daily --limit 10
longbridge statement list --type monthly --start-date 2026-01-01

# 导出报表 (24 个可选 section)
longbridge statement export <FILE_KEY> --all --export-format json -o report.json

# 可用 section:
# Asset, AccountBalanceSum, EquityHoldingSums, AccountBalanceChangeSums,
# StockTradeSums, EquityHoldingChangeSums, AccountBalanceLockSums,
# EquityHoldingLockSums, OptionTradeSums, FundTradeSums, IpoTradeSums,
# VirtualTradeSums, Interests, LendingFees, CustodianFees, Corps,
# BondEquityHoldingSums, OtcTradeSums, OutstandingSums,
# FinancingTransactionSums, InterestDeposits, MaintenanceFees,
# CashPluses, GstDetails
```

---

## 十六、自选股管理

```bash
# 查看所有自选组
longbridge watchlist

# 查看某组内的股票
longbridge watchlist show <GROUP>

# 创建自选组
longbridge watchlist create "科技股"

# 删除自选组
longbridge watchlist delete <ID> --yes

# 更新自选组 (添加/移除股票)
longbridge watchlist update <ID> --add COIN.US AVGO.US
longbridge watchlist update <ID> --remove 3032.HK

# 置顶
longbridge watchlist pin COIN.US 9988.HK
longbridge watchlist pin --remove COIN.US
```

---

## 十七、价格提醒

```bash
# 查看提醒
longbridge alert
longbridge alert COIN.US              # 按标的筛选

# 添加提醒
longbridge alert add COIN.US --price 180.00 --direction fall --note "跌破缺口支撑"
longbridge alert add 603920.SH --price 52.00 --direction fall --frequency once

# 删除 / 启用 / 禁用
longbridge alert delete <ID>
longbridge alert enable <ID>
longbridge alert disable <ID>
```
