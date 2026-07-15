---
name: company-deep-dive
description: "买入前的公司深度价值分析，输出 HTML 决策仪表盘。覆盖 A股/港股/美股。Codex/ChatGPT 优先通过 Longbridge connector/app 获取行情、估值、评级、预测、财务报表和公司资料，连接器不可用时回退 CLI/OpenAPI；关键财务数据再与交易所、巨潮、SEC EDGAR 等一手披露交叉验证。请务必在以下场景触发：用户说「深度分析 X 公司」「估值一下 X」「价值投资角度看 X」「X 值不值得买」「帮我做 X 的 DCF」「X 的护城河怎么样」，或者提供股票代码并明确要求做基本面/价值分析；即使用户只说「研究一下 X」「帮我看看 X」且上下文是长期投资/选股/估值，也应触发。输出包含 11 维度评分、DCF、安全边际和 HTML 仪表盘，不处理短线买卖点；短线、做T、持仓管理请走 trading-analyst。"
---

# Company Deep Dive — 买入前公司深度研究

一套模拟顶级价值投资者（巴菲特/芒格/段永平风格）研究公司的工作流。
通过并行子代理收集财务数据、做宏观/行业/估值分析，最终由主代理对 11 个维度打分（1-5），
输出一份可交付的 HTML 决策仪表盘。

**免责声明**：结果仅供研究参考，不构成任何投资建议。投资有风险，决策需独立判断。

---

## 数据源选择

按以下顺序路由：

1. **Longbridge connector/app（Codex / ChatGPT 首选）**：当前会话存在 Longbridge 工具时直接调用，覆盖行情、估值、财报、评级预测、公司资料、业务分部、股东和公告资讯。
2. **统一 Python 客户端（回退）**：连接器未安装、未授权、缺少接口或失败时，使用 `scripts/lb_client.py`。API 模式通过 `QuoteContext` + `FundamentalContext` 获取结构化数据；SDK 暂未覆盖的接口才回退 CLI。
3. **官方披露交叉验证**：财务报表、股本、重大事项至少再核对一个一手来源；定性信息优先公司 IR、交易所、巨潮或 SEC EDGAR。

连接器映射见 `references/longbridge-connector.md`。连接器可用时不运行 `lb_client.py detect`，不要求用户提供 API key，也不对同一批字段重复调用 CLI。

`SKILL_DIR` 是当前 `SKILL.md` 所在目录。Skill 可能由 Agent 安装器放到平台自己的目录，也可能由用户手动安装；不要猜测固定位置或依赖当前工作目录。**在调用任何命令或构造子代理 prompt 之前，先从 Skill 文件路径解析绝对目录**，记为 `SKILL_DIR`；`LB_CLIENT="$SKILL_DIR/scripts/lb_client.py"`，后续统一用绝对路径调用。

## Agent 平台兼容

- 在 Codex / ChatGPT 中，使用平台原生的 subagent workflow，把同一阶段的独立任务并行委派并等待全部完成。
- 在 Claude Code 中，使用对应的 Agent/Task 工具完成相同委派。
- 构造数据子代理 prompt 时明确传入“Longbridge connector/app 优先”的路由；子代理能看到连接器工具时直接调用，不要先跑 `lb_client.py`。
- 文中的 `Agent(...)` 只是平台无关的伪代码，不要求工具名称或参数名完全相同。关键是启动真实、隔离的子代理，而不是由主代理串行模拟。
- 文中的网页搜索指当前平台提供的搜索/浏览工具。优先官方披露和一手来源，最终报告保留可点击来源链接。
- 文中的“写文件”或“运行 shell”使用当前平台提供的等价工具；所有写入必须落在用户指定目录或当前 workspace 内。

---

## 三条硬性铁律（务必先读）

这个 skill 有三个反复出错的点，先讲清楚：

### 铁律 1：必须用平台原生能力真的 spawn 子代理，不要用 Python 脚本代替

