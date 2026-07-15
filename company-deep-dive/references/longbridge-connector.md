# Longbridge connector/app：公司深度研究映射

Codex 或 ChatGPT 暴露 Longbridge 工具时直接调用。工具可能显示为 `longbridge_quote`，底层全名可能带 MCP 命名空间；按语义匹配，不通过 shell 转发。

| 研究数据 | 工具 | 关键参数 |
|---|---|---|
| 实时报价 | `longbridge_quote` | `symbols` |
| PE/PB/市值/股息率 | `longbridge_calc_indexes` | `symbols`, 可选 `indexes` |
| 估值与同行比较 | `longbridge_valuation`, `longbridge_valuation_comparison`, `longbridge_industry_peers` | `symbol` |
| 证券静态资料 | `longbridge_static_info` | `symbols` |
| 公司概况/管理层 | `longbridge_company`, `longbridge_executive` | `symbol` |
| 业务分部 | `longbridge_business_segments`, `longbridge_business_segments_history` | `symbol`, 历史接口可指定 `report` |
| 机构评级/一致预期 | `longbridge_institution_rating`, `longbridge_forecast_eps`, `longbridge_consensus` | `symbol` |
| 年报核心三表 | `longbridge_financial_report` | `symbol`, `kind="ALL"`, `report="af"` |
| 季报核心三表 | `longbridge_financial_report` | `symbol`, `kind="ALL"`, `report="qf"` 或具体季度 |
| 详细会计科目（补充） | `longbridge_financial_statement` | `symbol`, `kind="ALL"`, `report="af/qf"`；可能返回空 |
| 最新财务摘要 | `longbridge_financial_report_latest` | `symbol` |
| 监管文件 | `longbridge_filings` | `symbol`，保留返回 URL |
| 历史行情 | `longbridge_candlesticks` | `symbol`, `period`, `count` |
| 公司资讯 | `longbridge_news`, `longbridge_news_search` | `symbol` 或 `keyword` |

## 数据质量规则

- 同阶段互不依赖的工具并行调用，减少研究耗时。
- 以 `financial_report` 作为 5 年结构化底稿；`financial_statement` 只补充流动资产、债务拆分、商誉、无形资产等详细科目。若后者为空，直接转官方财报，不要把空结果误判为公司没有该科目。
- `financial_report_latest` 用于最新一期摘要，不能代替完整历史序列。
- DCF 可直接以 `自由现金流 = 经营现金流 - CAPEX` 计算；若需要 D&A、营运资本调整或债务拆分，必须从详细报表或官方披露补齐。
- 保留报告期、币种、单位、时间戳和原始来源 URL；不要把不同会计期间或单位直接拼接。
- 连接器字段为空时只回退缺失部分，不重复抓整套数据。
- 聚合接口通常不保证提供流动资产/负债、长短债务、商誉、无形资产、D&A、股息支付、ROIC、流动/速动比率和归母口径；这些字段必须显式标为缺失并从一手披露补齐。
- 子代理 prompt 要明确“连接器优先，CLI/OpenAPI 回退”，并把缺失、口径差异写进 `notes`。

## 官方接口参考

- 文档总览：<https://open.longbridge.com/docs>
- MCP：<https://open.longbridge.com/docs/mcp>
- CLI 与 JSON 输出：<https://open.longbridge.com/docs/cli>
- Fundamental API：<https://open.longbridge.com/docs/fundamental/overview>
- Financial Report：<https://open.longbridge.com/docs/fundamental/fundamental/financial-report>
- Financial Statement（CLI 详细科目）：<https://open.longbridge.com/docs/cli/fundamentals/financial-statement>
- Static Info（股本/EPS/BPS）：<https://open.longbridge.com/docs/quote/pull/static>

SDK 会随版本增加 FundamentalContext 方法。客户端调用可选方法前先检查方法是否存在；本地 SDK 较旧时，回退 CLI 或连接器，不要据此断言官方接口不存在。
