"""Deterministic research JSON -> native Feishu Docx blocks and PNG charts.

Callers must validate research data before rendering.  This module never fetches
market data, converts Markdown, publishes documents, or reads credentials.  All
blocks are flat root children; the publisher owns batching and image insertion.

Native shapes follow the official Docx structure and guide:
https://open.feishu.cn/document/docs/docs/data-structure/block
https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/guide
Image creation follows the official three-step FAQ (empty block, upload, replace):
https://open.feishu.cn/document/docs/docs/faq?lang=zh-CN

Link URLs are percent-encoded once as complete URLs, as in the official guide.
The renderer itself uses only the standard library. PNG helpers lazily import
matplotlib and select its noninteractive Agg backend. Compatible with Python 3.10.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
import math
from pathlib import Path
import re
from typing import Iterable
from urllib.parse import quote, urlsplit


MAX_TEXT_RUN = 1500
MANAGED_NOTICE_PREFIX = (
    "本页由 trading-analyst 自动发布；仅同步已校验的研究档案。"
    "历史记录保留交易日、行情截止时间和修订版本。自动管理键："
)
COMPLETED_NOTICE_PREFIX = "本份分析内容结束，完整记录："
_MANAGED_KEY = re.compile(
    r"(?:overview|symbol:[A-Z0-9][A-Z0-9.-]*|"
    r"report:[A-Z0-9][A-Z0-9.-]*:\d{4}-\d{2}-\d{2}:[1-9]\d*)\Z"
)
_BLOCK_NAMES = {2: "text", 3: "heading1", 4: "heading2", 5: "heading3", 12: "bullet"}
_MARKETS = {"CN": "A股", "HK": "港股", "US": "美股"}
_OUTLOOK = {
    "Strong Bullish": "强看多", "Bullish": "偏多", "Neutral": "中性",
    "Bearish": "偏空", "Strong Bearish": "强看空", "Unknown": "未形成判断",
}
_QUALITY = {"pass": "通过", "degraded": "数据受限", "blocked": "分析阻塞"}
_PROVENANCE = {"fresh_analysis": "当期分析", "historical_backfill": "历史补录", "validation": "流程验证"}
_DIRECTION = {"positive": "利多", "negative": "利空", "mixed": "多空交织", "neutral": "中性", "unknown": "尚待核验"}
_LEVEL_STATUS = {"active": "报告当日有效", "conditional": "附条件", "expired": "原报告已标记失效", "unavailable": "未形成价位"}
_TARGET_TYPE = {"technical": "技术情景", "fundamental": "基本面估值", "model": "模型结果"}
_COLORS = ("#65d6c1", "#edbc70", "#8bacf1", "#f091a1", "#b4a2eb")


def _encoded_url(url: str) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or any(ord(char) < 32 for char in url)):
        raise ValueError("Document and source links must be absolute HTTPS URLs without credentials")
    return quote(url, safe="")


def _rich_blocks(parts: Iterable[tuple[str, str | None, bool]], block_type: int = 2) -> list[dict]:
    """Preserve all Unicode content and link styles without oversized runs.

    Each block also stays at <=1500 Python Unicode code points, safely below the
    Docx block limit even when astral characters take two UTF-16 code units.
    """
    field = _BLOCK_NAMES[block_type]
    blocks: list[dict] = []
    elements: list[dict] = []
    length = 0
    for content, url, bold in parts:
        style: dict = {}
        if url is not None:
            style["link"] = {"url": _encoded_url(url)}
        if bold:
            style["bold"] = True
        offset = 0
        while offset < len(content):
            available = MAX_TEXT_RUN - length
            chunk = content[offset:offset + available]
            run = {"content": chunk}
            if style:
                # Each element owns its dict, so later publisher edits are local.
                run["text_element_style"] = {
                    key: dict(value) if isinstance(value, dict) else value
                    for key, value in style.items()
                }
            elements.append({"text_run": run})
            length += len(chunk)
            offset += len(chunk)
            if length == MAX_TEXT_RUN:
                blocks.append({"block_type": block_type, field: {"elements": elements}})
                elements, length = [], 0
    if elements:
        blocks.append({"block_type": block_type, field: {"elements": elements}})
    return blocks


def _add(blocks: list[dict], content: str, block_type: int = 2) -> None:
    blocks.extend(_rich_blocks([(content, None, False)], block_type))


def _field(blocks: list[dict], label: str, value: str, block_type: int = 12) -> None:
    blocks.extend(_rich_blocks([(label + "：", None, True), (value, None, False)], block_type))


def _link(blocks: list[dict], label: str, url: str | None, suffix: str = "", block_type: int = 2) -> None:
    if not url:
        _add(blocks, label + "（链接尚未生成）" + suffix, block_type)
    else:
        blocks.extend(_rich_blocks([(label, url, False), (suffix, None, False)], block_type))


def _navigation(blocks: list[dict], links: list[tuple[str, str]], page_url: str | None) -> None:
    if page_url:
        links = links + [("网页归档", page_url)]
    parts: list[tuple[str, str | None, bool]] = []
    for label, url in links:
        if parts:
            parts.append(("  ·  ", None, False))
        parts.append((label, url, False))
    blocks.extend(_rich_blocks(parts))


def _value(value: object, reason: str = "原报告未记录") -> str:
    return "—（" + reason + "）" if value is None else str(value)


def _price(value: object, currency: str) -> str:
    if value is None:
        return "—（未记录价位）"
    text = format(Decimal(str(value)), "f")
    if "." not in text:
        text += ".00"
    elif len(text.split(".")[1]) == 1:
        text += "0"
    return text + " " + currency


def _percent(value: object) -> str:
    return "—（未记录涨跌幅）" if value is None else str(value) + "%"


def _sort_key(report: dict) -> tuple[str, int]:
    return report["trade_date"], report["revision"]


def _for_symbol(symbol: dict, reports: list[dict]) -> list[dict]:
    return sorted((report for report in reports if report["symbol"] == symbol["symbol"]), key=_sort_key)


def _latest(symbol: dict, reports: list[dict], *, research_only: bool = False) -> dict | None:
    matching = _for_symbol(symbol, reports)
    actual = [report for report in matching if report["provenance"]["kind"] != "validation"]
    candidates = actual if actual or research_only else matching
    return candidates[-1] if candidates else None


def _daily_latest(reports: list[dict]) -> list[dict]:
    by_date: dict[str, dict] = {}
    for report in sorted(reports, key=_sort_key):
        by_date[report["trade_date"]] = report
    return list(by_date.values())


def managed_notice(key: str) -> dict:
    """First root block of every managed document; stable and non-secret."""
    if not _MANAGED_KEY.fullmatch(key) or len(key) > 200:
        raise ValueError("Invalid managed research document key")
    return _rich_blocks([(MANAGED_NOTICE_PREFIX + key, None, False)])[0]


def completed_notice(key: str) -> dict:
    """Mark complete report text; do not assert image or publication success."""
    if not _MANAGED_KEY.fullmatch(key) or len(key) > 200:
        raise ValueError("Invalid completed research document key")
    return _rich_blocks([(COMPLETED_NOTICE_PREFIX + key, None, False)])[0]


def image_placeholder() -> dict:
    """Official Docx FAQ: token is populated only after upload/replace_image."""
    return {"block_type": 27, "image": {}}


def _report_metadata(blocks: list[dict], symbol: dict, report: dict, *, immutable: bool = False) -> None:
    fields = (
        ("标的", symbol["symbol"] if immutable else symbol["name"] + " · " + symbol["symbol"]),
        ("市场 / 货币", _MARKETS[symbol["market"]] + " / " + symbol["currency"]),
        ("交易日期", report["trade_date"]),
        ("行情截止时间", _value(report["data_as_of"], "原报告未记录行情截止时间")),
        ("实际生成时间", report["generated_at"]),
        ("报告类型 / 修订", report["report_kind"] + "（收盘分析） / v" + str(report["revision"])),
        ("数据合同版本", str(report["schema_version"])),
        ("当时方向", report["outlook"] + "（" + _OUTLOOK[report["outlook"]] + "）"),
        ("分析质量", report["analysis_quality"] + "（" + _QUALITY[report["analysis_quality"]] + "）"),
        ("归档属性", report["provenance"]["kind"] + "（" + _PROVENANCE[report["provenance"]["kind"]] + "）"),
    )
    for label, value in fields:
        _field(blocks, label, value)


def _snapshot(blocks: list[dict], symbol: dict, report: dict) -> None:
    _add(blocks, "收盘行情快照", 4)
    for key, label in (("close", "收盘价"), ("previous_close", "前收盘"), ("change_pct", "日涨跌幅"), ("open", "开盘价"), ("high", "最高价"), ("low", "最低价")):
        value = report["market_snapshot"][key]
        _field(blocks, label, _percent(value) if key == "change_pct" else _price(value, symbol["currency"]))


def _price_levels(blocks: list[dict], symbol: dict, report: dict) -> None:
    _add(blocks, "关键价位与成立条件", 4)
    _add(blocks, "以下为原报告的研究情景；页面同步不会延长原有效期，也不表示当前条件已经成立。")
    levels = report["price_levels"]
    if levels is None:
        _add(blocks, "—（本报告未形成可发布的研究价位、条件区间或失效线。）")
        return
    currency = symbol["currency"]
    low, high = levels["entry_low"], levels["entry_high"]
    interval = "—（未记录条件区间）" if low is None else _price(low, currency) + " — " + _price(high, currency)
    fields = (
        ("当时状态", levels["status"] + "（" + _LEVEL_STATUS[levels["status"]] + "）"),
        ("原条件区间", interval),
        ("当时目标", _price(levels["target"], currency)),
        ("当时失效线", _price(levels["stop"], currency)),
        ("第一观察位", _price(levels["take_profit_1"], currency)),
        ("第二观察位", _price(levels["take_profit_2"], currency)),
        ("目标类型", "—（未记录类型）" if levels["target_type"] is None else levels["target_type"] + "（" + _TARGET_TYPE[levels["target_type"]] + "）"),
        ("目标期限", _value(levels["target_horizon"], "未记录目标期限")),
        ("原有效期截止", _value(levels["entry_valid_until"], "未记录截止日期")),
        ("触发条件", _value(levels["trigger"], "未记录触发条件")),
        ("失效条件", _value(levels["invalidation"], "未记录失效条件")),
        ("价位依据", _value(levels["basis"], "未记录价位依据")),
    )
    for label, value in fields:
        _field(blocks, label, value)


def _limitations(blocks: list[dict], report: dict) -> None:
    _add(blocks, "数据缺口与研究边界", 4)
    if report["limitations"]:
        for limitation in report["limitations"]:
            _add(blocks, limitation, 12)
    else:
        _add(blocks, "本报告未另行列出数据缺口；这不表示没有研究不确定性。")


def render_report(symbol: dict, report: dict, *, symbol_url: str, overview_url: str,
                  page_url: str | None = None) -> list[dict]:
    """Render one immutable research revision, including every evidence section.

    Catalog display names and optional Pages URLs may change independently of an
    archived report. Use its symbol code as identity and omit ``page_url`` here;
    the argument remains accepted for compatibility with other render calls.
    """
    if symbol["symbol"] != report["symbol"]:
        raise ValueError("Report symbol does not match the document symbol")
    key = "report:" + report["symbol"] + ":" + report["trade_date"] + ":" + str(report["revision"])
    blocks = [managed_notice(key)]
    _add(blocks, symbol["symbol"] + " · " + report["trade_date"] + " 收盘研究 · v" + str(report["revision"]), 3)
    _navigation(blocks, [("研究总览", overview_url), ("返回标的历史", symbol_url)], None)
    _add(blocks, "历史研究记录：所有价格、方向和条件均对应原报告的日期与数据。历史补录不等于当时已发布的预测，不能视为实时信号。")
    if report["provenance"]["kind"] == "validation":
        _add(blocks, "流程验证记录：本页用于验证归档链路，不形成市场判断，不计入方向统计或走势对比。")
    _report_metadata(blocks, symbol, report, immutable=True)
    _add(blocks, "结论快照", 4)
    _add(blocks, report["headline"], 5)
    _add(blocks, report["summary"])
    _snapshot(blocks, symbol, report)

    _add(blocks, "指标快照", 4)
    if not report["indicators"]:
        _add(blocks, "—（本报告没有可用指标；不以零代替缺值。）")
    for indicator in report["indicators"]:
        _field(blocks, indicator["name"], _value(indicator["value"], "指标值未记录") + "；单位：" + _value(indicator["unit"], "未单列单位") + "；口径 / 限制：" + _value(indicator["note"], "未单列说明"))

    _add(blocks, "核心驱动与多空证据", 4)
    sources = {source["id"]: source for source in report["sources"]}
    if not report["drivers"]:
        _add(blocks, "—（本报告没有记录驱动条目。）")
    for driver in report["drivers"]:
        _add(blocks, driver["title"], 5)
        _field(blocks, "证据方向", driver["direction"] + "（" + _DIRECTION[driver["direction"]] + "）")
        _add(blocks, driver["evidence"])
        for source_id in driver["source_ids"]:
            source = sources[source_id]
            _link(blocks, "证据来源 [" + source_id + "]：" + source["label"], source["url"])

    _price_levels(blocks, symbol, report)
    _add(blocks, "情景路径", 4)
    if not report["scenarios"]:
        _add(blocks, "—（本报告未形成可发布的价格情景。）")
    for scenario in report["scenarios"]:
        _add(blocks, scenario["name"], 5)
        _field(blocks, "情景价格", _price(scenario["price"], symbol["currency"]))
        _field(blocks, "成立条件", scenario["condition"])
        _field(blocks, "情景期限", _value(scenario["horizon"], "未记录期限"))

    _add(blocks, "分析正文", 4)
    if not report["sections"]:
        _add(blocks, "—（原记录没有完整分析正文。）")
    for section in report["sections"]:
        _add(blocks, section["heading"], 5)
        _add(blocks, section["body"])

    _add(blocks, "与前次分析的变化", 4)
    if report["comparison"] is None:
        _add(blocks, "—（没有记录前次比较；不由当前判断反推旧观点。）")
    else:
        _field(blocks, "前次交易日期", _value(report["comparison"]["previous_trade_date"], "没有可对应的前次交易日"))
        _add(blocks, report["comparison"]["narrative"])

    _add(blocks, "公开来源", 4)
    if not report["sources"]:
        _add(blocks, "—（本报告没有记录可跳转的公开来源。）")
    for source in report["sources"]:
        _link(blocks, "[" + source["id"] + "] " + source["label"], source["url"], block_type=12)
        _field(blocks, "来源发布时间", _value(source["published_at"], "原报告未记录发布时间"), block_type=2)
    _limitations(blocks, report)

    _add(blocks, "归档来源与属性", 4)
    _field(blocks, "属性", report["provenance"]["kind"] + "（" + _PROVENANCE[report["provenance"]["kind"]] + "）")
    _field(blocks, "原研究来源", report["provenance"]["source"])
    _field(blocks, "归档说明", report["provenance"]["note"])

    _add(blocks, "原报告收盘数据序列", 4)
    _add(blocks, "以下只包含该版本实际记录的历史收盘价，单位 " + symbol["currency"] + "。不拼接其他版本，不把目标或失效价当作历史行情。")
    if not report["price_history"]:
        _add(blocks, "—（本报告没有记录可核验的收盘价序列。）")
    for point in report["price_history"]:
        _field(blocks, point["date"], _price(point["close"], symbol["currency"]))
    blocks.append(completed_notice(key))
    return blocks


def _series(symbol: dict, reports: list[dict]) -> tuple[dict | None, list[dict]]:
    """Use only the latest actual report; never silently fall back or splice."""
    report = _latest(symbol, reports, research_only=True)
    if not report:
        return None, []
    history = report["price_history"]
    previous = None
    for point in history:
        day = date.fromisoformat(point["date"])
        value = point["close"]
        if (previous is not None and day <= previous) or point["date"] > report["trade_date"]:
            raise ValueError("History must have unique ascending dates no later than the report date")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("History must contain finite positive observed close prices")
        previous = day
    return report, history[-120:]


def _trend_note(blocks: list[dict], symbol: dict, reports: list[dict]) -> None:
    report, history = _series(symbol, reports)
    if len(history) < 2:
        _add(blocks, "走势图片未生成：最新正式报告不足两个实际收盘记录；不回退到更早报告，也不补造走势。")
        return
    _add(blocks, "真实收盘区间：" + history[0]["date"] + " — " + history[-1]["date"] + "；" + str(len(history)) + " 条记录；单位 " + symbol["currency"] + "。")
    _add(blocks, "序列来源：" + report["trade_date"] + " / v" + str(report["revision"]) + "；行情截止：" + _value(report["data_as_of"]) + "。仅使用这一版本的最近120条以内记录，沿用原系列口径，不拼接复权方式不同的其他记录。")


def render_symbol(symbol: dict, reports: list[dict], *,
                  report_urls: dict[tuple[str, int], str], overview_url: str,
                  page_url: str | None = None) -> list[dict]:
    matching = _for_symbol(symbol, reports)
    latest = _latest(symbol, matching)
    blocks = [managed_notice("symbol:" + symbol["symbol"])]
    _add(blocks, symbol["name"] + " · " + symbol["symbol"] + " 研究档案", 3)
    _navigation(blocks, [("返回研究总览", overview_url)], page_url)
    _field(blocks, "市场 / 货币", _MARKETS[symbol["market"]] + " / " + symbol["currency"])
    _field(blocks, "研究记录", str(len({r["trade_date"] for r in matching})) + " 个交易日期，" + str(len(matching)) + " 个独立版本")
    _add(blocks, "历史档案不代表实时信号。行情历史与研究价位历史分开；每次判断保留原交易日和版本。")
    if latest is None:
        _add(blocks, "等待首份研究", 4)
        _add(blocks, "已纳入观察目录，尚无分析归档。当前不生成方向、指标、目标、失效线或模拟走势。")
    else:
        _add(blocks, "最新归档结论", 4)
        _report_metadata(blocks, symbol, latest)
        _add(blocks, latest["headline"], 5)
        _add(blocks, latest["summary"])
        _link(blocks, "阅读 " + latest["trade_date"] + " / v" + str(latest["revision"]) + " 完整报告", report_urls.get(_sort_key(latest)))
        _snapshot(blocks, symbol, latest)
        _price_levels(blocks, symbol, latest)
        _limitations(blocks, latest)

    _add(blocks, "真实行情历史", 4)
    _trend_note(blocks, symbol, matching)
    _add(blocks, "历史研究价位", 4)
    _add(blocks, "价位只对应有研究记录的日期；不会向未记录日期延伸。同日摘要采用最新正式研究修订，原版本仍可单独阅读。")
    actual = [report for report in matching if report["provenance"]["kind"] != "validation"]
    levels_recorded = [report for report in _daily_latest(actual) if report["price_levels"] is not None]
    if not levels_recorded:
        _add(blocks, "—（尚无历史研究价位记录。）")
    for report in reversed(levels_recorded):
        levels = report["price_levels"]
        line = ("；当时目标 " + _price(levels["target"], symbol["currency"])
                + "；当时失效线 " + _price(levels["stop"], symbol["currency"])
                + "；原截止 " + _value(levels["entry_valid_until"], "未记录日期"))
        _link(blocks, report["trade_date"] + " / v" + str(report["revision"]), report_urls.get(_sort_key(report)), line, 12)

    _add(blocks, "每日分析与全部修订", 4)
    if not matching:
        _add(blocks, "—（第一份分析尚未归档，暂无日报链接。）")
    latest_by_date = {report["trade_date"]: report["revision"] for report in _daily_latest(matching)}
    for report in reversed(matching):
        key = _sort_key(report)
        label = report["trade_date"] + " / v" + str(report["revision"]) + " · " + report["headline"]
        suffix = "；" + _PROVENANCE[report["provenance"]["kind"]] + "；" + _OUTLOOK[report["outlook"]] + "；质量：" + _QUALITY[report["analysis_quality"]]
        if report["revision"] == latest_by_date[report["trade_date"]]:
            suffix += "；当日最新修订"
        _link(blocks, label, report_urls.get(key), suffix, 12)
        _field(blocks, "版本生成时间", report["generated_at"], 2)
    return blocks


def _comparison_groups(symbols: list[dict], reports: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for symbol in symbols:
        grouped[(symbol["market"], symbol["currency"])].append(symbol)
    market_order = {"CN": 0, "HK": 1, "US": 2}
    result = []
    for (market, currency), catalog in sorted(grouped.items(), key=lambda item: (market_order.get(item[0][0], 9), item[0][1])):
        series = []
        missing = []
        for symbol in catalog:
            report, history = _series(symbol, reports)
            if len(history) >= 2:
                series.append((symbol, report, {point["date"]: point["close"] for point in history}))
            else:
                missing.append(symbol["symbol"])
        common_dates = set(series[0][2]) if series else set()
        for _, _, history in series[1:]:
            common_dates.intersection_update(history)
        dates = sorted(common_dates)[-120:]
        reason = ""
        if len(series) < 2:
            reason = "至少需要两只标的各自提供两个真实收盘记录"
        elif len(dates) < 2:
            reason = "没有足够的共同真实交易日期"
        result.append({"market": market, "currency": currency, "series": series,
                       "dates": dates, "missing": missing, "reason": reason})
    return result


def render_overview(symbols: list[dict], reports: list[dict], *,
                    symbol_urls: dict[str, str],
                    report_urls: dict[tuple[str, str, int], str],
                    page_url: str | None = None) -> list[dict]:
    blocks = [managed_notice("overview")]
    _add(blocks, "研究总览", 3)
    if page_url:
        _link(blocks, "打开网页归档总览", page_url)
    _add(blocks, "研究路径：总览 → 标的档案 → 交易日日报及修订版本。所有价格和判断均为历史归档，不能视为实时信号。")
    actual = [report for report in reports if report["provenance"]["kind"] != "validation"]
    covered = sum(_latest(symbol, actual, research_only=True) is not None for symbol in symbols)
    _field(blocks, "观察目录", str(len(symbols)) + " 只标的；已有正式研究 " + str(covered) + " 只")
    _field(blocks, "研究日报", str(len({(r["symbol"], r["trade_date"]) for r in actual})) + " 个标的交易日；" + str(len(actual)) + " 个正式研究版本")
    _field(blocks, "最新归档交易日", max((report["trade_date"] for report in actual), default="—（尚无正式研究）"))
    counts = {"偏多": 0, "中性": 0, "偏空": 0, "未形成判断": 0}
    for symbol in symbols:
        latest = _latest(symbol, actual, research_only=True)
        if latest is None or latest["outlook"] == "Unknown":
            counts["未形成判断"] += 1
        elif latest["outlook"] in ("Bullish", "Strong Bullish"):
            counts["偏多"] += 1
        elif latest["outlook"] in ("Bearish", "Strong Bearish"):
            counts["偏空"] += 1
        else:
            counts["中性"] += 1
    _field(blocks, "最新方向分布", "；".join(label + " " + str(count) for label, count in counts.items()))
    _add(blocks, "方向统计只使用每只标的最新正式研究。Unknown 和无报告归为未形成判断，不用 Neutral 填补缺值；流程验证记录不形成研究信号。")

    _add(blocks, "同市场、同币种走势", 4)
    _add(blocks, "每个市场 / 币种独立分图。仅取各标的最新正式报告的一条价格序列，在共同真实交易日上对齐；共同首日归零后比较收盘涨跌幅。不同币种价格不混绘，目标和失效价不画成历史曲线。")
    for group in _comparison_groups(symbols, reports):
        label = _MARKETS[group["market"]] + " / " + group["currency"]
        if group["reason"]:
            _field(blocks, label, "不绘制对比图：" + group["reason"] + "。")
        else:
            _field(blocks, label, group["dates"][0] + " — " + group["dates"][-1] + "；" + str(len(group["dates"])) + " 个共同交易日；" + str(len(group["series"])) + " 只标的。")
        if group["missing"]:
            _field(blocks, label + " 缺少足够行情", "、".join(group["missing"]))

    for market in ("CN", "HK", "US"):
        catalog = [symbol for symbol in symbols if symbol["market"] == market]
        if not catalog:
            continue
        _add(blocks, _MARKETS[market] + "观察标的", 4)
        for symbol in catalog:
            _add(blocks, symbol["name"] + " · " + symbol["symbol"] + " · " + symbol["currency"], 5)
            _link(blocks, "进入 " + symbol["name"] + " 标的档案", symbol_urls.get(symbol["symbol"]))
            matching = _for_symbol(symbol, reports)
            latest = _latest(symbol, matching)
            if latest is None:
                _add(blocks, "等待首份研究：已纳入观察目录，暂无分析、方向、价位或走势；真实报告写入后会进入日期档案。")
                continue
            _field(blocks, "最新版本", latest["trade_date"] + " / v" + str(latest["revision"]) + "；" + _PROVENANCE[latest["provenance"]["kind"]])
            _field(blocks, "当时方向 / 质量", _OUTLOOK[latest["outlook"]] + " / " + _QUALITY[latest["analysis_quality"]])
            _field(blocks, "行情截止 / 实际生成", _value(latest["data_as_of"]) + " / " + latest["generated_at"])
            _add(blocks, latest["headline"])
            _add(blocks, latest["summary"])
            _field(blocks, "收盘 / 日涨跌", _price(latest["market_snapshot"]["close"], symbol["currency"]) + " / " + _percent(latest["market_snapshot"]["change_pct"]))
            levels = latest["price_levels"]
            _field(blocks, "当时目标 / 失效线", _price(levels["target"] if levels else None, symbol["currency"]) + " / " + _price(levels["stop"] if levels else None, symbol["currency"]))
            key = (symbol["symbol"], latest["trade_date"], latest["revision"])
            _link(blocks, "阅读 " + latest["trade_date"] + " / v" + str(latest["revision"]) + " 日报", report_urls.get(key))
            if latest["limitations"]:
                _add(blocks, "该版本的数据限制：")
                for limitation in latest["limitations"]:
                    _add(blocks, limitation, 12)
    return blocks


def _plot_modules():
    import matplotlib
    matplotlib.use("Agg", force=True)
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    return plt, mdates


def _style_axes(axis, mdates, currency: str, *, percent: bool = False) -> None:
    axis.set_facecolor("#111d2d")
    for spine in axis.spines.values():
        spine.set_color("#33455b")
    axis.tick_params(colors="#9baec4", labelsize=9)
    axis.grid(axis="y", color="#33455b", alpha=.7, linewidth=.6, linestyle="--")
    axis.set_axisbelow(True)
    locator = mdates.AutoDateLocator(minticks=3, maxticks=7)
    axis.xaxis.set_major_locator(locator)
    axis.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    axis.xaxis.get_offset_text().set_color("#9baec4")
    axis.set_ylabel("Change (%)" if percent else "Close (" + currency + ")", color="#c1cfde", fontsize=10)
    axis.margins(x=.025, y=.12)


def trend_png(symbol: dict, reports: list[dict], destination: Path) -> bool:
    """Draw <=120 actual closes from the latest actual report; False if <2.

    No fallback to older reports, no blending of adjustment bases, and no target
    or stop lines. The caller must respect False and not reuse a stale image.
    """
    report, history = _series(symbol, reports)
    if len(history) < 2:
        return False
    plt, mdates = _plot_modules()
    figure, axis = plt.subplots(figsize=(11.2, 4.8), dpi=160, facecolor="#09121f")
    try:
        dates = [datetime.strptime(point["date"], "%Y-%m-%d") for point in history]
        closes = [point["close"] for point in history]
        _style_axes(axis, mdates, symbol["currency"])
        axis.plot(dates, closes, color=_COLORS[0], linewidth=2.2, label="Observed close")
        axis.scatter([dates[-1]], [closes[-1]], s=28, color=_COLORS[0], zorder=3)
        axis.annotate(_price(closes[-1], symbol["currency"]), (dates[-1], closes[-1]), xytext=(-8, 12), textcoords="offset points", ha="right", color="#e8edf4", fontsize=9)
        figure.subplots_adjust(left=.09, right=.97, top=.73, bottom=.21)
        figure.text(.07, .935, symbol["symbol"] + " | Historical close (" + symbol["currency"] + ")", color="#e8edf4", fontsize=17, weight="semibold")
        figure.text(.07, .875, "Source report: " + report["trade_date"] + " / v" + str(report["revision"]) + " | Data as of: " + _value(report["data_as_of"]), color="#9baec4", fontsize=9)
        figure.text(.07, .825, history[0]["date"] + " to " + history[-1]["date"] + " | " + str(len(history)) + " observed trading dates", color="#9baec4", fontsize=9)
        figure.text(.07, .075, "One report series; original price basis retained. No series splicing, targets or reconstructed forecasts.", color="#9baec4", fontsize=9)
        figure.text(.07, .035, "Historical archive, not a live market signal. Source generation: " + report["generated_at"], color="#9baec4", fontsize=8)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(destination, format="png", facecolor=figure.get_facecolor(), metadata={"Title": symbol["symbol"] + " historical close", "Description": "Currency: " + symbol["currency"] + "; report: " + report["trade_date"] + "; revision: " + str(report["revision"]) + "; data_as_of: " + str(report["data_as_of"])})
    finally:
        plt.close(figure)
    return True


def overview_png(symbols: list[dict], reports: list[dict], destination: Path) -> bool:
    """Compare common observed dates in separate market/currency panels."""
    groups = _comparison_groups(symbols, reports)
    usable = [group for group in groups if not group["reason"]]
    if not usable:
        return False
    plt, mdates = _plot_modules()
    figure, axes = plt.subplots(len(usable), 1, figsize=(11.2, 3.65 * len(usable) + 1.05), dpi=160, squeeze=False, facecolor="#09121f")
    skipped = [group["market"] + "/" + group["currency"] for group in groups if group["reason"]]
    missing = sorted({symbol for group in groups for symbol in group["missing"]})
    try:
        for row, group in enumerate(usable):
            axis = axes[row][0]
            _style_axes(axis, mdates, group["currency"], percent=True)
            days = group["dates"]
            dates = [datetime.strptime(day, "%Y-%m-%d") for day in days]
            cutoff_labels = []
            for index, (symbol, report, history) in enumerate(group["series"]):
                initial = history[days[0]]
                changes = [(history[day] / initial - 1) * 100 for day in days]
                axis.plot(dates, changes, label=symbol["symbol"], color=_COLORS[index % len(_COLORS)], linewidth=1.9)
                cutoff_labels.append(symbol["symbol"] + ": " + report["trade_date"] + " v" + str(report["revision"]))
            axis.set_title(group["market"] + " / " + group["currency"] + " | " + days[0] + " - " + days[-1] + " | " + str(len(days)) + " common trading dates", loc="left", color="#e8edf4", fontsize=11, pad=12)
            axis.legend(loc="best", frameon=True, facecolor="#111d2d", edgecolor="#33455b", labelcolor="#c1cfde", fontsize=9)
            axis.text(0, -.23, "Source reports: " + "; ".join(cutoff_labels), transform=axis.transAxes, color="#9baec4", fontsize=8)
        # Reserve space below the final panel for its source line and the
        # archive/omission captions; these must never overlap at 1–3 panels.
        bottom = .17 if len(usable) >= 3 else .22 if len(usable) == 2 else .28
        top = .92 if len(usable) >= 3 else .89 if len(usable) == 2 else .86
        figure.subplots_adjust(left=.09, right=.97, top=top, bottom=bottom, hspace=.65)
        figure.text(.07, .965, "Historical close comparison | Same market and currency", color="#e8edf4", fontsize=15, weight="semibold")
        figure.text(.07, .061, "Common first observation = 0%. One source report per symbol; no mixed-currency price lines or forecasts.", color="#9baec4", fontsize=8)
        omitted = "Not shown (insufficient series/common dates): " + (", ".join(skipped) if skipped else "none")
        if missing:
            omitted += ". Symbols with insufficient history: " + ", ".join(missing)
        figure.text(.07, .03, omitted, color="#edbc70", fontsize=8, wrap=True)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(destination, format="png", facecolor=figure.get_facecolor(), metadata={"Title": "Historical same-currency close comparison", "Description": "Common observed dates; each market/currency in a separate panel; latest report series only."})
    finally:
        plt.close(figure)
    return True