阶段 1 的 2 个数据 agent、阶段 2 的 4 个分析 agent、阶段 3 的 DCF agent，都必须通过当前平台真正的子代理/委派能力启动。**不要**在主线程里自己写 `fetch_financials.py` 或 `analyze_porter.py` 之类的脚本来“模拟”子代理——那样会：
- 丢失并行加速（阶段 1 和阶段 2 本来能并发）
- 主代理 context 被原始数据塞满，推理质量下降
- 每个维度的独立视角消失，变成一个声音自说自话

判断是否做对了：应该出现 7 个真实子代理任务（2 + 4 + 1），而不是若干主线程 shell/文件写入来冒充委派。如果平台不支持子代理，明确告知用户并请求允许降级为串行执行；不要静默降级。

### 铁律 2：生成 HTML 必须调用 `scripts/render_dashboard.py`，不要自己手写 HTML

所有子代理跑完、analysis_summary.json 准备好之后，生成最终 HTML 只有一条路径：

```bash
python <skill根目录>/scripts/render_dashboard.py \
  --data-dir <workspace/company_data/公司名> \
  --analysis-json <workspace/公司名/analysis_summary.json> \
  --output <workspace/outputs/公司名_价值分析_日期.html>
```

**不要**用 `<script>` 标签写一个新的 HTML、不要用 f-string 拼 HTML、不要绕过渲染脚本直接写 `.html`。脚本已经包含了所有必要的中文标签（"波特五力"、"PESTEL"、11 个维度名、"致命风险"、"第二层思维"、"安全边际"等），以及 Chart.js CDN 引入、雷达图、财务趋势图、DCF 横条、颜色规范。自己写 HTML 会漏掉这些字面标签和可视化，测试会挂。

如果脚本不够用（比如要加新维度），**改脚本本身**，不要绕过。

### 铁律 3：主代理的 `analysis_summary.json` 必须包含 render_dashboard.py 所需的所有键

脚本读取的顶层键是固定的：`scores`（11 维度）、`valuation`（DCF + 账面）、`moat`、`pestel`、`porter`、`second_level_thinking`、`fatal_risks`、`investment_decision`。每一项的子结构见下方 Step 5.5，缺一个字段仪表盘对应区块就空了。

---

## 什么时候用这个 skill

**触发场景**：
- 用户要对某家公司做"深度/价值/基本面/长期持有"角度的分析
- 用户问 "X 值不值得买入"、"X 的护城河"、"X 的 DCF 估值"
- 用户给出公司名或代码并提到"研究"、"估值"、"投资决策"
- 用户要 "PESTEL"、"波特五力"、"第二层思维"等价值投资专业分析

**不要用这个 skill**：
- 用户要的是当日短线技术信号（MA/MACD/资金流）→ 用 `trading-analyst`
- 用户已持有、要做T或制定短期交易计划 → 用 `trading-analyst`
- 用户要做大盘复盘或持仓审视 → 用 `trading-analyst`

---

## 整体工作流（四阶段）

```
[阶段 1] 财务数据采集（2 个 Agent 并行）
    ├─ stock_quote_fetcher           抓实时行情 & 市值 & PE/PB & 股本
    └─ financial_statements_fetcher   抓近 5 年 + 最新季报的三表 & 核心指标
                              ↓
[阶段 2] 公司多维分析（4 个 Agent 并行）
    ├─ economic_model_analyst     盈利/成长/营运/偿债 四大能力分析
    ├─ pestel_analyst             政治/经济/社会/技术/环境/法律 动态分析
    ├─ porter_five_forces_analyst 波特五力 + 互补品（第六力）动态分析
    └─ asset_valuator             净现金/股权投资/固定资产账面估值
                              ↓
[阶段 3] DCF 估值（1 个 Agent）
    └─ dcf_valuator  乐观/中性/悲观三档，内部用 Python 精确计算
                              ↓
[阶段 4] 主代理综合分析
    ├─ 11 维度逐项打分（1-5 分）
    ├─ 公司总价值 = DCF 经营价值 + 账面资产价值
    ├─ 安全边际计算（基于当前股价 vs 内在价值）
    └─ 生成 HTML 决策仪表盘
```

**并行 = 在同一个消息里发出多个 Agent 调用**。绝对不要串行。

---

## 执行步骤

### Step 0：确认公司和代码

