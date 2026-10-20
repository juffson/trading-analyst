# 研究报告 JSON 合同 v1

本目录只保存可公开的市场与公司分析。所有报告文件都会进入公开仓库，校验覆盖完整 JSON，不能把未展示字段当作私密存储。个人账户、持仓、交易记录与内部文档链接禁止进入报告。

## 文件与身份

- 标的登记表：`research/symbols.json`。
- 初版：`research/reports/<symbol>/YYYY-MM-DD.json`，`revision` 必须为 `1`。
- 修订：`research/reports/<symbol>/YYYY-MM-DD.v2.json`，文件后缀数字与 `revision` 一致，从 `2` 开始。
- 业务唯一键为 `symbol + trade_date + revision`。不添加 `report_id`，报告不得自带未知字段。
- 首次生成的历史报告也是初版。补录日期放在 `generated_at`，不能冒充交易日当时已发布的报告。同日修改必须新增修订文件，保留原版。校验器要求修订从 1 连续递增，生成时间递增。
- `trade_date` 是该市场的交易日；`data_as_of` 是行情截止时间；`generated_at` 是实际生成时间。这三者独立，不要求交易日等于生成日期。收盘后资讯可以晚于行情截止时间，但必须注明其发布时间及收盘后属性，不用于倒推当天价格原因，也不能晚于报告生成时间。
- 只有已经收盘的交易日可标记 `closing`。校验器按市场时区和常规收盘时间检查；不内置节假日日历，生产者仍须核实当日是否交易。

## 标的登记表

```json
{"schema_version":1,"symbols":[{"symbol":"002594.SZ","name":"比亚迪","market":"CN","currency":"CNY","slug":"byd"}]}
```

每个对象上述字段都必填。`market` 只接受 `CN`、`HK`、`US`，货币分别必须为 `CNY`、`HKD`、`USD`。代码必须分别以 `.SZ/.SH`、`.HK`、`.US` 结尾。`slug` 为唯一的小写字母、数字与连字符，用于页面地址；不影响原始报告目录。登记表和报告都禁止额外字段。

## 完整最小报告示例

以下例子表达“缺少行情，分析被阻塞”，并非行情样本。所有顶层键均必填；没有证据的数值使用 `null`，没有条目使用空数组。

```json
{
  "schema_version": 1,
  "symbol": "002594.SZ",
  "trade_date": "2026-10-19",
  "revision": 1,
  "data_as_of": null,
  "generated_at": "2026-10-20T08:00:00Z",
  "report_kind": "closing",
  "outlook": "Unknown",
  "analysis_quality": "blocked",
  "headline": "等待可核验的收盘行情",
  "summary": "尚未获得所需数据，本页不形成方向判断。",
  "market_snapshot": {"close": null, "previous_close": null, "change_pct": null, "open": null, "high": null, "low": null},
  "indicators": [],
  "drivers": [],
  "price_levels": null,
  "scenarios": [],
  "sections": [],
  "comparison": null,
  "sources": [],
  "limitations": ["缺少可核验的收盘数据。"],
  "price_history": [],
  "provenance": {"kind": "historical_backfill", "source": "人工补录", "note": "按实际生成时间保存；未回填任何历史预测。"}
}
```

## 字段细则

