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
| 年报三表 | `longbridge_financial_statement` | `symbol`, `kind="ALL"`, `report="af"` |
| 季报三表 | `longbridge_financial_statement` | `symbol`, `kind="ALL"`, `report="qf"` 或具体季度 |
| 最新财务摘要 | `longbridge_financial_report_latest` | `symbol` |
| 监管文件 | `longbridge_filings` | `symbol`，保留返回 URL |
| 历史行情 | `longbridge_candlesticks` | `symbol`, `period`, `count` |
| 公司资讯 | `longbridge_news`, `longbridge_news_search` | `symbol` 或 `keyword` |

## 数据质量规则

- 同阶段互不依赖的工具并行调用，减少研究耗时。
- `financial_statement` 能提供报表时，以其作为结构化底稿，但关键年度营收、净利润、现金流、总股本仍与官方披露交叉验证。
- 保留报告期、币种、单位、时间戳和原始来源 URL；不要把不同会计期间或单位直接拼接。
- 连接器字段为空时只回退缺失部分，不重复抓整套数据。
- 子代理 prompt 要明确“连接器优先，CLI/OpenAPI 回退”，并把缺失、口径差异写进 `notes`。