如果用户只给了名字（如"茅台"），先确认代码和市场。常见映射：

```
茅台 → 600519 (A股)         宁德时代 → 300750 (A股)
比亚迪 → 002594 (A股)        腾讯 → hk00700 (港股)
美团 → hk03690 (港股)        苹果 → AAPL (美股)
```

不确定时使用当前平台的网页搜索确认，并优先交易所或公司官网，避免代码写错。

### Step 1：检查 workspace 是否已有数据

数据目录：`<workspace>/company-deep-dive/company_data/<公司名>/`（workspace 用当前项目目录或用户指定目录）

如果已有且在 7 天内：直接读取，跳过 Step 2。
如果没有或过旧：进入 Step 2。

### Step 2：并行调用 2 个数据 Agent（阶段 1）

使用平台原生子代理能力发出 2 个并行调用，并在继续前等待两者完成。**不要**用主线程 shell + Python 抓数据代替。

形式上应该长这样（示意，实际 prompt 去 `references/sub_agent_prompts.md` 里拿完整版）：

```
Agent(
  description="抓取 <公司> 实时行情",
  prompt="<把 sub_agent_prompts.md 第 1 节的模板填入公司代码和输出路径>"
)
Agent(
  description="抓取 <公司> 近5年三表数据",
  prompt="<sub_agent_prompts.md 第 2 节模板>"
)
```

**Agent 1 — stock_quote_fetcher**（见 `references/sub_agent_prompts.md` 第 1 节完整 prompt）

让它写出 `stock_quote.json`，包含：
- 当前股价、涨跌幅、成交量、成交额
- 总市值、流通市值
- 总股本、流通股本（亿股）
- PE TTM、PB、股息率 TTM
- 52周高低、交易所

**Agent 2 — financial_statements_fetcher**（见 `references/sub_agent_prompts.md` 第 2 节完整 prompt）

让它写出 `financial_statements.json`，包含近 5 年 + 最新季报：
- 利润表（营收、毛利、营业利润、净利润、归母净利润、EPS）
- 资产负债表（流动/非流动资产和负债、股东权益、现金、有息负债）
- 现金流量表（经营/投资/筹资现金流、CAPEX、折旧摊销、自由现金流）
- 核心比率（ROE、ROIC、毛利率、净利率、资产周转率、负债率、流动比率）

**数据源优先级**（详见 `references/data_sources.md`）：

1. **第一优先：Longbridge connector/app**——工具映射见 `references/longbridge-connector.md`
   - 行情与估值：`longbridge_quote`、`longbridge_calc_indexes`、`longbridge_valuation`
   - 公司与证券资料：`longbridge_company`、`longbridge_static_info`、`longbridge_business_segments`
   - 评级预测：`longbridge_institution_rating`、`longbridge_forecast_eps`、`longbridge_consensus`
   - 财务报表：优先 `longbridge_financial_report`（年度 `report="af"`、季度 `report="qf"`），并用 `longbridge_financial_report_latest` 获取最新摘要；`longbridge_financial_statement` 仅作详细科目补充，返回空时不要阻塞
   - 公告资讯：`longbridge_filings`、`longbridge_news`、`longbridge_news_search`
2. **第二优先：`python3 $LB_CLIENT` 回退**——自动适配 CLI / OpenAPI
   - `python3 $LB_CLIENT quote <SYMBOL>`：实时行情（价、涨跌、成交量、盘前盘后）
   - `python3 $LB_CLIENT calc-index <SYMBOL>`：PE TTM、PB、换手率、总市值
   - `python3 $LB_CLIENT static <SYMBOL>`：股本、EPS、BPS、股息、最小交易单位
   - `python3 $LB_CLIENT financial-report <SYMBOL> --kind ALL --report af`：年度核心三表
   - `python3 $LB_CLIENT financial-report <SYMBOL> --kind ALL --report qf`：季度核心三表
   - `python3 $LB_CLIENT institution-rating <SYMBOL>`：机构评级、目标价
   - `python3 $LB_CLIENT forecast-eps <SYMBOL>`：EPS 预测，部分 A 股可能无数据
   - `python3 $LB_CLIENT company <SYMBOL>` / `executive` / `shareholder-top`：公司、管理层和主要股东
   - `python3 $LB_CLIENT kline <SYMBOL> --period day --count 260`：K 线（可用于计算 52 周高低）
   - `python3 $LB_CLIENT capital <SYMBOL>`：资金分布（大单/中单/小单净额）
   - ⚠️ **A 股持仓不可查**（`positions` 只返回港美股账户），但行情/基本面/资金流都正常可用
