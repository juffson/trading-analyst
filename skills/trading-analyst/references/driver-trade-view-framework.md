# Driver → Trade View 分析框架

这套框架把相邻项目中的 Factor/Driver 证据模型与可执行结论模型压缩成个人交易报告可用的结构。它解决三个问题：发生了什么、证据有多强、是否适合按当前策略交易。

## 1. 三类分数必须分开

不要把所有信息混成一个“综合分”。报告至少区分：

| 字段 | 范围 | 含义 | 不代表什么 |
|---|---:|---|---|
| `driver_strength` | 0-100 / null | 事件或异常本身有多显著 | 不代表现在值得买卖 |
| `confidence` | 0-100 | 对方向、归因和数据完整性的把握 | 不代表盈亏概率 |
| `strategy_fit_score` | 0-100 | 当前价格、趋势、催化和风控是否适合执行策略 | 不等于涨跌方向 |

Factor group 的 `relevance_score` 是归因权重，逐项保留，不并入上述三者。资讯推荐排序分也只用于排序，不可冒充信号强度。

## 2. Driver / Factor 标准化

每个关键催化或因子尽量保留下列字段；取不到时写 `null` 或 `unknown`，不得猜造：

```json
{
  "id": "fact-or-local-id",
  "name": "财报收入超预期",
  "type": "fundamental",
  "sub_type": "earnings",
  "occur_time": "2026-07-15T08:00:00Z",
  "data_status": "completed",
  "direction": "long",
  "direction_reasoning": "收入和指引同时超过一致预期",
  "trigger_condition": "收入同比增速 > 市场预期",
  "raw_value": "+18% YoY",
  "driver_strength": 84,
  "strength_basis": "z=3.2, HIGH threshold=3.0",
  "confidence": 88,
  "driving_cause": "上调未来两个季度盈利预期",
  "evidence_sources": [{"name": "公司公告", "url": "https://..."}]
}
```

规则：

- `direction` 只使用 `long` / `short` / `neutral`。多个因子按多空票数汇总；票数相同为 `neutral`，并显式展示冲突。
- 只有 `completed` 且时效仍有效的证据进入主评分。`pending` / `stale` / `filtered` 只能放到观察区。
- 有异常检测时，同时展示测试方法、原始结果、阈值和显著性。按 `NORMAL/LOW/MEDIUM/HIGH` 映射为 0-24 / 25-49 / 50-74 / 75-100；有 z-score 时可在档内线性定位。
- 没有异常检测但数据源提供事件强度（0-1）时，可将其乘以 100 并四舍五入，同时把依据写为 `provider driver strength`。
- 两者都没有时 `driver_strength=null`，不可用模型主观补分。
- 对宏观/新闻事件逐标的保留 `driving_cause`，不能把行业利好机械复制给所有股票。
- 因子归因按 `relevance_score` 降序展示 Top 3，并保留 `impact` 和 `reasoning`。

## 3. Strategy Fit 评分（100 分）

这是适配活跃交易和做 T 的评分表。每项都要输出 `score/max_score`、状态、证据和扣分原因。

| 维度 | 满分 | 主要证据 |
|---|---:|---|
| 趋势与相对强弱 | 25 | 日/周趋势、均线排列、相对指数或行业强弱 |
| 技术确认 | 20 | MACD、RSI/KDJ、布林带、突破/背离、关键位 |
| 量价与资金 | 15 | 成交量确认、资金流、大中小单结构、流动性 |
| 驱动质量与时效 | 15 | driver strength、来源级别、新鲜度、传导逻辑 |
| 基本面与估值 | 10 | 盈利趋势、预期修正、估值分位、机构覆盖 |
| 风险收益与可执行性 | 15 | 三情景赔率、止损距离、仓位、市场交易规则 |

评分解释：

- `80-100`：策略高度契合；无硬门槛失败时可输出可执行信号。
- `65-79`：条件式观察；列明还缺哪个确认，不追价。
- `<65`：当前不执行新动作；仍输出 bearish/avoid 等有价值结论，不能把报告整体过滤掉。

硬门槛独立于总分。任一失败时 `should_execute=false`：

- 核心行情或价格锚点过期/缺失；
- 关键催化只有二手传闻且无法核验；
- 没有明确失效条件或止损后风险收益不成立；
- 流动性、最小交易单位或仓位使计划不可执行；
- 计划违反市场规则，例如 A 股把当日新买份额用于当日正 T 卖出。

`analysis_quality` 与投资观点分离：取数时间、来源、字段完整性和冲突处理不合格时设为 `degraded` 或 `blocked`。低策略分不等于分析质量差，bearish 也不等于无效信号。

## 4. 五级 Outlook 与核心结论

机器字段固定使用英文枚举，展示字段再本地化：

- `Strong Bullish` → 强烈看多
- `Bullish` → 看多
- `Neutral` → 中性
- `Bearish` → 看空
- `Strong Bearish` → 强烈看空

Outlook 由多空证据的方向和一致性决定；`strategy_fit_score` 表示“是否适合执行该方向”，两者不可互相替代。

每份个股分析和交易计划开头先给 Trade View：

```json
{
  "as_of": "2026-07-15T14:30:00+08:00",
  "analysis_quality": "pass",
  "should_execute": true,
  "skip_reason": null,
  "outlook": "Bullish",
  "outlook_desc": "看多",
  "recommendation": "回踩确认后分批买入；不追突破首根 K 线",
  "strategy_fit_score": 84,
  "confidence": 81,
  "key_driver": "业绩与指引同步上修",
  "analysis_price": 187.23,
  "conservative_price": 178.0,
  "benchmark_price": 198.0,
  "optimistic_price": 212.0,
  "analysis_price_to_benchmark_pct": 5.75,
  "invalidation": "日线收盘跌破 178 且次日不能收回"
}
```

价格统一以 `analysis_price` 为锚，并给保守/基准/乐观三个场景。必须说明价格的计算依据和预测周期；如果用了机构目标价，标注日期，偏离现价超过 30% 时提醒可能过时。方向判断不能只由目标价决定。

## 5. 报告层次

完整分析 JSON 与人读卡片同时保留：

1. **Trade View**：建议、五级 outlook、策略分、置信度、是否执行、三情景价格、失效条件。
2. **Quick Takeaways**：3-5 条最重要的多空结论。
3. **Drivers & Evidence**：主驱动、逐标的传导原因、来源、时效和异常强度。
4. **Score Breakdown**：六项评分、Top 3 驱动、多空票数和冲突。
5. **Execution Rules**：`entry` / `hold` / `exit` / `sizing`，每项都有状态、动作、原因。
6. **Scenario Valuation**：保守/基准/乐观价格、触发条件、路径、概率、风险提示。
7. **Sources & Data Status**：取数时间、原始链接、缺失与降级说明。

结论必须可证伪：写清楚“什么条件发生时观点失效”，不能只给模糊的看多/看空描述。