| 字段 | 类型与规则 |
| --- | --- |
| `schema_version` | 整数 `1`；布尔值不算整数。 |
| `symbol` | 已登记代码，大小写与目录必须完全一致。 |
| `trade_date` | 有效 `YYYY-MM-DD`，必须不晚于生成时间对应的市场日期。 |
| `revision` | 正整数，与文件名一致。 |
| `data_as_of` | 行情截止时间，带时区的 ISO 8601 时间或 `null`；有行情时必填，不晚于生成时间。收盘后资讯使用各来源自己的 `published_at`，不改变行情截止时间。 |
| `generated_at` | 实际生成时间，带时区 ISO 8601，例如 `2026-10-20T08:00:00Z`。 |
| `report_kind` | 当前只允许 `closing`。 |
| `outlook` | `Strong Bullish`、`Bullish`、`Neutral`、`Bearish`、`Strong Bearish`、`Unknown`。 |
| `analysis_quality` | `pass`、`degraded`、`blocked`。缺少数据不能伪装成 `Neutral`。 |
| `headline`、`summary` | 非空纯文本。 |
| `market_snapshot` | 六个键都必填：`close`、`previous_close`、`change_pct`、`open`、`high`、`low`。每项为有限数值或 `null`；价格必须大于零。涨跌幅使用百分数，例如 `1.25` 表示 `1.25%`。 |
| `indicators` | 数组；每项 `{name, value, unit, note}`。`name` 非空文本，`value` 为有限数值、非空文本或 `null`；`unit`、`note` 为文本或 `null`。 |
| `drivers` | 数组；每项 `{title, direction, evidence, source_ids}`。`direction` 为 `positive/negative/mixed/neutral/unknown`；`title/evidence` 非空文本，`source_ids` 引用当前报告中存在的来源 ID。 |
| `price_levels` | `null` 或下表所列完整对象。数值都是分析价位，不是个人成本或实际订单。 |
| `scenarios` | 数组；每项 `{name, price, condition, horizon}`。`price` 为正的有限数值或 `null`；`name/condition` 非空文本，`horizon` 为非空文本或 `null`。 |
| `sections` | 数组；每项 `{heading, body}`，二者为非空纯文本。不解析 HTML，不接受 HTML 标签或脚本协议。 |
| `comparison` | `null` 或 `{previous_trade_date, narrative}`。日期为更早交易日或 `null`，`narrative` 非空文本；描述与上次分析的变化，不要求登记表中必须已有那天的报告。 |
| `sources` | 数组；每项 `{id, label, url, published_at}`。`id` 为唯一字母、数字、连字符或下划线，`label` 非空；`url` 必须为公开 HTTPS 网页，不接受认证参数、内部地址或 Notion/飞书链接；`published_at` 为带时区时间或 `null`。 |
| `limitations` | 非空文本数组；`degraded/blocked` 至少一项，清楚说明不足。 |
| `price_history` | 数组；每项 `{date, close}`，正数收盘价、日期唯一严格递增且不晚于 `trade_date`。若含当天，与 `market_snapshot.close` 一致。只含真实历史价格，不回填历史预测。 |
| `provenance` | `{kind, source, note}`，均必填。`kind` 为 `fresh_analysis/historical_backfill/validation`；`source/note` 非空文本。`validation` 只能用 `blocked + Unknown`，用于验证写入，不能作为有效研究信号。 |

### `price_levels` 对象

以下所有键都必填，缺失的数值与描述可为 `null`：

```json
{
  "entry_low": null,
  "entry_high": null,
  "target": null,
  "stop": null,
  "take_profit_1": null,
  "take_profit_2": null,
  "target_type": null,
  "target_horizon": null,
  "entry_valid_until": null,
  "status": "unavailable",
  "trigger": null,
  "invalidation": null,
  "basis": null
}
```

所有价位为正的有限数值或 `null`；进场区间两端必须同时存在或同时为 `null`，且下界不大于上界。`target_type` 只接受 `technical/fundamental/model` 或 `null`；目标价存在时，类型、期限与依据必须有值。`status` 为 `active/conditional/expired/unavailable`。`entry_valid_until` 为日期或 `null`，`trigger/invalidation/basis/target_horizon` 为非空文本或 `null`。`unavailable` 不允许夹带价位。`active/conditional` 的区间必须有触发、失效条件和截止日期；截止日期不早于报告交易日。对于不同方向的分析，不强制止损与目标位的上下关系。

### 质量规则

`pass` 要求收盘价、行情截止时间和至少一个公开来源，并且不能使用 `Unknown`。`degraded` 必须写明限制。缺少收盘价一律使用 `blocked + Unknown`。`blocked` 不发布价位对象或情景预测。`Unknown` 说明证据不足；`Neutral` 是基于数据的中性判断，二者不能互换。

## Python 接口与命令

```python
from pathlib import Path
from scripts.validate_research import load_dataset, validate_report, ValidationError

symbols, reports = load_dataset(Path("."))
# symbols/reports 均为原始 dict 的 list，不追加内部字段。
# reports 按 (symbol, trade_date, revision) 排序；调用前完成完整数据集校验。
```

`validate_report(report, symbols_by_code, source_path=None)` 校验单个报告；`source_path` 可为相对仓库路径或绝对路径，其 `reports/<symbol>/日期文件.json` 尾部必须匹配身份。完整数据集还验证业务键、修订连续性及生成时间顺序。所有错误抛出 `ValidationError`，可用于在构建前中止发布。

```bash
python scripts/validate_research.py --root .
python -m unittest discover -s tests -p 'test_research.py'
```

在拥有完整 Git 历史的 CI 中追加 `--base-ref <基准提交完整SHA>`，会拒绝对已有 `research/reports/` 文件的覆盖、删除、移动或类型变更，只接受新增报告和新增修订。Python 接口为 `validate_append_only(repo_root, base_ref)`。首次发布没有基准时可仅校验快照；后续 push/PR 应传入事件的基准提交。基准不存在会失败，不会静默跳过。

`schema.json` 提供编辑器可用的 JSON Schema，根结构同时接受标的登记表和报告；Python 校验器另行执行日期、标的货币、价格关系、来源引用、隐私和文件路径等跨字段规则。核心校验只使用 Python 标准库；可选的基准比较调用 checkout 环境内的 Git。校验器不执行网络请求，也不保证来源内容真实，因此分析生产者仍须查验公开证据。
