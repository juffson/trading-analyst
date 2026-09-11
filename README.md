# Trading Analyst

个人交易分析工具集，支持 **OpenAI Codex / ChatGPT** 与 **Claude Code**。Codex/ChatGPT 优先直连 Longbridge connector/app；其他环境或连接器不可用时回退 CLI/OpenAPI。覆盖买入前研究、持有期操作和量化回测。

## 仓库结构

```
skills/                  三个可独立安装的 Agent Skill（本仓库的主体）
├── trading-analyst/       持有期交易操作
├── company-deep-dive/     买入前深度研究
└── quant-backtest/        QuantScript 量化回测
auto-trader/             本地工具：把三个 Skill 串成「研究→回测→模拟交易」的自动循环
quant-studio/            本地工具：Rust web 应用，人工浏览因子、跑回测看图、跟踪因子表现
```

**`skills/` 下的是 Skill，根目录下的 `auto-trader/` 和 `quant-studio/` 是独立工具**，不是 Skill、不用装进
`~/.claude/skills/`，各自有自己的 README 和运行方式。两个工具都会读 `skills/` 里的 Skill 目录，
所以别把 `skills/` 挪走或改名。

## 仓库包含的 Skill

### [`skills/trading-analyst/`](skills/trading-analyst/) — 持有期交易操作
短中线操作视角。已持有或准备近期买卖时用。

- **持仓审视** — 拉取账户持仓，评估仓位分布与风险（A 股持仓需手动提供，longbridge 不支持查询）
- **个股技术分析** — 均线/MACD/RSI/KDJ/布林带/斐波那契等多指标综合研判
- **分时复盘** — 盘中走势分析，分时段成交量与资金流向
- **交易计划制定** — 关键价位操作表、做T明细、多情景概率估计、降成本测算
- **跟进复盘** — 计划 vs 实际对比，更新价位与情景概率
- **操作记录** — 成交记录归档，成本变化追踪

触发词举例：「帮我看看 COIN.US」「做T计划」「9988.HK 支撑压力位」「查看持仓」

### [`skills/company-deep-dive/`](skills/company-deep-dive/) — 买入前深度研究
长期投资视角。买之前判断公司值不值得买时用。

- **11 维度价值评分** — 业务简单性 / 经济模型 / 商业模式 / 企业文化 / 护城河 / 管理团队 / PESTEL / 波特五力 / 安全边际 / 第二层思维 / 致命风险
- **DCF 估值** — 乐观/中性/悲观三档，Python 精确计算
- **数据源分层** — longbridge 优先（行情、PE/PB、EPS 预测、机构评级），爬取兜底（5 年历史三表从巨潮/SEC EDGAR 取）
- **并行子代理架构** — 阶段 1 抓数据 2 个 Agent、阶段 2 分析 4 个 Agent、阶段 3 DCF 1 个 Agent，主代理综合打分
- **HTML 决策仪表盘** — 雷达图 + 财务趋势 + DCF 横条 + 买入价格带

触发词举例：「X 值不值得买」「估值一下 X」「X 的 DCF」「X 的护城河」「深度分析 X 公司」

### [`skills/quant-backtest/`](skills/quant-backtest/) — QuantScript 量化回测
策略验证视角。想知道一套规则历史上能不能赚钱时用。

- **跑 QuantScript** — 优先走 connector/app 的 `quant_run`，不可用时回退 `longbridge quant run` CLI / OpenAPI
- **回测报告** — 夏普比率、最大回撤、胜率、盈亏比、逐笔成交、净值/回撤/买入持有曲线
- **动态调参** — 同一脚本换参数批量对比，做参数敏感性分析
- **图表数据** — `plot()` 输出的指标序列可取出来画图（`scripts/chart.py`）
- **接口实测记录** — `references/api.md` 记了 `report_json`/`chart_json` 的真实字段命名和坑

触发词举例：「回测 RSI 策略」「跑一下这个策略」「调一下参数再测」「这策略跑赢买入持有了吗」

## 本地工具（不是 Skill）

