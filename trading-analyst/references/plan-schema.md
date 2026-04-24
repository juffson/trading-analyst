# 计划与复盘的 JSON Schema

这份文档定义**交易计划（plan）**和**复盘（review）**的结构化存储格式。

为什么要结构化存 JSON，而不是只靠 HTML？因为复盘的时候 skill 需要可靠地把"当时说的关键价位、情景、做T触发条件"加载回来，和实际行情对比。HTML 是给人看的，JSON 是给 skill 读回来的。JSON 和 HTML 是**同源双份**：HTML 里写的东西都要能在 JSON 里找到对应字段。

## 使用时机

- **模式 4（交易计划制定）**：计划做完、HTML 输出后，**询问用户是否落盘**。用户同意并给出目录后，调用 `scripts/plan_io.py save-plan` 写入 JSON。
- **模式 5（跟进复盘）**：用户给出目录（或同一 session 内已记住的路径），调用 `scripts/plan_io.py load-latest-plan` 加载最新 plan.json 作为对比基线。产出 review 时也写 JSON。
- **模式 6（当日操作记录）**：如果该标的有历史 plan，把成交和 plan 的触发条件做一次 diff，追加到 review。

## Plan JSON schema

```json
{
  "version": "1",
  "symbol": "AAPL.US",
  "plan_date": "2026-04-23",
  "created_at": "2026-04-23T10:30:00+08:00",
  "market": "US",

  "snapshot": {
    "price": 187.23,
    "shares": 100,
    "cost_basis": 250.00,
    "market_value": 18723.00,
    "unrealized_pnl": -6277.00,
    "unrealized_pnl_pct": -25.11
  },

  "position_split": {
    "base_shares": 70,
    "active_shares": 30,
    "rationale": "底仓 70% 锁仓，活动仓 30% 做 T"
  },

  "price_levels": [
    {
      "price": 192.5,
      "type": "resistance",
      "meaning": "MA20 + Fib 38.2%",
      "action": "卖 10 股（倒 T）"
    },
    {
      "price": 175.0,
      "type": "support",
      "meaning": "前低 + MA60",
      "action": "买回 10 股（正 T 回补）"
    }
  ],

  "t_plans": [
    {
      "direction": "reverse_T",
      "trigger_price": 192.5,
      "trigger_condition": "突破 MA20 受压",
      "shares": 10,
      "exit_trigger_price": 185.0,
      "exit_condition": "回落到 MA5 支撑",
      "logic": "压力位减仓降成本"
    }
  ],

  "scenarios": [
    {
      "name": "乐观",
      "probability": 0.30,
      "trigger": "大盘回暖 + 财报超预期",
      "path": "反弹至 MA60 $210",
      "target_range": [205, 215],
      "recommended_strategy": "减仓 30%"
    },
    {
      "name": "中性",
      "probability": 0.50,
      "trigger": "维持现有震荡区间",
      "path": "在 $180-$195 震荡",
      "target_range": [180, 195],
      "recommended_strategy": "正常做 T"
    },
    {
      "name": "悲观",
      "probability": 0.20,
      "trigger": "大盘回调 + 行业利空",
      "path": "跌破 $175 前低，下探 $165",
      "target_range": [165, 175],
      "recommended_strategy": "减T频率，保留现金"
    }
  ],

  "cost_reduction_estimate": {
    "conservative_monthly_pct": 0.5,
    "normal_monthly_pct": 1.2,
    "ideal_monthly_pct": 2.0,
    "notes": "按当前 ATR 和做 T 频率估算"
  },

  "calendar": [
    {"week": "2026-W17", "focus": "财报披露日 2026-05-01"},
    {"week": "2026-W18", "focus": "关注季报后指引变化"}
  ],

  "discipline": [
    "底仓 70 股不动",
    "单日做 T 不超过 20 股",
    "跌破 $170 止损"
  ],

  "technical_summary": {
    "ma_alignment": "空头排列 (MA5<MA20<MA60)",
    "macd": "死叉后走平",
    "rsi14": 38,
    "composite_score": "3 多 / 5 空"
  },

  "fundamentals_summary": {
    "pe_ttm": 28.5,
    "pb": 42.1,
    "institution_rating": "中性（强买 5 / 买 12 / 持有 8 / 卖 1）",
    "valuation_verdict": "偏高"
  },

  "news_summary": "AI 竞争加剧、Q2 营收指引下修，板块整体回调 10%。",

  "data_sources": {
    "longbridge_snapshot_time": "2026-04-23T10:30:00+08:00",
    "news_urls": ["https://..."]
  }
}
```

### 字段使用规则