3. **第三优先：官方披露与网页研究**（用于交叉验证、补齐历史口径和定性信息）
   - A股：巨潮资讯网（官方）> 东方财富 > 新浪财经 > 雪球
   - 港股：港交所 HKEXnews > 富途 > 新浪港股 > 雪球
   - 美股：SEC EDGAR（10-K/10-Q）> Yahoo Finance > Macrotrends > Stockanalysis.com

两个 Agent 必须交叉验证至少 2 个数据源（longbridge 算一个），并在 JSON 里标注 `data_source` 和 `as_of_date`。如果 longbridge 返回空或字段缺失，只补抓缺失字段并在 JSON 里注明。任何缺失值写 `null`，不得用 `0` 或未经验证的推算值代替。

### Step 3：等待阶段 1 完成，并行调用 4 个分析 Agent（阶段 2）

阶段 1 的两个 JSON 落地后，在同一阶段并行启动 4 个子代理并等待完成。不要在主线程里用 Python 模拟分析。这 4 个维度（经济模型、PESTEL、波特、账面资产）的判断要保持独立视角——交给 4 个 agent 分别做，主代理再综合。

1. **economic_model_analyst** — 盈利/成长/营运/偿债四大能力 + 5 年趋势判断
2. **pestel_analyst** — 政治/经济/社会/技术/环境/法律 六维 + 动态趋势（↗↘→）
3. **porter_five_forces_analyst** — 五力 + 第六力（互补品）+ 动态趋势
4. **asset_valuator** — 净现金、股权投资（上市/非上市分开估）、固定资产、其他资产

每个 Agent 的详细 prompt 在 `references/sub_agent_prompts.md` 第 3-6 节。调用形式示意：

```
Agent(description="经济模型分析", prompt="<sub_agent_prompts.md 第 3 节> + 已抓到的 financial_statements.json 路径")
Agent(description="PESTEL 动态分析", prompt="<sub_agent_prompts.md 第 4 节>")
Agent(description="波特六力分析", prompt="<sub_agent_prompts.md 第 5 节>")
Agent(description="账面资产估值", prompt="<sub_agent_prompts.md 第 6 节> + financial_statements.json 路径")
```

**关键要求**：让每个 Agent 把结果写成结构化 JSON（不只是 markdown），保存在 workspace 里；
这样后续 DCF agent 和 `render_dashboard.py` 都能机器读取。文件名约定：`economic_model.json`、`pestel.json`、`porter.json`、`asset_valuation.json`。

### Step 4：阶段 2 完成后，调用 DCF agent（阶段 3）

主代理聚合阶段 2 的数据，再次使用当前平台的真实子代理能力调用 `dcf_valuator`（见 `references/sub_agent_prompts.md` 第 7 节）。

传入 Agent 的信息必须包括：
- 财务数据（营收、净利润、经营现金流、CAPEX、折旧摊销、总负债、现金）
- 经济模型分析得出的长期可持续增长率判断
- 行业格局（波特五力结论）—— 判断护城河能否支撑超额回报的年限
- 宏观趋势（PESTEL 结论）—— 影响终值假设
- 总股本（用于算每股价值）
- 当前无风险利率（10 年期国债收益率，根据市场选 CN/HK/US）

Agent 必须输出乐观/中性/悲观三档估值，且内部用 Python 做 DCF 计算（不允许口算）。

### Step 5：主代理做 11 维度综合分析

主代理读完所有子 agent 的产出后，**自己**做下面这些事：

#### 5.1 计算公司总价值

```
公司总价值 = DCF 经营价值 + 账面资产价值（来自 asset_valuator 的非经营资产部分）
每股总价值 = 公司总价值 / 总股本（来自 stock_quote.json）
```