### [`auto-trader/`](auto-trader/) — 自动化循环
把三个 Skill 串成持续运转的流水线：`researching`（深度研究）→ `calibrating`（回测调参）→
`paper_trading`（模拟交易）→ 周期性复盘。默认 `signal_only`：只生成交易计划，不碰风控也不下单；
真实下单永远是手动调 `executor.confirm_order()`。详见 [auto-trader/README.md](auto-trader/README.md)。

### [`quant-studio/`](quant-studio/) — 因子/回测工作台
本地 Rust web 应用（`cargo run`，绑 `127.0.0.1:4870`）：浏览因子片段库、跑 Longbridge 回测看图、
跟踪同一个因子在不同时间测出来的表现漂移，另外把模拟交易、实盘账户、两个分析 Skill 接进同一个页面。
详见 [quant-studio/README.md](quant-studio/README.md)。

## 前置依赖

- OpenAI Codex（桌面端、CLI 或 IDE 扩展），或 Claude Code
- Codex/ChatGPT：安装并授权 Longbridge plugin/connector
- 回退环境：[Longbridge Terminal](https://github.com/longbridge/longbridge-terminal) 已登录，或配置 LongPort OpenAPI 凭据
- Python 3.10+

## 安装

### Codex / ChatGPT

把下面这段话直接交给 Codex/ChatGPT 的 Agent：

```text
请使用 $skill-installer 从 GitHub 仓库 juffson/trading-analyst 安装以下 Skill paths：
- skills/trading-analyst
- skills/company-deep-dive
- skills/quant-backtest
```

Agent 会从仓库中读取每个独立目录并安装到它自己的 Skill 目录。仓库不包含 `.agents/skills` 自动加载镜像，也不要求用户手动复制整个仓库。

也可以只安装其中一个，例如：

```text
请使用 $skill-installer 从 juffson/trading-analyst 的 skills/company-deep-dive path 安装这个 Skill。
```

安装完成后可显式输入 `$trading-analyst`、`$company-deep-dive` 或 `$quant-backtest`，也可以用自然语言触发。

### Claude Code（手动安装）

Claude Code 继续使用 `~/.claude/skills/`：

```bash
git clone https://github.com/juffson/trading-analyst.git
cd trading-analyst
mkdir -p ~/.claude/skills
ln -sfn "$PWD/skills/trading-analyst"   ~/.claude/skills/trading-analyst
ln -sfn "$PWD/skills/company-deep-dive" ~/.claude/skills/company-deep-dive
ln -sfn "$PWD/skills/quant-backtest"    ~/.claude/skills/quant-backtest
```

## 三个 Skill 的关系

| 场景 | 用哪个 | 输出 |
|------|--------|------|
| "XX 值不值得买" | `company-deep-dive` | 决策仪表盘 HTML |
| "帮我分析 XX 的技术面" | `trading-analyst` | 技术分析报告 |
| "做T计划" / "支撑压力位" | `trading-analyst` | 交易计划 HTML |
| "XX 的 DCF 估值" | `company-deep-dive` | 估值三档 + 安全边际 |
| "查看持仓" | `trading-analyst` | 持仓审视 |
| "回测 RSI/均线策略" | `quant-backtest` | 回测指标 + 图表数据 |

## 跨平台约定

- Skill 遵循开放的 Agent Skills 目录格式：每个 Skill 都以带 `name` 和 `description` frontmatter 的 `SKILL.md` 为入口。
- 三个可独立安装的 Skill path 都在 `skills/` 下，不在 `.agents/skills` 下复制或镜像。
- Skill 内调用脚本时先解析 Skill 的绝对路径，不假设当前工作目录就是 Skill 目录。
- Codex/ChatGPT 检测到 Longbridge connector/app 时直接调用工具，不再先运行 `lb_client.py`。
- connector 不可用或缺少接口时才回退 CLI/OpenAPI，并向用户说明原因。
- 需要联网研究时，使用当前平台可用的网页搜索工具，并给出来源链接。
- `company-deep-dive` 需要真实子代理并行工作：Codex 使用原生 subagent workflow，Claude Code 使用对应的 Agent/Task 工具。
- 会话交接文件采用平台无关格式；仅在当前平台能可靠获得任务或会话 ID 时才记录该 ID。

## 免责声明

本工具仅供个人学习与研究使用，所有分析结果不构成投资建议。投资有风险，决策需谨慎。
