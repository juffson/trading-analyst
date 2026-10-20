# Trading Analyst

个人交易分析工具集，支持 **OpenAI Codex / ChatGPT** 与 **Claude Code**。三个可独立安装的 Skill 覆盖买入前研究、持有期操作和量化回测；`quant-studio/` 提供本地研究工作台与自动化运行器。Codex/ChatGPT 优先使用 Longbridge connector/app，缺少接口时回退 CLI/OpenAPI。

## 仓库结构

```
skills/                  三个可独立安装的 Agent Skill（本仓库的主体）
├── trading-analyst/       持有期交易操作
├── company-deep-dive/     买入前深度研究
└── quant-backtest/        QuantScript 量化回测
quant-studio/            本地工具：Quant Lab 工作台（Rust web UI）+ auto-trader 自动化循环
├── src/ static/           因子浏览、回测看图、模拟/实盘视图、Skill 命令生成
└── auto-trader/           「研究→回测→模拟交易」cron 流水线（Python）
research/                公开研究站：标的登记、纯分析 JSON 与静态页面资源
├── reports/              按标的和交易日归档，修订保留原版
└── CONTRACT.md           Task 写入合同
scripts/                 研究校验、静态站生成器与飞书 Wiki 发布器
.github/workflows/       校验、GitHub Pages 发布与飞书自动同步
```

**`skills/` 下的是 Skill，`quant-studio/` 是本地工具**，不是 Skill、不用装进
`~/.claude/skills/`。工具会读 `skills/` 里的 Skill 目录，所以别把 `skills/` 挪走或改名。

## 公开研究站（GitHub Pages）

部署后的[研究总览](https://juffson.github.io/trading-analyst/)采用“总览 → 标的 → 交易日报告”三级结构，保存最新判断、真实历史行情及每次报告的分析价位。每份报告都有独立地址，历史原版和后续修订可分别查看。

数据入口是 `research/reports/<symbol>/YYYY-MM-DD.json`。启用 Pages 后，ChatGPT Task 通过 GitHub 插件提交一份符合合同的纯分析 JSON，GitHub Actions 自动校验、重建索引并部署网页，无需 Task 操作网页或修改 HTML。报告格式与任务提示见 [research/README.md](research/README.md)，完整字段规则见 [research/CONTRACT.md](research/CONTRACT.md)。

Pages 默认关闭，工作流的构建和部署作业均跳过。首次部署需要在仓库 **Settings → Pages → Source** 选择 **GitHub Actions**，并在 **Settings → Secrets and variables → Actions → Variables** 设置 Repository variable `ENABLE_RESEARCH_PAGES=true`。仅使用飞书时，保持该变量未设置或设为 `false`。

本地预览使用 Python 3.10+：

```bash
python3 scripts/validate_research.py --root .
python3 scripts/build_research_site.py --root . --output _site --base-path ''
python3 -m http.server 8000 --directory _site
```

公开站只接收市场和公司分析；个人账户、持仓与交易记录不进入报告。现有 Skill 与 Quant Studio 继续使用原来的路径和运行方式。

## 飞书 Wiki 自动发布

工作流 **Publish research to Feishu** 读取同一份纯分析 JSON，在配置的 Wiki 入口下建立独立的“总览 → 标的 → 日期与修订报告”分支，并上传真实历史价格趋势图。每日提交 `research/` 数据即可触发同步；相同报告重跑跳过，部分失败可按已写内容恢复，日期报告保留原版。

在仓库 **Settings → Secrets and variables → Actions** 配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_WIKI_ROOT_TOKEN`。第三项推荐填写完整 Wiki 入口链接。进入 **Actions → Publish research to Feishu → Run workflow**，先运行 `check` 只读检查，再运行 `publish` 写入。使用长期 App ID/Secret，每次运行自行获取应用身份凭证，无需手工保存两小时 Token。

未配置凭据时可运行 `validate` 检查渲染和图表，不调用飞书；自动 push 则提示配置缺失并跳过同步。飞书发布与 Pages 部署相互独立。完整权限、变量、首次运行和故障恢复说明见 **[research/FEISHU.md](research/FEISHU.md)**。

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
- **数据源分层** — Longbridge connector/app 优先获取行情、估值、评级预测及近 5 年核心财报；客户端通过 `FundamentalContext` 或 CLI 回退，详细科目与关键数字再用巨潮、港交所或 SEC EDGAR 等官方披露核对
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

### [`quant-studio/`](quant-studio/) — Quant Lab 量化研究工作台
本地 Rust web 应用（在 `quant-studio/` 运行 `cargo run`，监听 `127.0.0.1:4870`）+ 收在里面的 `auto-trader/`：
浏览因子片段库、跑 Longbridge 回测看图、跟踪因子表现漂移，模拟交易 / 实盘账户 / 两个分析 Skill
接进同一页面；auto-trader 负责 cron 流水线（`researching`→`calibrating`→`paper_trading`，
默认 `signal_only`，真实下单永远手动 `executor.confirm_order()`）。
详见 [quant-studio/README.md](quant-studio/README.md)。

## 前置依赖

- OpenAI Codex（桌面端、CLI 或 IDE 扩展），或 Claude Code
- Codex/ChatGPT：安装并授权 Longbridge plugin/connector
- 回退环境：[Longbridge Terminal](https://github.com/longbridge/longbridge-terminal) 已登录，或配置 LongPort OpenAPI 凭据
- Python 3.10+
- 运行 Quant Lab 工作台另需 Rust 工具链（支持 Rust 2024 edition）

### 配置 Longbridge 账号（跑本地工具 / OpenAPI 回退时需要）

仓库不含真实凭据。到 [Longbridge 开发者平台](https://open.longbridge.com/docs) 创建应用、申请
OpenAPI 权限，拿到 `App Key` / `App Secret` / `Access Token`，再 `export LONGPORT_APP_KEY`
等环境变量。行情与交易权限分开申请；Access Token 会过期。完整步骤见
[quant-studio/README.md](quant-studio/README.md) 的「配置自己的 Longbridge 账号」。

使用本地工作台时，在仓库根目录运行：

```bash
cd quant-studio
cargo run
# 打开 http://127.0.0.1:4870
```

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
