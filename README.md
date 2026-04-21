# Trading Analyst

个人交易分析 Skill 集合 —— 基于 [Claude Code](https://claude.ai/claude-code)，通过 [Longbridge](https://longbridge.com) CLI 实时获取行情数据，覆盖**买入前深度研究**和**持有期交易操作**两个阶段。

## 仓库包含的 Skill

### [`trading-analyst/`](trading-analyst/) — 持有期交易操作
短中线操作视角。已持有或准备近期买卖时用。

- **持仓审视** — 拉取账户持仓，评估仓位分布与风险（A 股持仓需手动提供，longbridge 不支持查询）
- **个股技术分析** — 均线/MACD/RSI/KDJ/布林带/斐波那契等多指标综合研判
- **分时复盘** — 盘中走势分析，分时段成交量与资金流向
- **交易计划制定** — 关键价位操作表、做T明细、多情景概率估计、降成本测算
- **跟进复盘** — 计划 vs 实际对比，更新价位与情景概率
- **操作记录** — 成交记录归档，成本变化追踪

触发词举例：「帮我看看 COIN.US」「做T计划」「9988.HK 支撑压力位」「查看持仓」

### [`company-deep-dive/`](company-deep-dive/) — 买入前深度研究
长期投资视角。买之前判断公司值不值得买时用。

- **11 维度价值评分** — 业务简单性 / 经济模型 / 商业模式 / 企业文化 / 护城河 / 管理团队 / PESTEL / 波特五力 / 安全边际 / 第二层思维 / 致命风险
- **DCF 估值** — 乐观/中性/悲观三档，Python 精确计算
- **数据源分层** — longbridge 优先（行情、PE/PB、EPS 预测、机构评级），爬取兜底（5 年历史三表从巨潮/SEC EDGAR 取）
- **并行子代理架构** — 阶段 1 抓数据 2 个 Agent、阶段 2 分析 4 个 Agent、阶段 3 DCF 1 个 Agent，主代理综合打分
- **HTML 决策仪表盘** — 雷达图 + 财务趋势 + DCF 横条 + 买入价格带

触发词举例：「X 值不值得买」「估值一下 X」「X 的 DCF」「X 的护城河」「深度分析 X 公司」

## 前置依赖

- [Claude Code](https://claude.ai/claude-code)
- [Longbridge Terminal](https://github.com/longbridge/longbridge-terminal) — 已登录并配置好行情权限

## 安装

克隆仓库后，把两个 skill 目录链接或复制到 `~/.claude/skills/`：

```bash
git clone https://github.com/juffson/trading-analyst.git
cd trading-analyst
ln -sf "$PWD/trading-analyst"     ~/.claude/skills/trading-analyst
ln -sf "$PWD/company-deep-dive"   ~/.claude/skills/company-deep-dive
```

## 两个 Skill 的关系

| 场景 | 用哪个 | 输出 |
|------|--------|------|
| "XX 值不值得买" | `company-deep-dive` | 决策仪表盘 HTML |
| "帮我分析 XX 的技术面" | `trading-analyst` | 技术分析报告 |
| "做T计划" / "支撑压力位" | `trading-analyst` | 交易计划 HTML |
| "XX 的 DCF 估值" | `company-deep-dive` | 估值三档 + 安全边际 |
| "查看持仓" | `trading-analyst` | 持仓审视 |

## 免责声明

本工具仅供个人学习与研究使用，所有分析结果不构成投资建议。投资有风险，决策需谨慎。
