#!/usr/bin/env python3
"""Build the public, three-level research archive with Python's standard library.

Only validated research JSON and the two explicitly named site assets are copied.
No repository-wide copy, runtime API, template execution, or Markdown/HTML parsing
is used. Every report string is rendered as escaped plain text.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from itertools import count
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from urllib.parse import unquote, urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
from scripts.validate_research import ValidationError, load_dataset  # noqa: E402


MARKETS = {"CN": "A股", "HK": "港股", "US": "美股"}
OUTLOOKS = {
    "Strong Bullish": ("强看多", "up"),
    "Bullish": ("偏多", "up"),
    "Neutral": ("中性", "neutral"),
    "Bearish": ("偏空", "down"),
    "Strong Bearish": ("强看空", "down"),
    "Unknown": ("未形成判断", "unknown"),
}
QUALITIES = {"pass": "分析质量：通过", "degraded": "分析质量：受限", "blocked": "分析阻塞"}
PROVENANCE = {"fresh_analysis": "当期分析", "historical_backfill": "历史补录", "validation": "流程验证"}
DIRECTIONS = {
    "positive": ("利多", "up"), "negative": ("利空", "down"),
    "mixed": ("多空交织", "warning"), "neutral": ("中性", "neutral"),
    "unknown": ("尚待核验", "unknown"),
}
COLORS = ("#65d6c1", "#edbc70", "#8bacf1", "#f091a1", "#b4a2eb")
ASSETS = ("styles.css", "app.js")
CHART_IDS = count(1)


def e(value: object) -> str:
    return escape(str(value), quote=True)


def compact(value: str, limit: int = 100) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit].rstrip("，、；。 ") + "…"


def number(value: object, decimals: int = 5) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return f"{value:,.{decimals}f}".rstrip("0").rstrip(".")


def price(value: float | None) -> str:
    if value is None:
        return "—"
    text = f"{value:,.6f}".rstrip("0")
    if text.endswith("."):
        return text + "00"
    decimals = len(text.split(".")[1])
    return text + ("0" if decimals == 1 else "")


def percentage(value: float | None, signed: bool = True) -> str:
    if value is None:
        return "—"
    return f"{value:+.2f}%" if signed else f"{value:.2f}%"


def time_label(value: str | None) -> str:
    if not value:
        return "—（未记录数据截止时间）"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    offset = parsed.strftime("%z")
    zone = "UTC" if offset == "+0000" else "UTC" + offset[:3] + ":" + offset[3:]
    return parsed.strftime("%Y-%m-%d %H:%M:%S ") + zone


def badge(label: str, tone: str = "unknown") -> str:
    return f'<span class="badge badge-{e(tone)}">{e(label)}</span>'


def outlook_badge(report: dict) -> str:
    if report["provenance"]["kind"] == "validation":
        return badge("流程验证", "warning")
    return badge(*OUTLOOKS[report["outlook"]])


def quality_badge(report: dict) -> str:
    return badge(QUALITIES[report["analysis_quality"]], "warning" if report["analysis_quality"] != "pass" else "unknown")


def empty_state(title: str, explanation: str, glyph: bool = True) -> str:
    icon = '<span class="empty-glyph" aria-hidden="true">···</span>' if glyph else ""
    return f'<div class="empty-state">{icon}<h3>{e(title)}</h3><p>{e(explanation)}</p></div>'


def paragraphs(text: str) -> str:
    return '<div class="prose">' + "".join(f"<p>{e(part)}</p>" for part in text.split("\n\n") if part.strip()) + "</div>"


def safe_source_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValidationError("Unsafe source URL encountered while rendering")
    return value


def report_sort(report: dict) -> tuple:
    return report["trade_date"], report["revision"]


def daily_latest(reports: list[dict]) -> list[dict]:
    by_date: dict[str, dict] = {}
    for report in sorted(reports, key=report_sort):
        by_date[report["trade_date"]] = report
    return sorted(by_date.values(), key=report_sort)


def latest_research(reports: list[dict]) -> dict | None:
    actual = [r for r in reports if r["provenance"]["kind"] != "validation"]
    return max(actual or reports, key=report_sort) if reports else None


def history_report(reports: list[dict]) -> dict | None:
    eligible = [r for r in reports if r["price_history"] and r["provenance"]["kind"] != "validation"]
    return max(eligible, key=report_sort) if eligible else None


def json_filename(report: dict) -> str:
    suffix = "" if report["revision"] == 1 else f'.v{report["revision"]}'
    return f'{report["trade_date"]}{suffix}.json'


def sparkline(history: list[dict], label: str) -> str:
    if not history:
        return '<span class="spark-empty">尚无收盘价序列</span>'
    recent = history[-60:]
    values = [p["close"] for p in recent]
    low, high = min(values), max(values)
    span = high - low or high * .02
    coordinates = [(5 + i * 106 / max(1, len(values) - 1), 43 - (v - low) / span * 33) for i, v in enumerate(values)]
    if len(values) == 1:
        coordinates = [(58, 26)]
    color = COLORS[0] if values[-1] >= values[0] else COLORS[3]
    line = '<polyline points="' + " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates) + f'" fill="none" stroke="{color}" stroke-width="1.8" vector-effect="non-scaling-stroke"/>' if len(values) > 1 else ""
    x, y = coordinates[-1]
    return f'<svg class="sparkline" viewBox="0 0 116 54" role="img"><title>{e(label)}：{e(recent[0]["date"])} 至 {e(recent[-1]["date"])} 的真实收盘价</title>{line}<circle cx="{x:.2f}" cy="{y:.2f}" r="2.8" fill="{color}"/></svg>'


def chart(series: list[dict], *, unit: str, title: str, draw_lines: bool = True,
          report_links: dict[str, str] | None = None) -> str:
    """Responsive native SVG; every hover value is an observed input point.

    Two view boxes retain readable axes on mobile without a chart dependency.
    Target/stop series use draw_lines=False, so no unrecorded forecasts appear.
    """
    dates = sorted({point["date"] for item in series for point in item["points"]})
    if not dates:
        return empty_state("暂无可展示的历史数据", "尚未记录可核验数值；图表会在真实记录加入后出现。")
    values = [point["value"] for item in series for point in item["points"]]
    minimum, maximum = min(values), max(values)
    pad = (maximum - minimum) * .12 or max(abs(maximum) * .035, .01)
    minimum -= pad
    maximum += pad
    maps = [{p["date"]: p["value"] for p in item["points"]} for item in series]
    variants = []
    for css_class, width, height in (("chart-desktop", 840, 276), ("chart-mobile", 360, 240)):
        left, right, top, bottom = (63, 18, 19, 39)
        plot_width, plot_height = width - left - right, height - top - bottom
        xpos = {day: left + i * plot_width / max(1, len(dates) - 1) for i, day in enumerate(dates)}
        if len(dates) == 1:
            xpos[dates[0]] = left + plot_width / 2

        def ypos(value: float) -> float:
            return top + (maximum - value) / (maximum - minimum) * plot_height

        title_id = f"chart-title-{next(CHART_IDS)}"
        fragments = [f'<title id="{title_id}">{e(title)}。左右方向键浏览实际记录。</title>']
        for i in range(4):
            value = maximum - (maximum - minimum) * i / 3
            y = top + i * plot_height / 3
            label = f"{value:.1f}%" if unit == "%" else price(round(value, 3 if maximum < 10 else 2))
            fragments.append(f'<line class="chart-grid" x1="{left}" x2="{width - right}" y1="{y:.2f}" y2="{y:.2f}"/><text class="chart-axis-text" x="{left - 10}" y="{y + 3.5:.2f}" text-anchor="end">{e(label)}</text>')
        tick_count = 4 if width > 400 else 3
        tick_indices = sorted({round(i * (len(dates) - 1) / max(1, tick_count - 1)) for i in range(tick_count)})
        for index in tick_indices:
            day = dates[index]
            fragments.append(f'<text class="chart-axis-text" x="{xpos[day]:.2f}" y="{height - 13}" text-anchor="middle">{e(day[5:])}</text>')
        for item, values_by_date in zip(series, maps):
            coordinates = [(xpos[day], ypos(value)) for day, value in sorted(values_by_date.items())]
            color = item["color"]
            if draw_lines and len(coordinates) > 1:
                points = " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates)
                if len(series) == 1:
                    polygon = f'{coordinates[0][0]:.2f},{height - bottom} {points} {coordinates[-1][0]:.2f},{height - bottom}'
                    fragments.append(f'<polygon points="{polygon}" fill="{color}" fill-opacity=".065"/>')
                fragments.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round"/>')
            points_to_show = list(sorted(values_by_date.items())) if not draw_lines else [list(sorted(values_by_date.items()))[-1]]
            for day, value in points_to_show:
                point_title = f'{day} · {item["label"]} {percentage(value) if unit == "%" else price(value)} {"" if unit == "%" else unit}'
                circle = f'<circle cx="{xpos[day]:.2f}" cy="{ypos(value):.2f}" r="{4 if not draw_lines else 3}" fill="{color}" stroke="#111d2d" stroke-width="1.5"><title>{e(point_title)}</title></circle>'
                if report_links and day in report_links:
                    circle = f'<a href="{e(report_links[day])}" aria-label="{e(point_title)}，查看当日报告">{circle}</a>'
                fragments.append(circle)
        hover_points = []
        for day in dates:
            label_lines = [day]
            representative = None
            for item, values_by_date in zip(series, maps):
                if day in values_by_date:
                    value = values_by_date[day]
                    if representative is None:
                        representative = value
                    rendered = percentage(value) if unit == "%" else price(value) + " " + unit
                    label_lines.append(item["label"] + "  " + rendered)
            hover_points.append({"x": round(xpos[day], 3), "y": round(ypos(representative), 3), "label": "\n".join(label_lines)})
        fragments.append(f'<g class="chart-hover" display="none" aria-hidden="true"><line y1="{top}" y2="{height - bottom}"/><circle r="4.5"/></g>')
        hover_data = e(json.dumps(hover_points, ensure_ascii=False, separators=(",", ":")))
        variants.append(f'<div class="chart-frame {css_class}" data-chart-frame><svg class="research-chart" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{title_id}" tabindex="0" data-chart-points="{hover_data}">{"".join(fragments)}</svg><div class="chart-tooltip" hidden aria-live="off"></div></div>')
    return '<div class="chart-group">' + "".join(variants) + "</div>"


class Site:
    def __init__(self, root: Path, output: Path, base_path: str, symbols: list[dict], reports: list[dict]):
        self.root = root
        self.output = output
        self.base = base_path
        self.symbols = symbols
        self.reports = reports
        self.by_symbol: dict[str, list[dict]] = defaultdict(list)
        for report in reports:
            self.by_symbol[report["symbol"]].append(report)
        self.catalog = {s["symbol"]: s for s in symbols}
        self.pages: list[str] = []

    def url(self, path: str = "") -> str:
        return self.base + "/" + path.lstrip("/")

    def symbol_url(self, symbol: dict) -> str:
        return self.url(f'symbols/{symbol["slug"]}/')

    def report_url(self, symbol: dict, report: dict, version: bool = False) -> str:
        suffix = f'v{report["revision"]}.html' if version else ""
        return self.url(f'symbols/{symbol["slug"]}/{report["trade_date"]}/{suffix}')

    def data_url(self, report: dict) -> str:
        return self.url(f'data/reports/{report["symbol"]}/{json_filename(report)}')

    def write(self, relative_path: str, text: str) -> None:
        path = self.output / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if relative_path.endswith(".html"):
            self.pages.append(relative_path)

    def page(self, title: str, description: str, body: str, breadcrumbs: list[tuple[str, str | None]], overview: bool = False) -> str:
        crumbs = []
        for label, url in breadcrumbs:
            if crumbs:
                crumbs.append('<span class="crumb-separator" aria-hidden="true">/</span>')
            crumbs.append(f'<a href="{e(url)}">{e(label)}</a>' if url else f'<span class="crumb-current" aria-current="page">{e(label)}</span>')
        current = ' aria-current="page"' if overview else ""
        return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#09121f">
  <meta name="referrer" content="no-referrer">
  <title>{e(title)} · Trading Analyst</title>
  <meta name="description" content="{e(compact(description, 170))}">
  <link rel="stylesheet" href="{e(self.url('assets/styles.css'))}">
  <script src="{e(self.url('assets/app.js'))}" defer></script>
</head>
<body>
<a class="skip-link" href="#main">跳转到正文</a>
<header class="site-header"><div class="header-inner">
  <a class="brand" href="{e(self.url())}" aria-label="Trading Analyst 研究总览"><span class="brand-mark" aria-hidden="true">TA</span><span class="brand-name">Trading Analyst<span class="brand-subtitle">研究档案</span></span></a>
  <nav class="header-nav" aria-label="主导航"><a href="{e(self.url())}"{current}>研究总览</a><a href="{e(self.url('#coverage'))}">观察标的</a><span class="archive-mark">历史记录 · 非实时信号</span></nav>
</div></header>
<main class="shell" id="main">
<nav class="breadcrumbs" aria-label="面包屑导航">{"".join(crumbs)}</nav>
{body}
</main>
<footer class="site-footer"><div class="footer-inner"><div><span class="footer-brand">TRADING ANALYST / RESEARCH ARCHIVE</span>每份分析保留原始交易日期、数据截止时间与修订版本。</div><div class="footer-links"><a href="{e(self.url())}">返回总览</a><a href="{e(self.url('data/symbols.json'))}" download>公开标的索引 JSON</a><a href="{e(self.url('#method'))}">阅读说明</a></div></div></footer>
</body></html>
'''

    def overview_card(self, symbol: dict) -> str:
        reports = self.by_symbol[symbol["symbol"]]
        latest = latest_research(reports)
        stock_url = self.symbol_url(symbol)
        header = f'<div class="security-card-top"><div><a class="security-name" href="{e(stock_url)}">{e(symbol["name"])}</a><div class="security-code">{e(symbol["symbol"])}<span class="market-tag">{e(MARKETS[symbol["market"]])} · {e(symbol["currency"])}</span></div></div>{outlook_badge(latest) if latest else badge("等待研究")}</div>'
        attributes = f'data-security-card data-market="{e(symbol["market"])}" data-search="{e(symbol["symbol"] + " " + symbol["name"] + " " + symbol["slug"])}"'
        if not latest:
            return f'<article class="security-card card-empty" {attributes}>{header}<div class="empty-card-body"><p>已纳入观察目录，尚无分析归档。</p><small>收盘趋势、研究价位与日报将在真实报告写入后显示。</small></div><div class="card-footer"><span>尚无报告</span><a class="text-link" href="{e(stock_url)}">进入标的 ↗</a></div></article>'
        snapshot = latest["market_snapshot"]
        change = snapshot["change_pct"]
        tone = "positive" if change is not None and change >= 0 else "negative" if change is not None else "quiet"
        history = history_report(reports)
        levels = latest["price_levels"] or {}
        currency = symbol["currency"]
        mini_levels = "".join(f'<div><span class="level-mini-label">{label} · {e(currency)}</span><span class="level-mini-value">{e(price(levels.get(field)))}</span></div>' for label, field in (("当时目标", "target"), ("当时失效线", "stop")))
        return f'''<article class="security-card" {attributes}>
{header}
<div class="card-price-row"><div><div class="price-value">{e(price(snapshot["close"]))}<span class="price-currency">{e(currency)}</span></div><div class="price-change {tone}">{e(percentage(change))}<span class="quiet"> · 收盘涨跌</span></div><div class="price-caption">{e(latest["trade_date"])} 收盘快照</div></div>{sparkline(history["price_history"] if history else [], symbol["name"])}</div>
<a class="card-headline" href="{e(self.report_url(symbol, latest))}">{e(compact(latest["headline"], 48))}</a>
<p class="card-summary">{e(compact(latest["summary"], 87))}</p>
<div class="card-levels">{mini_levels}</div>
<div class="card-footer"><span>{e(latest["trade_date"])} · {e(PROVENANCE[latest["provenance"]["kind"]])}</span><a class="text-link" href="{e(stock_url)}">查看研究 ↗</a></div>
</article>'''

    def comparison(self) -> str:
        groups = []
        for currency in ("CNY", "HKD", "USD"):
            histories = []
            for symbol in self.symbols:
                if symbol["currency"] != currency:
                    continue
                report = history_report(self.by_symbol[symbol["symbol"]])
                if report and len(report["price_history"]) > 1:
                    histories.append((symbol, report))
            groups.append((currency, histories))
        selected = next((currency for currency, histories in groups if len(histories) >= 2), "CNY")
        tabs, panels = [], []
        for currency, histories in groups:
            active = currency == selected
            tabs.append(f'<button class="tab-button" type="button" data-compare-target="compare-{currency}" aria-controls="compare-{currency}" aria-pressed="{str(active).lower()}">{currency}<span class="filter-count">{len(histories)}</span></button>')
            if len(histories) < 2:
                contents = empty_state("同币种走势对比等待更多记录", f"{currency} 目前有 {len(histories)} 只标的提供了至少两个交易日的真实收盘价；需要至少两只标的及共同交易日期才能形成对比。")
            else:
                common_dates = set(p["date"] for p in histories[0][1]["price_history"])
                for _, report in histories[1:]:
                    common_dates &= {p["date"] for p in report["price_history"]}
                dates = sorted(common_dates)[-90:]
                if len(dates) < 2:
                    contents = empty_state("尚无足够的共同交易日", "现有记录的日期尚不足以对齐；不会连接不重叠的历史窗口。")
                else:
                    series, legend, rows = [], [], []
                    for index, (symbol, report) in enumerate(histories):
                        mapping = {point["date"]: point["close"] for point in report["price_history"]}
                        initial = mapping[dates[0]]
                        color = COLORS[index % len(COLORS)]
                        points = [{"date": day, "value": (mapping[day] / initial - 1) * 100} for day in dates]
                        series.append({"label": symbol["name"], "points": points, "color": color})
                        legend.append(f'<span class="legend-item"><svg width="8" height="8" aria-hidden="true"><circle cx="4" cy="4" r="3.5" fill="{color}"/></svg><a href="{e(self.symbol_url(symbol))}">{e(symbol["name"])}</a></span>')
                        change = points[-1]["value"]
                        rows.append(f'<tr><td><a class="text-link" href="{e(self.symbol_url(symbol))}">{e(symbol["name"])}</a></td><td class="numeric mono">{e(price(initial))}</td><td class="numeric mono">{e(price(mapping[dates[-1]]))}</td><td class="numeric mono {"positive" if change >= 0 else "negative"}">{e(percentage(change))}</td></tr>')
                    contents = '<div class="chart-legend">' + "".join(legend) + '</div>' + chart(series, unit="%", title=f"{currency} 同币种收盘走势对比")
                    contents += f'<div class="chart-note"><span>{e(dates[0])} — {e(dates[-1])} · {len(dates)} 个共同交易日</span><span>共同首日归零 · 单位为涨跌幅 %</span></div><details><summary>查看区间收盘价与涨跌幅（{currency}）</summary><div class="table-scroll"><table><thead><tr><th>标的</th><th class="numeric">起点收盘</th><th class="numeric">终点收盘</th><th class="numeric">区间变化</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div></details>'
            panels.append(f'<div id="compare-{currency}" data-compare-panel aria-label="{currency} 走势对比"{" hidden" if not active else ""}>{contents}</div>')
        return f'<section class="panel comparison-panel" aria-labelledby="compare-title"><div class="panel-heading stack-mobile"><div><h2 id="compare-title">走势对比</h2><p>按币种分组，使用各标的已有行情的共同交易日。不同币种价格不混绘。</p></div><div class="tabs" aria-label="选择对比币种">{"".join(tabs)}</div></div>{"".join(panels)}</section>'

    def overview(self) -> None:
        actual = [r for r in self.reports if r["provenance"]["kind"] != "validation"]
        covered = sum(any(r["provenance"]["kind"] != "validation" for r in self.by_symbol[s["symbol"]]) for s in self.symbols)
        last_date = max((r["trade_date"] for r in actual), default="尚无报告")
        days = len({(r["symbol"], r["trade_date"]) for r in actual})
        signals = defaultdict(int)
        for symbol in self.symbols:
            report = latest_research(self.by_symbol[symbol["symbol"]])
            if report and report["provenance"]["kind"] != "validation":
                signals[OUTLOOKS[report["outlook"]][1]] += 1
        direction_text = f'偏多 {signals["up"]} · 中性 {signals["neutral"]} · 偏空 {signals["down"]}'
        stat_items = (
            ("观察目录", str(len(self.symbols)), "只", "覆盖 A股 / 港股 / 美股"),
            ("已有研究", str(covered), f"/ {len(self.symbols)} 只", "未研究的标的保留为空状态"),
            ("研究日报", str(days), "份", "按标的与交易日计数，修订另存"),
            ("最新方向分布", str(signals["up"] + signals["neutral"] + signals["down"]), "只已形成判断", direction_text),
        )
        stats = "".join(f'<div class="stat"><span class="stat-label">{e(label)}</span><div class="stat-value">{e(value)}<small>{e(unit)}</small></div><div class="stat-foot">{e(note)}</div></div>' for label, value, unit, note in stat_items)
        filters = f'<button class="filter-button" data-market-filter="all" aria-pressed="true" type="button">全部<span class="filter-count">{len(self.symbols)}</span></button>'
        for market, label in MARKETS.items():
            filters += f'<button class="filter-button" data-market-filter="{market}" aria-pressed="false" type="button">{label}<span class="filter-count">{sum(s["market"] == market for s in self.symbols)}</span></button>'
        body = f'''<div class="page-heading"><div><p class="eyebrow">Research / Overview</p><h1>研究总览</h1><p class="page-description">先看趋势与最新判断，再进入标的历史，追溯每份收盘分析。</p></div><div class="heading-side">最新归档交易日<strong>{e(last_date)}</strong></div></div>
<section class="stats-row" aria-label="研究覆盖概况">{stats}</section>
<section id="coverage" aria-labelledby="coverage-title"><div class="section-heading"><div><h2 id="coverage-title">观察标的</h2><p>各卡片展示该标的最新归档分析；日期与信息完整程度可能不同。</p></div><span class="archive-mark">历史快照</span></div>
<noscript><p class="no-js-note">所有标的和报告均可直接阅读。启用 JavaScript 后可筛选、搜索与查看图表日值。</p></noscript>
<div class="toolbar"><div class="filter-group" aria-label="市场筛选">{filters}</div><div class="search-wrap"><label class="sr-only" for="security-search">搜索标的名称或代码</label><svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5.5" stroke="currentColor" stroke-width="1.5"/><path d="m13 13 4 4" stroke="currentColor" stroke-width="1.5"/></svg><input class="search-input" id="security-search" type="search" placeholder="搜索名称、代码" autocomplete="off" data-security-search></div></div>
<p class="coverage-status" data-coverage-status aria-live="polite">显示 {len(self.symbols)} / {len(self.symbols)} 只观察标的</p>
<div class="security-grid">{"".join(self.overview_card(symbol) for symbol in self.symbols)}</div><div data-no-results hidden>{empty_state("没有匹配的观察标的", "试试其他代码、名称，或切换到全部市场。")}</div></section>
{self.comparison()}
<section class="method-note" id="method" aria-label="阅读说明"><div><h3>三层研究结构</h3><p>总览 → 标的主页 → 按交易日归档的分析。每份日报保留独立链接；同日修订保留原始版本。</p></div><div><h3>行情历史与判断历史分开</h3><p>曲线仅来自实际收盘价。目标和失效价只出现在有研究记录的日期，不补造过往建议。</p></div><div><h3>日期与缺口保持可见</h3><p>交易日、数据截止时间、生成时间分别展示。“未形成判断”表示证据不足；历史记录不能视作实时信号。</p></div></section>'''
        self.write("index.html", self.page("研究总览", "A股、港股、美股的公开研究档案：总览、标的历史与每日分析。", body, [("研究总览", None)], True))

    def price_history_panel(self, symbol: dict, reports: list[dict]) -> str:
        report = history_report(reports)
        if not report:
            content = empty_state("尚无可核验的收盘价序列", "这里仅显示报告中记录的真实历史行情；不会生成占位走势。")
        else:
            history = report["price_history"]
            series = [{"label": "收盘价", "color": COLORS[0], "points": [{"date": p["date"], "value": p["close"]} for p in history]}]
            content = chart(series, unit=symbol["currency"], title=f'{symbol["name"]} 真实收盘历史')
            content += f'<div class="chart-note"><span>{e(history[0]["date"])} — {e(history[-1]["date"])} · {len(history)} 个已记录交易日</span><span>来源：<a class="text-link" href="{e(self.report_url(symbol, report, True))}">{e(report["trade_date"])} / v{report["revision"]}</a></span></div>'
            rows = "".join(f'<tr><td class="mono">{e(p["date"])}</td><td class="numeric mono">{e(price(p["close"]))}</td></tr>' for p in reversed(history))
            content += f'<details><summary>查看真实收盘记录（{symbol["currency"]}）</summary><div class="table-scroll history-details"><table><thead><tr><th>交易日期</th><th class="numeric">收盘价 · {symbol["currency"]}</th></tr></thead><tbody>{rows}</tbody></table></div></details>'
        return f'<section class="panel" id="price-history"><div class="panel-heading"><div><h2>行情历史</h2><p>真实收盘价，单位 {e(symbol["currency"])}。悬停或使用方向键查看日期与价格。</p></div><span class="panel-title-meta">收盘价序列</span></div>{content}</section>'

    def level_history_panel(self, symbol: dict, reports: list[dict]) -> str:
        daily = [r for r in daily_latest(reports) if r["provenance"]["kind"] != "validation"]
        recorded = [r for r in daily if r["price_levels"] and (r["price_levels"]["target"] is not None or r["price_levels"]["stop"] is not None)]
        if not recorded:
            content = empty_state("尚无历史研究价位", "后续报告记录目标或失效线后，将按实际报告日期显示；不从价格曲线反推旧预测。", False)
        else:
            series = []
            for field, label, color in (("close", "报告日收盘", "#9baec4"), ("target", "当时目标", COLORS[0]), ("stop", "当时失效线", COLORS[1])):
                points = []
                for report in recorded:
                    value = report["market_snapshot"]["close"] if field == "close" else report["price_levels"][field]
                    if value is not None:
                        points.append({"date": report["trade_date"], "value": value})
                if points:
                    series.append({"label": label, "color": color, "points": points})
            content = '<div class="chart-legend"><span class="legend-item"><span class="legend-dot slate"></span>报告日收盘</span><span class="legend-item"><span class="legend-dot"></span>当时目标</span><span class="legend-item"><span class="legend-dot amber"></span>当时失效线</span></div>'
            content += chart(series, unit=symbol["currency"], title=f'{symbol["name"]} 历史报告记录的研究价位', draw_lines=False, report_links={r["trade_date"]: self.report_url(symbol, r) for r in recorded})
            content += f'<p class="chart-note">仅显示 {len(recorded)} 个有价位记录的研究日期；点之间不连线，不向未记录日期延伸。同日以最新修订展示，原版保留在日报中。</p>'
        return f'<section class="panel" id="level-history"><div class="panel-heading"><div><h2>历史研究价位</h2><p>与行情历史分开展示，保留判断形成时的日期。</p></div><span class="panel-title-meta">单位 {e(symbol["currency"])}</span></div>{content}</section>'

    def archive_panel(self, symbol: dict, reports: list[dict]) -> str:
        daily = list(reversed(daily_latest(reports)))
        if not daily:
            content = empty_state("第一份分析尚未归档", "该标的的日期目录将在真实报告写入后生成。当前没有可跳转的日报。")
        else:
            rows = []
            for report in daily:
                versions = sorted([r for r in reports if r["trade_date"] == report["trade_date"]], key=report_sort)
                revisions = "".join(f'<a href="{e(self.report_url(symbol, version, True))}">v{version["revision"]}</a>' for version in versions)
                rows.append(f'<tr><td><a class="report-table-date" href="{e(self.report_url(symbol, report))}">{e(report["trade_date"])}</a><div class="table-revisions">{revisions}</div></td><td>{outlook_badge(report)}</td><td><a class="report-table-headline" href="{e(self.report_url(symbol, report))}">{e(report["headline"])}</a><span class="quiet small">{e(PROVENANCE[report["provenance"]["kind"]])} · {e(QUALITIES[report["analysis_quality"]])}</span></td><td><a class="text-link" href="{e(self.report_url(symbol, report))}">阅读 ↗</a></td></tr>')
            content = f'<div class="table-scroll"><table><thead><tr><th>交易日期 / 版本</th><th>当时方向</th><th>分析摘要</th><th>日报</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        return f'<section class="panel" id="report-history"><div class="panel-heading"><div><h2>每日分析</h2><p>选择交易日期进入完整报告；修订版本分别保留。</p></div><span class="panel-title-meta">{len(daily)} 个交易日 · {len(reports)} 个版本</span></div>{content}</section>'

    def symbol_page(self, symbol: dict) -> None:
        reports = self.by_symbol[symbol["symbol"]]
        latest = latest_research(reports)
        latest_button = f'<a class="button" href="{e(self.report_url(symbol, latest))}">阅读最新分析 ↗</a>' if latest else ""
        heading = f'<div class="page-heading security-heading"><div><p class="eyebrow">Research / {e(symbol["market"])}</p><h1>{e(symbol["name"])}<span class="title-code">{e(symbol["symbol"])}</span></h1><div class="inline-meta"><span>{e(MARKETS[symbol["market"]])} · {e(symbol["currency"])}</span><span>{len(daily_latest(reports))} 个研究日期</span><span>最新交易日：{e(latest["trade_date"]) if latest else "—"}</span></div></div><div class="button-row"><a class="button button-secondary" href="{e(self.url())}">← 返回总览</a>{latest_button}</div></div>'
        if latest:
            snapshot = f'<section class="panel"><div class="inline-meta">{outlook_badge(latest)}{quality_badge(latest)}{badge(PROVENANCE[latest["provenance"]["kind"]])}<span>{e(latest["trade_date"])} · v{latest["revision"]}</span></div><h2 class="snapshot-headline">{e(latest["headline"])}</h2><p class="snapshot-summary">{e(latest["summary"])}</p><a class="text-link small" href="{e(self.report_url(symbol, latest))}">打开完整结论、条件与来源 →</a></section>'
        else:
            snapshot = '<section class="panel">' + empty_state("已建立标的档案，等待首份研究", "目前只登记了观察标的。这里不会生成方向、指标、目标或模拟走势。真实分析写入后会自动进入总览与日期目录。") + '</section>'
        body = heading + '<div class="notice"><span class="notice-mark" aria-hidden="true">◷</span><p><strong>这是历史研究档案。</strong> 下方价格与判断对应各自记录日期，最新归档也不代表实时市场状态。</p></div>'
        body += snapshot + self.price_history_panel(symbol, reports) + self.level_history_panel(symbol, reports) + self.archive_panel(symbol, reports)
        self.write(f'symbols/{symbol["slug"]}/index.html', self.page(symbol["name"] + " · 标的研究", symbol["name"] + "的真实行情历史、研究价位与每日分析。", body, [("研究总览", self.url()), (symbol["name"], None)]))

    def snapshot_panel(self, symbol: dict, report: dict) -> str:
        snapshot = report["market_snapshot"]
        items = []
        for field, label in (("close", "收盘价"), ("change_pct", "日涨跌幅"), ("previous_close", "前收盘"), ("open", "开盘价"), ("high", "最高价"), ("low", "最低价")):
            value = snapshot[field]
            rendered = percentage(value) if field == "change_pct" else price(value)
            unit = "" if field == "change_pct" or value is None else symbol["currency"]
            tone = ("positive" if value >= 0 else "negative") if field == "change_pct" and value is not None else ""
            missing = '<span class="missing-reason">该项未记录；见数据限制</span>' if value is None else ""
            items.append(f'<div class="snapshot-item"><span class="snapshot-label">{label}</span><span class="snapshot-value {tone}">{e(rendered)}</span><span class="snapshot-unit">{e(unit)}</span>{missing}</div>')
        return f'<section class="panel" id="snapshot"><div class="inline-meta">{outlook_badge(report)}{quality_badge(report)}{badge(PROVENANCE[report["provenance"]["kind"]])}</div><h2 class="snapshot-headline">{e(report["headline"])}</h2><p class="snapshot-summary">{e(report["summary"])}</p><div class="snapshot-grid">{"".join(items)}</div></section>'

    def indicators_panel(self, report: dict) -> str:
        if not report["indicators"]:
            content = empty_state("未记录可用指标", "该份报告未提供指标值；不以零值替代缺失数据。", False)
        else:
            rows = []
            for item in report["indicators"]:
                value = e(number(item["value"]))
                if item["value"] is None:
                    value += '<span class="missing-reason">' + e(item["note"] or "原报告未记录该指标值") + '</span>'
                rows.append(f'<tr><td>{e(item["name"])}</td><td class="numeric mono">{value}</td><td class="quiet">{e(item["unit"] or "—")}</td><td class="table-note">{e(item["note"] or "未另行记录口径说明")}</td></tr>')
            content = f'<div class="table-scroll"><table><thead><tr><th>指标</th><th class="numeric">记录值</th><th>单位</th><th>口径 / 限制</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
        return f'<section class="panel" id="indicators"><div class="panel-heading"><div><h2>指标快照</h2><p>保留报告当时数值及其计算口径。</p></div></div>{content}</section>'

    def drivers_panel(self, report: dict) -> str:
        if not report["drivers"]:
            content = empty_state("尚未记录驱动分析", "本份报告没有可引用的驱动条目。", False)
        else:
            source_names = {source["id"]: (index, source["label"]) for index, source in enumerate(report["sources"], 1)}
            items = []
            for driver in report["drivers"]:
                links = "".join(f'<a href="#source-{e(source_id)}" aria-label="来源：{e(source_names[source_id][1])}">来源 {source_names[source_id][0]:02d} ↗</a>' for source_id in driver["source_ids"])
                references = f'<div class="source-refs">{links}</div>' if links else ""
                items.append(f'<article class="driver-item"><div class="driver-heading"><h3>{e(driver["title"])}</h3>{badge(*DIRECTIONS[driver["direction"]])}</div><p>{e(driver["evidence"])}</p>{references}</article>')
            content = '<div class="driver-list">' + "".join(items) + '</div>'
        return f'<section class="panel" id="drivers"><div class="panel-heading"><div><h2>核心驱动与多空证据</h2><p>方向判断与原始证据分开展示。</p></div></div>{content}</section>'

    def levels_panel(self, symbol: dict, report: dict) -> str:
        levels = report["price_levels"]
        if levels is None or levels["status"] == "unavailable":
            content = empty_state("本报告未形成研究价位", "当前记录没有可发布的目标、条件区间或失效线。缺值不代表零，也不隐含任何实时信号。", False)
        else:
            entries = "—" if levels["entry_low"] is None else price(levels["entry_low"]) + "–" + price(levels["entry_high"])
            cards = []
            for label, value, tone, reason in (("原条件区间", entries, "", "原报告未记录区间"), ("当时目标", price(levels["target"]), "target", "原报告未记录目标"), ("当时失效线", price(levels["stop"]), "stop", "原报告未记录失效价格")):
                missing = f'<span class="missing-reason">{e(reason)}</span>' if value == "—" else ""
                cards.append(f'<div class="level-detail {tone}"><span class="snapshot-label">{label} · {e(symbol["currency"])}</span><div class="snapshot-value">{e(value)}{missing}</div></div>')
            status = {"active": "报告当日有效", "conditional": "附条件", "expired": "原报告已标记失效", "unavailable": "未记录"}[levels["status"]]
            target_type = {"technical": "技术情景", "fundamental": "基本面估值", "model": "模型结果", None: "未记录"}[levels["target_type"]]
            values = (
                ("当时状态", status), ("目标类型", target_type), ("目标期限", levels["target_horizon"] or "—（未记录期限）"),
                ("原有效期", levels["entry_valid_until"] or "—（未记录截止日期）"),
                ("第一观察位", price(levels["take_profit_1"]) + (" " + symbol["currency"] if levels["take_profit_1"] is not None else "（未记录）")),
                ("第二观察位", price(levels["take_profit_2"]) + (" " + symbol["currency"] if levels["take_profit_2"] is not None else "（未记录）")),
                ("触发条件", levels["trigger"] or "—（未记录触发条件）"),
                ("失效条件", levels["invalidation"] or "—（未记录失效条件）"),
                ("价位依据", levels["basis"] or "—（未记录价位依据）"),
            )
            content = '<div class="levels-grid">' + "".join(cards) + '</div><dl class="definition-list">' + "".join(f'<dt>{e(label)}</dt><dd>{e(value)}</dd>' for label, value in values) + '</dl>'
        return f'<section class="panel" id="levels"><div class="panel-heading"><div><h2>关键价位与成立条件</h2><p>以下为报告形成时的分析情景，有效期不会因页面更新而延长。</p></div></div>{content}</section>'

    def scenarios_panel(self, symbol: dict, report: dict) -> str:
        if not report["scenarios"]:
            content = empty_state("未记录可用情景", "本报告没有形成可展示的价格情景。", False)
        else:
            cards = []
            for scenario in report["scenarios"]:
                missing = '<span class="missing-reason">该情景未给出价格</span>' if scenario["price"] is None else f'<span class="snapshot-unit">{e(symbol["currency"])}</span>'
                cards.append(f'<article class="scenario-card"><h3>{e(scenario["name"])}</h3><div class="scenario-price">{e(price(scenario["price"]))}{missing}</div><p>{e(scenario["condition"])}</p><span class="scenario-horizon">期限：{e(scenario["horizon"] or "未记录")}</span></article>')
            content = '<div class="scenario-grid">' + "".join(cards) + '</div>'
        return f'<section class="panel" id="scenarios"><div class="panel-heading"><div><h2>情景路径</h2><p>条件成立后的可能路径；价格触及本身不等于确认。</p></div></div>{content}</section>'

    def analysis_panel(self, report: dict) -> str:
        content = "".join(f'<section class="analysis-section"><h3>{e(section["heading"])}</h3>{paragraphs(section["body"])}</section>' for section in report["sections"])
        if not content:
            content = empty_state("尚无完整分析正文", "原记录没有正文段落；请以本页已展示的快照与限制为准。", False)
        return f'<section class="panel" id="analysis"><div class="panel-heading"><h2>分析正文</h2></div>{content}</section>'

    def prior_comparison_panel(self, symbol: dict, report: dict) -> str:
        comparison = report["comparison"]
        if comparison is None:
            content = empty_state("没有可用的前次对比", "当前报告未记录前次比较。不会由当前判断反推早前观点。", False)
        else:
            content = ""
            previous = comparison["previous_trade_date"]
            if previous:
                match = next((r for r in reversed(self.by_symbol[symbol["symbol"]]) if r["trade_date"] == previous), None)
                content += f'<p class="small muted">前次交易日：<a class="text-link" href="{e(self.report_url(symbol, match))}">{e(previous)} →</a></p>' if match else f'<p class="small muted">前次交易日：{e(previous)} · 本站尚无对应日报</p>'
            content += paragraphs(comparison["narrative"])
        return f'<section class="panel" id="comparison"><div class="panel-heading"><h2>与前次分析的变化</h2></div>{content}</section>'

    def sources_panel(self, report: dict) -> str:
        if report["sources"]:
            sources = []
            for index, source in enumerate(report["sources"], 1):
                url = safe_source_url(source["url"])
                published = time_label(source["published_at"]) if source["published_at"] else "发布时间未记录"
                sources.append(f'<li class="source-item" id="source-{e(source["id"])}"><span class="source-index">{index:02d}</span><div><a class="source-label" href="{e(url)}" target="_blank" rel="noopener noreferrer">{e(source["label"])} ↗</a><div class="source-meta">{e(urlsplit(url).hostname)} · {e(published)}</div></div></li>')
            source_html = '<ol class="source-list">' + "".join(sources) + '</ol>'
        else:
            source_html = empty_state("未记录公开来源", "该记录不具备可跳转的公开来源，请结合下方数据限制阅读。", False)
        limitations = '<ul class="limitations">' + "".join(f'<li>{e(item)}</li>' for item in report["limitations"]) + '</ul>' if report["limitations"] else '<p class="small muted">本报告未另行记录数据缺口；这不表示没有研究不确定性。</p>'
        provenance = report["provenance"]
        return f'<section class="panel" id="sources"><div class="panel-heading"><div><h2>来源与数据限制</h2><p>保留公开证据链接与缺失项，便于回看判断依据。</p></div></div>{source_html}<div class="section-divider"></div><h3 id="limitations">数据缺口与研究边界</h3>{limitations}<div class="section-divider"></div><dl class="definition-list"><dt>记录来源</dt><dd>{e(provenance["source"])}</dd><dt>归档说明</dt><dd>{e(provenance["note"])}</dd></dl></section>'

    def report_page(self, symbol: dict, report: dict, version: bool = False) -> None:
        reports = self.by_symbol[symbol["symbol"]]
        versions = sorted([r for r in reports if r["trade_date"] == report["trade_date"]], key=report_sort)
        latest = versions[-1]
        revision_links = []
        for other in versions:
            current = ' aria-current="page"' if other["revision"] == report["revision"] else ""
            revision_links.append(f'<a class="revision-link" href="{e(self.report_url(symbol, other, True))}"{current}>v{other["revision"]}</a>')
        revision_links = "".join(revision_links)
        latest_link = f'<a class="text-link" href="{e(self.report_url(symbol, latest))}">当日最新修订 ↗</a>' if report["revision"] != latest["revision"] else '<span>当前为该交易日最新修订</span>'
        warning = f'<p class="revision-warning">正在阅读早期版本 v{report["revision"]}。较新版本 v{latest["revision"]} 已独立归档，原文保持可追溯。</p>' if report["revision"] < latest["revision"] else ""
        notice_text = f'<strong>历史分析记录 · {e(report["trade_date"])}。</strong> 本页保留当时的价格、条件与判断。数据截止时间和生成时间见下方，不能将历史归档视为实时信号。'
        if report["provenance"]["kind"] == "validation":
            notice_text = '<strong>流程验证记录。</strong> 本页只用于验证归档链路，不是市场研究结果；不计入方向分布或走势对比。'
        times = "".join(f'<div><span class="timestamp-label">{label}</span><span class="timestamp-value">{e(value)}</span></div>' for label, value in (("交易日期", report["trade_date"]), ("数据截止时间", time_label(report["data_as_of"])), ("实际生成时间", time_label(report["generated_at"]))))
        sections = self.snapshot_panel(symbol, report) + self.indicators_panel(report) + self.drivers_panel(report) + self.levels_panel(symbol, report) + self.scenarios_panel(symbol, report) + self.analysis_panel(report) + self.prior_comparison_panel(symbol, report) + self.sources_panel(report)
        nav = "".join(f'<a href="#{anchor}">{label}</a>' for anchor, label in (("snapshot", "结论与收盘快照"), ("indicators", "指标快照"), ("drivers", "驱动与多空证据"), ("levels", "关键价位与条件"), ("scenarios", "情景路径"), ("analysis", "分析正文"), ("comparison", "前次对比"), ("sources", "来源与数据限制")))
        body = f'''<div class="page-heading daily-heading"><div><p class="eyebrow">Daily Research / Closing</p><h1>{e(symbol["name"])} · 收盘分析</h1><div class="inline-meta"><span class="mono">{e(symbol["symbol"])}</span><span>{e(report["trade_date"])}</span><span>{e(MARKETS[symbol["market"]])} · {e(symbol["currency"])}</span></div></div><a class="button button-secondary" href="{e(self.symbol_url(symbol))}">← 返回标的历史</a></div>
<div class="notice{" validation-notice" if report["provenance"]["kind"] == "validation" else ""}"><span class="notice-mark" aria-hidden="true">◷</span><p>{notice_text}</p></div>
<div class="timestamps">{times}</div>
<div class="revision-bar"><span>修订版本</span>{revision_links}<span>·</span>{latest_link}</div>{warning}
<div class="report-layout"><div class="report-main">{sections}</div><aside class="report-aside" aria-label="报告目录"><div class="panel aside-panel"><h2 class="aside-title">本页内容</h2><nav class="section-nav" aria-label="报告章节">{nav}</nav><div class="section-divider"></div><a class="button button-secondary" href="{e(self.data_url(report))}" download>下载本版公开 JSON ↓</a><p class="aside-provenance"><strong>{e(PROVENANCE[report["provenance"]["kind"]])} · v{report["revision"]}</strong><br>{e(report["provenance"]["note"])}</p></div></aside></div>'''
        suffix = f'v{report["revision"]}.html' if version else "index.html"
        title = f'{symbol["name"]} · {report["trade_date"]} · v{report["revision"]}'
        self.write(f'symbols/{symbol["slug"]}/{report["trade_date"]}/{suffix}', self.page(title, report["summary"], body, [("研究总览", self.url()), (symbol["name"], self.symbol_url(symbol)), (report["trade_date"] + f' / v{report["revision"]}', None)]))

    def build(self) -> None:
        for asset in ASSETS:
            source = self.root / "research" / "site" / asset
            if not source.is_file() or source.is_symlink():
                raise ValidationError(f"Missing or unsafe allowlisted asset: research/site/{asset}")
            target = self.output / "assets" / asset
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        self.write(".nojekyll", "")
        self.write("data/symbols.json", json.dumps({"schema_version": 1, "symbols": self.symbols}, ensure_ascii=False, indent=2) + "\n")
        for report in self.reports:
            self.write(f'data/reports/{report["symbol"]}/{json_filename(report)}', json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        self.overview()
        for symbol in self.symbols:
            self.symbol_page(symbol)
            for report in self.by_symbol[symbol["symbol"]]:
                self.report_page(symbol, report, version=True)
            for report in daily_latest(self.by_symbol[symbol["symbol"]]):
                self.report_page(symbol, report)


class LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("id"):
            if attributes["id"] in self.ids:
                raise ValidationError(f'Duplicate HTML id: {attributes["id"]}')
            self.ids.add(attributes["id"])
        for key in ("href", "src"):
            if attributes.get(key):
                self.references.append(attributes[key])


def check_links(output: Path, pages: list[str], base: str) -> int:
    """Check actual emitted files and fragment IDs, including all deep routes."""
    parsed_pages = {}
    for page in pages:
        parser = LinkCollector()
        parser.feed((output / page).read_text(encoding="utf-8"))
        parsed_pages[page] = parser
    checked = 0
    for page, parser in parsed_pages.items():
        for reference in parser.references:
            parsed = urlsplit(reference)
            if parsed.scheme:
                if parsed.scheme != "https":
                    raise ValidationError(f"Unsafe URL scheme in {page}: {parsed.scheme}")
                continue
            if parsed.netloc:
                raise ValidationError(f"Protocol-relative URL in {page}")
            if not parsed.path:
                target_relative = page
            else:
                expected_prefix = base + "/"
                if not parsed.path.startswith(expected_prefix):
                    raise ValidationError(f"Link escapes configured base path in {page}: {reference}")
                relative = unquote(parsed.path[len(expected_prefix):])
                if ".." in Path(relative).parts:
                    raise ValidationError(f"Link traverses output tree in {page}: {reference}")
                target_relative = relative + "index.html" if not relative or relative.endswith("/") else relative
            if not (output / target_relative).is_file():
                raise ValidationError(f"Broken local link in {page}: {reference}")
            if parsed.fragment:
                target_parser = parsed_pages.get(target_relative)
                if not target_parser or unquote(parsed.fragment) not in target_parser.ids:
                    raise ValidationError(f"Broken fragment link in {page}: {reference}")
            checked += 1
    return checked


def normalize_base_path(value: str) -> str:
    if value in ("", "/"):
        return ""
    value = value.rstrip("/")
    if not value.startswith("/") or not re.fullmatch(r"(?:/[A-Za-z0-9._-]+)+", value):
        raise ValidationError("--base-path must be an absolute URL path such as /trading-analyst")
    if any(segment in (".", "..") for segment in value.split("/")):
        raise ValidationError("--base-path must not contain traversal segments")
    return value


def build(root: Path, output: Path, base: str) -> dict:
    if output.is_symlink():
        raise ValidationError("Output must not be a symlink")
    root, output = root.resolve(), output.resolve()
    base = normalize_base_path(base)
    symbols, reports = load_dataset(root)
    # Never replace repository content; only previously managed build directories.
    protected = (root, root / "research", root / "scripts", root / "skills", root / "quant-studio", root / ".git")
    if output == root or output in root.parents or any(output == path or path in output.parents for path in protected[1:]):
        raise ValidationError("Output directory overlaps source or repository metadata")
    if output.exists():
        if not output.is_dir() or output.is_symlink():
            raise ValidationError("Output must be a regular directory")
        if any(output.iterdir()):
            manifest_path = output / "build-manifest.json"
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raise ValidationError("Refusing to replace a nonempty directory without a research-site build manifest") from None
            if existing.get("builder") != "trading-analyst-research-v1":
                raise ValidationError("Output directory is not owned by this research-site builder")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".research-site-", dir=output.parent))
    try:
        site = Site(root, staging, base, symbols, reports)
        site.build()
        link_count = check_links(staging, site.pages, base)
        file_entries = []
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                file_entries.append({"path": path.relative_to(staging).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest()})
        manifest = {
            "builder": "trading-analyst-research-v1",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "base_path": base,
            "symbol_count": len(symbols),
            "report_count": len(reports),
            "research_date_count": len({(r["symbol"], r["trade_date"]) for r in reports}),
            "html_page_count": len(site.pages),
            "file_count": len(file_entries) + 1,
            "validated_local_links": link_count,
            "source_allowlist": ["research/symbols.json", "research/reports/<symbol>/<date>[.vN].json", "research/site/styles.css", "research/site/app.js"],
            "files": file_entries,
            "manifest_file": "build-manifest.json",
            "routes": [{"symbol": s["symbol"], "path": f'symbols/{s["slug"]}/index.html', "report_dates": sorted({r["trade_date"] for r in site.by_symbol[s["symbol"]]})} for s in symbols],
        }
        (staging / "build-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            shutil.rmtree(output)
        staging.rename(output)
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", "--repo-root", type=Path, default=REPOSITORY_ROOT, help="Repository containing validated research JSON")
    parser.add_argument("--output", "--output-dir", type=Path, default=Path("_site"), help="Managed static output directory, relative to --root")
    parser.add_argument("--base-path", default="", help="Project URL prefix, for example /trading-analyst")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else args.root / args.output
    try:
        manifest = build(args.root, output, args.base_path)
    except (ValidationError, OSError) as error:
        print(f"Research site build failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"output": str(output.resolve()), "symbols": manifest["symbol_count"], "reports": manifest["report_count"], "pages": manifest["html_page_count"], "files": manifest["file_count"], "validated_local_links": manifest["validated_local_links"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