| 情景 | DCF 价值 | 账面资产价值 | 公司总价值 | 每股价值 |
|------|----------|--------------|------------|----------|
| 乐观 | xxx 亿   | xxx 亿       | xxx 亿     | xxx 元   |
| 中性 | xxx 亿   | xxx 亿       | xxx 亿     | xxx 元   |
| 悲观 | xxx 亿   | xxx 亿       | xxx 亿     | xxx 元   |

#### 5.2 安全边际判断

当前股价 vs 中性情景每股价值：
- 股价 < 60% 中性价 → 深度低估，重点关注
- 股价 60-80% 中性价 → 有安全边际，可分批买入
- 股价 80-100% 中性价 → 合理估值，持有不加仓
- 股价 > 100% 中性价 → 高估，观望/减仓

#### 5.3 11 维度打分（1-5 分）

逐一评分，**每个维度都必须给出理由和证据**。打分标准见 `references/scoring_rubric.md`：

1. **业务简单性**（能否一句话讲清楚）
2. **经济模型**（ROE、毛利率、现金流质量、趋势）
3. **商业模式**（如何赚钱，是否长期可持续）
4. **企业文化与基因**（使命愿景、创始人/CEO 风格、历史上是否帮公司渡过危机）
5. **护城河**（品牌/网络效应/成本优势/转换成本/规模/专利——5 大类是否具备、强度趋势）
6. **管理团队**（CEO 诚信、资本配置记录、执行力、激励机制）
7. **PESTEL**（综合 pestel_analyst 输出）
8. **动态波特五力**（综合 porter_five_forces_analyst 输出）
9. **安全边际**（基于 5.1-5.2 的结果）
10. **第二层思维**（如果明显低估，市场错在哪？问题暂时还是结构性？如何验证？）
11. **致命风险**（最坏情况推演 + 管理层应对）

#### 5.4 给出投资建议

综合打分加权（见 rubric）：
- 总分 ≥ 45/55：强烈推荐进入深度研究名单
- 总分 40-44：值得关注，等更好买点
- 总分 35-39：中性
- 总分 < 35：暂不考虑

**必须包含**：
- 明确的买入价格带（基于中性估值的 60-70%）
- 核心跟踪指标（未来 4 个季度要看什么数据验证假设）
- 退出信号（什么情况下应卖出）

#### 5.5 把结果写成 `analysis_summary.json`（`render_dashboard.py` 的唯一输入）

主代理使用当前平台的文件写入能力，把上面所有结论写成一个结构化 JSON，保存到 `<workspace>/company_data/<公司名>/analysis_summary.json`。**字段必须齐全**，缺了脚本对应区块就空白：

