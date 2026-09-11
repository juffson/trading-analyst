# 数据源优先级与 URL 模板

## 第一优先：Longbridge connector/app（Codex / ChatGPT）

当前会话暴露 Longbridge 工具时直接调用。行情使用 `longbridge_quote` / `longbridge_candlesticks`，估值使用 `longbridge_calc_indexes` / `longbridge_valuation`，5 年核心报表使用 `longbridge_financial_report`，最新一期摘要使用 `longbridge_financial_report_latest`；`longbridge_financial_statement` 仅补充详细科目。评级预测使用 `longbridge_institution_rating` / `longbridge_forecast_eps` / `longbridge_consensus`，公告资讯使用 `longbridge_filings` / `longbridge_news`。

完整映射见 `longbridge-connector.md`。连接器未安装、未授权、缺少接口或失败时才进入下面的 Python 回退。

## 第二优先：lb_client.py 统一客户端（所有市场通用）

能命中 lb_client 就先获取结构化底稿——它自动适配 CLI / OpenAPI 双模式。OpenAPI 模式同时使用行情 `QuoteContext` 和基本面 `FundamentalContext`；官方披露仍用于关键字段交叉验证。
命令格式：`python3 $LB_CLIENT <subcmd> <SYMBOL>`（`$LB_CLIENT` 为 lb_client.py 的绝对路径）。

| 子命令 | 用途 | 关键输出字段 |
|--------|------|-------------|
| `quote <SYMBOL>` | 实时行情 | `last_done` 现价、`volume` 成交量、`turnover` 成交额 |
| `calc-index <SYMBOL>` | 核心估值指标 | `pe_ttm`、`pb`、`turnover_rate`、`total_market_value` |
| `static <SYMBOL>` | 静态信息 | `total_shares`、`eps`、`eps_ttm`、`bps`、`dividend_yield` |
| `financial-report <SYMBOL> --kind ALL --report af` | 5 年年度核心三表 | 营收、利润、资产负债、现金流、CAPEX 等历史序列 |
| `financial-report <SYMBOL> --kind ALL --report qf` | 季度核心三表 | 最新季度和历史季度序列 |
| `company/executive <SYMBOL>` | 公司及管理层 | 公司概况、管理层资料 |
| `institution-rating <SYMBOL>` | 机构评级 | 评级分布、目标价区间、行业排名 |
| `forecast-eps/consensus <SYMBOL>` | 一致预期 | 未来 EPS、营收和利润预测（部分标的无数据） |
| `valuation-history/shareholder-top <SYMBOL>` | 历史估值/股东 | 估值序列、主要股东 |
| `kline <SYMBOL> --period day --count 260` | K 线 | OHLCV（可算 52 周高低、波动率） |
| `capital <SYMBOL>` | 资金流 | 大/中/小单净流入 |
| `capital <SYMBOL> --flow` | 分时资金 | 累计净流入曲线 |

**使用原则**：
- lb_client 输出均为 JSON，无需加 `--format json`
- CLI 的其他参数会原样透传，例如 `financial-statement AAPL.US --kind BS --report af`
- A 股代码：`600xxx.SH`（沪）、`000xxx.SZ`（深）、`688xxx.SH`（科创板）、`300xxx.SZ`（创业板）
- 港股：`<5位代码>.HK`（如 `00700.HK`）
- 美股：`<ticker>.US`（如 `AAPL.US`）
- **A 股持仓不可查**：`longbridge positions` 只返回港美股账户，这是已知限制。行情/基本面/资金流查询不受影响
- `institution-rating` 目标价偏离现价 >30% 时数据可能过时，需标注并交叉验证
- `forecast-eps` 对部分 A 股标的无数据，属正常情况——降级到爬取卖方研报摘要

**仍需官方披露或网页研究补充的数据**：
- 流动资产/负债、长短债务拆分、商誉、无形资产、D&A、股息支付、归母口径等聚合接口缺失项
- 会计口径说明、财报附注及一次性项目
- 财报附注、产能、客户集中度等定性信息
- 行业数据（市场规模、竞争格局）
- 宏观数据（利率、汇率、政策）

对于这些场景，按下面的市场优先级列表爬取。

---

## A 股

| 优先级 | 数据源 | 用途 | URL 模板 |
|--------|--------|------|----------|
| 1 | 巨潮资讯网 | 官方财报披露 | `http://www.cninfo.com.cn/new/fulltextSearch?code=<code>` |
| 2 | 东方财富 | 行情 + 财务 + 公告 | `https://quote.eastmoney.com/<exchange>/<code>.html`，财务 `https://data.eastmoney.com/bbsj/<code>.html` |
| 3 | 新浪财经 | 备用行情 + 财务 | `https://finance.sina.com.cn/realstock/company/sh<code>/nc.shtml` (沪) / `sz<code>` (深) |
| 4 | 雪球 | 社区讨论 + 备用数据 | `https://xueqiu.com/S/SH<code>` / `SZ<code>` |
| 5 | 同花顺 | 行情 + 财务 | `https://stockpage.10jqka.com.cn/<code>/` |

A 股代码规则：
- 沪市主板：60 开头（如 600519 茅台）
- 沪市科创板：68 开头（如 688981 中芯国际）
- 深市主板：00 开头（如 000858 五粮液）
- 深市创业板：30 开头（如 300750 宁德时代）