- `version`: 固定 `"1"`，以后 schema 变更时递增
- `plan_date`: 只日期，不含时分。同一天多次做计划会覆盖
- `snapshot`: 写入时的行情和持仓，复盘时用作起点
- `position_split`: A 股按手（100 股整数倍），港美股按实际最小单位
- `price_levels`: 列所有关键价位，从高到低排序。`action` 写具体动作（买 X 股 / 卖 X 股 / 止损 / 观察）
- `t_plans`: 每条 T 操作都要有触发价 + 触发条件 + 退出价 + 退出条件，缺一项复盘时就没法 diff
- `scenarios`: 至少 3 种，概率加起来 ≈ 1.0（允许 ±0.05 误差）
- `cost_reduction_estimate`: 三档（保守/正常/理想），用小数百分比（`0.5` 即 0.5%）
- `calendar`: 未来 4-6 周，每周一行
- `discipline`: 红线规则，复盘时检查是否被破
- `technical_summary` / `fundamentals_summary`: 复盘时对比新老数据

## Review JSON schema

```json
{
  "version": "1",
  "symbol": "AAPL.US",
  "review_date": "2026-05-15",
  "created_at": "2026-05-15T11:00:00+08:00",

  "prior_plan_path": "/abs/path/to/AAPL.US/plan_2026-04-23.json",
  "prior_plan_date": "2026-04-23",
  "days_elapsed": 22,

  "current_snapshot": {
    "price": 195.40,
    "shares": 90,
    "cost_basis": 247.50,
    "unrealized_pnl_pct": -21.05
  },

  "scenario_verdict": {
    "actual_scenario_match": "中性",
    "prior_probabilities": {"乐观": 0.30, "中性": 0.50, "悲观": 0.20},
    "accuracy_comment": "走势接近中性路径，在 $180-$200 区间震荡略偏上沿"
  },

  "price_level_checks": [
    {
      "price": 192.5,
      "type": "resistance",
      "hit": true,
      "held": true,
      "first_touch_date": "2026-05-02",
      "comment": "多次触及后回落，阻力有效"
    },
    {
      "price": 175.0,
      "type": "support",
      "hit": false,
      "comment": "期间最低 $179，未下探至该支撑"
    }
  ],

  "t_plan_execution": [
    {
      "direction": "reverse_T",
      "planned_trigger_price": 192.5,
      "planned_shares": 10,
      "executed": true,
      "executed_date": "2026-05-02",
      "executed_price": 193.1,
      "executed_shares": 10,
      "exit_executed": true,
      "exit_price": 185.2,
      "pnl": 79.0,
      "discipline_comment": "按计划执行"
    }
  ],

  "cost_change": {
    "prior_cost_basis": 250.00,
    "current_cost_basis": 247.50,
    "actual_reduction_pct": 1.0,
    "plan_normal_estimate_pct": 1.2,
    "verdict": "略低于预期，但在合理区间"
  },

  "discipline_breaches": [],

  "news_delta": [
    "2026-05-01 Q2 财报，营收小幅超预期",
    "2026-05-10 WWDC 预告 AI 新功能"
  ],

  "updated_plan_path": "/abs/path/to/AAPL.US/plan_2026-05-15.json",

  "lessons": [
    "MA20 阻力在下跌趋势中有效，下次可提前布局",
    "正 T 未触发因价格未到支撑，说明支撑位应上调"
  ]
}
```

### 字段使用规则

- `prior_plan_path`: 加载的计划文件**绝对路径**，方便回溯
- `scenario_verdict.actual_scenario_match`: 必须是 prior plan 里列出过的情景名之一
- `price_level_checks`: 每个价位必须对应到 prior plan 的 `price_levels`
- `t_plan_execution`: 对应到 prior plan 的 `t_plans`，执行情况从 `longbridge order --history` 拉取
- `discipline_breaches`: 违反红线的清单；没违反就写 `[]`
- `updated_plan_path`: 如果复盘同时更新了计划（推荐），在这里指向新 plan.json

## 文件命名

目录由用户提供（session 内记忆一次即可，不跨会话）。目录下：

```
<user_provided_dir>/
├── plan_2026-04-23.json
├── plan_2026-04-23.html
├── plan_2026-05-15.json       # 复盘后更新的计划
├── plan_2026-05-15.html
├── review_2026-05-15.json
└── review_2026-05-15.html
```

同一天多次存 plan 会覆盖（plan_io.py 提示用户确认）。

## 加载最新计划的约定

`plan_io.py load-latest-plan <dir>` 的行为：

- 扫描目录下所有 `plan_YYYY-MM-DD.json`
- 按日期降序返回最新的那份
- 找不到则返回 `null`（skill 要处理这种情况）