```json
{
  "company": { "name": "贵州茅台", "code": "600519", "exchange": "上交所", "industry": "白酒", "currency": "CNY" },
  "report_date": "2026-04-21",
  "scores": {
    "业务简单性":    { "score": 5, "reason": "一句话" },
    "经济模型":      { "score": 5, "reason": "…" },
    "商业模式":      { "score": 5, "reason": "…" },
    "企业文化":      { "score": 4, "reason": "…" },
    "护城河":        { "score": 5, "reason": "…" },
    "管理团队":      { "score": 4, "reason": "…" },
    "PESTEL":        { "score": 3, "reason": "…" },
    "波特五力":      { "score": 4, "reason": "…" },
    "安全边际":      { "score": 3, "reason": "…" },
    "第二层思维":    { "score": 4, "reason": "…" },
    "致命风险":      { "score": 3, "reason": "…" }
  },
  "valuation": {
    "bear":  { "dcf": 0, "book": 0, "total": 0, "per_share": 0 },
    "base":  { "dcf": 0, "book": 0, "total": 0, "per_share": 0 },
    "bull":  { "dcf": 0, "book": 0, "total": 0, "per_share": 0 },
    "current_price": 0,
    "margin_of_safety_pct": 0,
    "wacc_used": 0,
    "terminal_growth_used": 0
  },
  "moat": {
    "brand":          { "strength": 5, "trend": "→", "note": "…" },
    "switching_cost": { "strength": 3, "trend": "→", "note": "…" },
    "network_effect": { "strength": 1, "trend": "→", "note": "…" },
    "cost_advantage": { "strength": 3, "trend": "↗", "note": "…" },
    "scale":          { "strength": 5, "trend": "→", "note": "…" }
  },
  "pestel": {
    "political":    { "score": 3, "trend": "→", "drivers": ["…"] },
    "economic":     { "score": 3, "trend": "↘", "drivers": ["…"] },
    "social":       { "score": 4, "trend": "↗", "drivers": ["…"] },
    "technological":{ "score": 3, "trend": "→", "drivers": ["…"] },
    "environmental":{ "score": 3, "trend": "→", "drivers": ["…"] },
    "legal":        { "score": 3, "trend": "→", "drivers": ["…"] }
  },
  "porter": {
    "existing_rivalry":   { "score": 4, "trend": "→", "note": "…" },
    "new_entrants":       { "score": 5, "trend": "→", "note": "…" },
    "substitutes":        { "score": 3, "trend": "↘", "note": "…" },
    "supplier_power":     { "score": 4, "trend": "→", "note": "…" },
    "buyer_power":        { "score": 4, "trend": "→", "note": "…" },
    "complementors":      { "score": 3, "trend": "→", "note": "…" }
  },
  "second_level_thinking": {
    "market_view":   "市场主流观点……",
    "our_view":      "我们的反共识判断……",
    "catalysts":     [{ "date": "2026 Q2", "event": "…" }],
    "verification":  ["跟踪指标 1", "跟踪指标 2"]
  },
  "fatal_risks": [
    { "risk": "塑化剂/食品安全危机", "probability": "低", "impact": "高", "mgmt_response": "…" },
    { "risk": "消费降级冲击高端白酒", "probability": "中", "impact": "中", "mgmt_response": "…" }
  ],
  "investment_decision": {
    "recommendation_band": "强烈推荐|优质公司等买点|中性跟踪|暂不考虑|避免",
    "buy_price_range": [1400, 1500],
    "tracking_metrics": ["下季度营收同比", "合同负债", "经销商数量"],
    "exit_triggers": ["ROE 连续 2 年 < 20%", "管理层变更"]
  },
  "data_sources": ["巨潮资讯网", "东方财富"],
  "data_as_of": "2026-04-21"
}
```

分数维度名**必须**用这 11 个中文字符串作为 JSON 键（不要翻成英文），因为 `render_dashboard.py` 会直接拿这些键当雷达图标签渲染到 HTML。

### Step 6：生成 HTML 决策仪表盘（必须调 `render_dashboard.py`）

**这里只有一条路径**：用 shell 运行 `scripts/render_dashboard.py`。重申铁律 2——不要自己写 HTML。

```bash
python "$SKILL_DIR/scripts/render_dashboard.py" \
  --data-dir <workspace/company_data/公司名> \
  --analysis-json <analysis_summary.json> \
  --output <output.html>
```

脚本会读取：
- `stock_quote.json`（实时行情）
- `financial_statements.json`（5 年趋势图数据）
- `analysis_summary.json`（11 维度打分 + 估值 + 投资建议，由主代理写）
- 4 个子 agent 的分析报告

输出：单个独立的 `.html` 文件（内联所有 CSS/JS/Chart.js）。默认放在当前 workspace 的 `outputs/` 下；如果用户指定了目录则使用用户目录。文件名格式：`<公司名>_价值分析_<日期>.html`。

仪表盘包含 12 个区块，详见 `references/dashboard_structure.md`：
1. 公司标题 + 综合评分雷达图
2. 一句话结论横幅（强推/关注/中性/回避）
3. 11 维度雷达图 + 分项评分卡
4. 财务五年趋势图（ROE、营收、净利润、自由现金流、毛利率）
5. 三档估值 bar chart（乐观/中性/悲观 + 当前股价横线）
6. 安全边际可视化
7. 护城河分析（5 大类，强度条形）
8. PESTEL 雷达图（动态趋势箭头标注）
9. 波特五力蜘蛛图
10. 第二层思维卡片
11. 致命风险清单
12. 跟踪指标 + 买入/卖出触发条件 + 免责声明

