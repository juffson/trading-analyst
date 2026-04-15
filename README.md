# Trading Analyst

个人交易分析助手 —— 基于 [Claude Code](https://claude.ai/claude-code) Skill，通过 [Longbridge](https://longbridge.com) CLI 实时获取行情数据，结合多维技术指标输出可操作的交易建议。

## 功能

- **持仓审视** — 拉取账户持仓，评估仓位分布与风险
- **个股深度分析** — 均线/MACD/RSI/KDJ/布林带/斐波那契等多指标综合研判
- **分时复盘** — 盘中走势分析，分时段成交量与资金流向
- **交易计划制定** — 关键价位操作表、做T明细、多情景概率估计、降成本测算
- **跟进复盘** — 计划 vs 实际对比，更新价位与情景概率
- **操作记录** — 成交记录归档，成本变化追踪

## 项目结构

```
├── SKILL.md                          # Claude Code Skill 定义
├── references/
│   ├── technical-analysis.md         # 技术指标计算方法参考
│   └── longbridge-commands.md        # Longbridge CLI 命令参考
└── scripts/
    └── calc_indicators.py            # 技术指标计算脚本
```

## 前置依赖

- [Claude Code](https://claude.ai/claude-code)
- [Longbridge Terminal](https://github.com/longbridge/longbridge-terminal) — 已登录并配置好行情权限

## 使用方式

1. 将本项目克隆到本地：
   ```bash
   git clone https://github.com/juffson/trading-analyst.git
   ```

2. 将 `SKILL.md` 安装为 Claude Code Skill（复制到 `~/.claude/skills/` 目录）

3. 在 Claude Code 中直接对话即可触发，例如：
   - "帮我看看 COIN.US"
   - "分析一下 603920.SH 的技术面"
   - "制定 9988.HK 的做T计划"
   - "查看持仓"

## 免责声明

本工具仅供个人学习与研究使用，所有分析结果不构成投资建议。投资有风险，决策需谨慎。
