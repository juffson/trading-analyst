# 数据源优先级与 URL 模板

## 第一优先：lb_client.py 统一客户端（所有市场通用）

能命中 lb_client 就不要去爬网页——自动适配 CLI / OpenAPI 双模式，数据结构化、免登录、无验证码风险。
命令格式：`python3 $LB_CLIENT <subcmd> <SYMBOL>`（`$LB_CLIENT` 为 lb_client.py 的绝对路径）。

| 子命令 | 用途 | 关键输出字段 |
|--------|------|-------------|
| `quote <SYMBOL>` | 实时行情 | `last_done` 现价、`volume` 成交量、`turnover` 成交额 |
| `calc-index <SYMBOL>` | 核心估值指标 | `pe_ttm`、`pb`、`turnover_rate`、`total_market_value` |
| `static <SYMBOL>` | 静态信息 | `total_shares`、`eps`、`eps_ttm`、`bps`、`dividend_yield` |
| `institution-rating <SYMBOL>` | 机构评级（CLI 兜底） | 评级分布、目标价区间、行业排名 |
| `forecast-eps <SYMBOL>` | EPS 预测（CLI 兜底） | 未来 1-3 年一致预期（部分 A 股无数据） |
| `kline <SYMBOL> --period day --count 260` | K 线 | OHLCV（可算 52 周高低、波动率） |
| `capital <SYMBOL>` | 资金流 | 大/中/小单净流入 |
| `capital <SYMBOL> --flow` | 分时资金 | 累计净流入曲线 |

**使用原则**：
- lb_client 输出均为 JSON，无需加 `--format json`
- A 股代码：`600xxx.SH`（沪）、`000xxx.SZ`（深）、`688xxx.SH`（科创板）、`300xxx.SZ`（创业板）
- 港股：`<5位代码>.HK`（如 `00700.HK`）
- 美股：`<ticker>.US`（如 `AAPL.US`）
- **A 股持仓不可查**：`longbridge positions` 只返回港美股账户，这是已知限制。行情/基本面/资金流查询不受影响
- `institution-rating` 目标价偏离现价 >30% 时数据可能过时，需标注并交叉验证
- `forecast-eps` 对部分 A 股标的无数据，属正常情况——降级到爬取卖方研报摘要

**longbridge 不覆盖的数据**（必须走爬取路径）：
- 5 年历史三表（利润表/资产负债表/现金流量表完整数据）
- 财报附注、分部营收、产能、客户集中度等定性信息
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