---

## 参考文件

- `references/sub_agent_prompts.md` — 7 个子 agent 的完整 prompt 模板，直接复制使用
- `references/data_sources.md` — A股/港股/美股的数据源优先级和 URL 模板
- `references/longbridge-connector.md` — Codex/ChatGPT Longbridge connector/app 工具映射
- `references/scoring_rubric.md` — 11 维度 1-5 分打分的具体标准和例子
- `references/dashboard_structure.md` — HTML 仪表盘 12 个区块的设计规范
- `references/dcf_methodology.md` — DCF 估值方法论（无风险利率、WACC、终值、敏感性）
- `scripts/render_dashboard.py` — 生成 HTML 的 Python 脚本（模板内联在脚本里）

---

## 关键原则

1. **客观中立**：基于数据和事实，不被市场情绪带偏。如果数据指向"暂不推荐"，就说暂不推荐。
2. **数据时效**：所有数据必须注明来源和时间戳。财报用最新年报/季报。
3. **数据交叉验证**：关键指标（市值、ROE、净利润）至少从 2 个数据源核对。
4. **标注不确定性**：如果数据缺失或质量差（如新上市公司历史不足 5 年），在报告里明确说明。
5. **风险提示**：报告结尾必须有免责声明。
6. **并行执行**：阶段 1 和阶段 2 的 Agent 调用必须在同一消息里并发，不要串行等待。
7. **不要过度简化**：11 个维度每一个都要认真分析，不要糊弄给分。
8. **用 Python 做精确计算**：DCF、账面估值等涉及金额的地方，让 Agent 写脚本算，不要心算。

---

## 错误处理

| 错误情况 | 处理方式 |
|----------|----------|
| 数据源全部失败 | 让 Agent 在 JSON 里标注 `"data_quality": "degraded"`，基于知识库做定性分析，主代理在报告中明确标注"数据非实时" |
| 公司不足 5 年历史 | 按实际年限分析，在经济模型 agent 里降低趋势判断的置信度 |
| 多元化集团（如伯克希尔） | PESTEL 和五力都要对营收占比 > 10% 的业务板块分别做 |
| 亏损公司 | DCF 无法用传统自由现金流模型，Agent 应该用可比估值（PS、EV/EBITDA）或账面资产法，在报告里说明 |
| 金融类公司（银行/保险） | DCF 应用 DDM 或 P/EV 方法，而不是 FCFF。在 `references/dcf_methodology.md` 中有说明 |

---

## 报告结尾

所有生成的 HTML 报告末尾都要包含：

```
═══════════════════════════════════════════════════════════════
本报告由 AI 公司价值分析专家生成
数据来源：[巨潮资讯网 / 东方财富 / SEC EDGAR / ...]
数据时点：[YYYY-MM-DD]
免责声明：本报告仅供学习研究，不构成任何投资建议。
═══════════════════════════════════════════════════════════════
```

（这段字面模板已经内置在 `render_dashboard.py` 里，按脚本走自动会带上。）

---

## 交付前自检清单

交给用户 HTML 之前，主代理心里过一遍：

1. 执行记录里是否真的有 7 个子代理任务（2 + 4 + 1）？如果全是主线程 shell/文件读写，回到铁律 1 重跑。
2. HTML 是不是由 `python .../render_dashboard.py` 生成的？有没有自己用 Write 写过一个 `.html`？如果是自己写的，丢弃，用脚本重来（铁律 2）。
3. `analysis_summary.json` 的 11 个维度 key 是不是都用了中文字符串（"业务简单性"、"经济模型"…"致命风险"）？
4. HTML 打开后能看到：雷达图、5 年财务趋势、DCF 三档横条、PESTEL、波特五力、致命风险 Top 3、买入价格带？
5. 所有金额/比率是不是真的来自 agent 抓取的财报，而不是你脑补的数字？
6. HTML 是否放在用户指定目录；未指定时是否放到当前 workspace 的 `outputs/`？
7. 最后是否返回了当前客户端可打开的文件链接和绝对路径？

过不去就别急着交，补齐再交。