### 东方财富财务接口字段速查（经过实测验证，A 股适用）

接口基础 URL：`https://datacenter.eastmoney.com/securities/api/data/v1/get`
公共参数：`source=HSF10&client=PC&filter=(SECURITY_CODE%3D%22{6位代码}%22)`

#### 利润表 `reportName=RPT_LICO_FN_CPD`

> ⚠️ 日期字段名为 `REPORTDATE`（无下划线），与其他两张表不同

| 字段名 | 含义 |
|--------|------|
| `REPORTDATE` | 报告期（格式 `YYYY-MM-DD HH:MM:SS`） |
| `TOTAL_OPERATE_INCOME` | 营业总收入 |
| `PARENT_NETPROFIT` | 归母净利润 |
| `BASIC_EPS` | 基本每股收益 |

排序：`sortColumns=REPORTDATE&sortTypes=-1`（最新在前）

#### 资产负债表 `reportName=RPT_DMSK_FN_BALANCE`

| 字段名 | 含义 |
|--------|------|
| `REPORT_DATE` | 报告期 |
| `TOTAL_ASSETS` | 总资产 |
| `TOTAL_LIABILITIES` | 总负债 |
| `MONETARYFUNDS` | 货币资金（现金等价物） |

排序：`sortColumns=REPORT_DATE&sortTypes=-1`

#### 现金流量表 `reportName=RPT_DMSK_FN_CASHFLOW`

| 字段名 | 含义 |
|--------|------|
| `REPORT_DATE` | 报告期 |
| `NETCASH_OPERATE` | 经营活动现金净流量 |
| `NETCASH_INVEST` | 投资活动现金净流量 |
| `NETCASH_FINANCE` | 筹资活动现金净流量 |
| `CONSTRUCT_LONG_ASSET` | CAPEX（购建长期资产支出） |

排序：`sortColumns=REPORT_DATE&sortTypes=-1`

**已验证无效的字段名**（不要用）：`OPERATE_PROFIT`、`NETPROFIT`、`TOT_SHAREHOLDERS_EQY`、`MONETARY_CAP`、`PURCHASE_FIXED_ASSETS`、`NET_CASH_FLOWS_OPER_ACT`

---

## 港股

| 优先级 | 数据源 | 用途 | URL 模板 |
|--------|--------|------|----------|
| 1 | HKEXnews | 官方披露 | `https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh` |
| 2 | 富途 | 行情 + 财务 | `https://www.futunn.com/stock/<code>-HK` |
| 3 | 新浪港股 | 备用 | `https://finance.sina.com.cn/realstock/company/hk<code>/nc.shtml` |
| 4 | 雪球港股 | 备用 + 讨论 | `https://xueqiu.com/S/<code>` (如 09988) |
| 5 | 同花顺港股 | 备用 | `https://stockpage.10jqka.com.cn/hk<code>/` |

港股代码：5 位数字，前面加"HK"前缀（如 HK00700 腾讯、HK09988 阿里巴巴）。

## 美股

| 优先级 | 数据源 | 用途 | URL 模板 |
|--------|--------|------|----------|
| 1 | SEC EDGAR | 官方 10-K/10-Q | `https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<ticker>` |
| 2 | Yahoo Finance | 行情 + 财务汇总 | `https://finance.yahoo.com/quote/<ticker>/` |
| 3 | Macrotrends | 长期历史财务 | `https://www.macrotrends.net/stocks/charts/<ticker>/<company>/financial-ratios` |
| 4 | Stockanalysis.com | 财务拆分 | `https://stockanalysis.com/stocks/<ticker>/financials/` |
| 5 | Seeking Alpha / Morningstar | 深度分析 | `https://seekingalpha.com/symbol/<ticker>` |

## 无风险利率（用于 DCF）

| 市场 | 基准 | 查询 URL |
|------|------|----------|
| A 股 | 中国 10 年期国债收益率 | `https://www.chinabond.com.cn/` 或 Wind 或 `https://cn.investing.com/rates-bonds/china-10-year-bond-yield` |
| 港股 | 香港 10 年期或美国 10 年 | `https://cn.investing.com/rates-bonds/hong-kong-10-year-bond-yield` |
| 美股 | 美国 10 年期国债收益率 | `https://www.treasury.gov/resource-center/data-chart-center/interest-rates/Pages/default.aspx` |

## 行业数据

| 数据源 | 用途 |
|--------|------|
| 国家统计局 | A 股行业 & 宏观数据 |
| 发改委、工信部 | 产业政策 |
| 海关总署 | 进出口数据（对出海公司重要） |
| 中金 / 中信证券研报 | 深度行业分析 |
| Statista / IBISWorld | 全球行业数据 |

## 使用原则

1. **官方披露优先**：巨潮、HKEXnews、SEC EDGAR 永远是最权威的
2. **交叉验证**：关键数据（市值、ROE、净利润）至少从 2 个源确认
3. **数据时点**：任何数据都要记录 `as_of_date`
4. **单位统一**：在一份报告内统一单位（都用亿元或都用百万），避免混用
5. **货币一致**：A 股用 CNY，港股用 HKD，美股用 USD，跨市场对比时做换算
6. **遇到封锁/收费墙**：如果某源要登录/收费，跳过，不用 bash/curl 绕过（违反使用条款）
