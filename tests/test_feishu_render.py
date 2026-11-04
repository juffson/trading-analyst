"""Contract and data-integrity tests for native Feishu document rendering."""

from __future__ import annotations

import ast
import copy
from datetime import date, timedelta
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote

from scripts import feishu_render as render
from scripts.validate_research import load_dataset


ROOT = Path(__file__).resolve().parents[1]


def text_of(blocks):
    return "\n".join("".join(element["text_run"]["content"] for element in block[field]["elements"])
                     for block in blocks for field in ("text", "heading1", "heading2", "heading3", "bullet") if field in block)


def all_runs(blocks):
    return [element["text_run"] for block in blocks for value in block.values() if isinstance(value, dict)
            for element in value.get("elements", []) if "text_run" in element]


def all_links(blocks):
    return [unquote(run["text_element_style"]["link"]["url"]) for run in all_runs(blocks)
            if "link" in run.get("text_element_style", {})]


class NativeBlocksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.symbols, cls.reports = load_dataset(ROOT)
        cls.symbol = next(symbol for symbol in cls.symbols if symbol["symbol"] == "002594.SZ")
        cls.report = next(report for report in cls.reports if report["symbol"] == cls.symbol["symbol"])
        cls.overview_url = "https://example.feishu.cn/docx/overview"
        cls.symbol_url = "https://example.feishu.cn/docx/byd"

    def render_report(self, report=None):
        return render.render_report(self.symbol, report or self.report,
                                    symbol_url=self.symbol_url, overview_url=self.overview_url,
                                    page_url="https://example.github.io/trading-analyst/symbols/byd/")

    def test_managed_identity_first_block_and_deterministic_pure_output(self):
        original = copy.deepcopy(self.report)
        blocks = self.render_report()
        key = "report:002594.SZ:" + self.report["trade_date"] + ":" + str(self.report["revision"])
        self.assertEqual(blocks[0], render.managed_notice(key))
        self.assertEqual(blocks, self.render_report())
        self.assertEqual(self.report, original)
        self.assertNotIn("children", json.dumps(blocks))
        self.assertTrue(all(block["block_type"] in {2, 3, 4, 5, 12} for block in blocks))
        self.assertEqual(render.image_placeholder(), {"block_type": 27, "image": {}})
        with self.assertRaises(ValueError):
            render.managed_notice("overview\nother-document")

    def test_immutable_report_ignores_catalog_name_and_optional_pages_url(self):
        baseline = render.render_report(self.symbol, self.report, symbol_url=self.symbol_url,
                                        overview_url=self.overview_url, page_url=None)
        renamed = dict(self.symbol, name="后续修改的公司展示名称")
        configured = render.render_report(renamed, self.report, symbol_url=self.symbol_url,
                                          overview_url=self.overview_url,
                                          page_url="https://changed.github.io/new-pages-path/")
        self.assertEqual(baseline, configured)
        self.assertNotIn("后续修改的公司展示名称", text_of(configured))
        self.assertNotIn(self.symbol["symbol"] + " · " + self.symbol["symbol"], text_of(configured))
        self.assertNotIn("https://changed.github.io/new-pages-path/", all_links(configured))
        symbol_blocks = render.render_symbol(renamed, [self.report], report_urls={},
                                              overview_url=self.overview_url,
                                              page_url="https://changed.github.io/new-pages-path/")
        self.assertIn("后续修改的公司展示名称", text_of(symbol_blocks))
        self.assertIn("https://changed.github.io/new-pages-path/", all_links(symbol_blocks))
        overview_blocks = render.render_overview([renamed], [self.report], symbol_urls={},
                                                 report_urls={},
                                                 page_url="https://changed.github.io/new-pages-path/")
        self.assertIn("后续修改的公司展示名称", text_of(overview_blocks))
        self.assertIn("https://changed.github.io/new-pages-path/", all_links(overview_blocks))

    def test_report_completion_marker_is_unique_and_after_all_history(self):
        key = "report:002594.SZ:" + self.report["trade_date"] + ":" + str(self.report["revision"])
        marker = render.completed_notice(key)
        blocks = self.render_report()
        self.assertEqual(blocks[-1], marker)
        self.assertEqual(sum(block == marker for block in blocks), 1)
        self.assertIn(self.report["price_history"][-1]["date"], text_of(blocks[-2:-1]))
        empty = copy.deepcopy(self.report)
        empty["price_history"] = []
        empty_blocks = self.render_report(empty)
        self.assertEqual(empty_blocks[-1], marker)
        self.assertIn("没有记录可核验的收盘价序列", text_of(empty_blocks[-2:-1]))
        self.assertNotIn("发布", text_of([marker]))
        self.assertNotIn("图片", text_of([marker]))
        with self.assertRaises(ValueError):
            render.completed_notice("report:002594.SZ:2026-10-19:1\nother-document")

    def test_long_unicode_runs_preserve_every_character_and_link_style(self):
        text = "分析🌱e\u0301\n" * 1700
        label = "证据🔗" * 900
        url = "https://example.com/研究?q=a%20b&x=1#证据"
        parts = [("前缀：", None, True), (text, None, False), (label, url, False)]
        blocks = render._rich_blocks(parts)
        runs = all_runs(blocks)
        self.assertEqual("".join(run["content"] for run in runs), "前缀：" + text + label)
        self.assertTrue(all(0 < len(run["content"]) <= 1500 for run in runs))
        linked_runs = [run for run in runs if "link" in run.get("text_element_style", {})]
        self.assertEqual("".join(run["content"] for run in linked_runs), label)
        self.assertTrue(all(unquote(run["text_element_style"]["link"]["url"]) == url for run in linked_runs))
        self.assertTrue(all(run["text_element_style"]["link"]["url"].startswith("https%3A%2F%2F") for run in linked_runs))

    def test_report_fields_evidence_conditions_and_times_are_preserved(self):
        blocks = self.render_report()
        text = text_of(blocks)
        for key in ("symbol", "trade_date", "data_as_of", "generated_at", "report_kind", "outlook", "analysis_quality", "headline", "summary"):
            self.assertIn(str(self.report[key]), text, key)
        for item in self.report["sections"]:
            self.assertIn(item["heading"], text)
            self.assertIn(item["body"], text)
        for item in self.report["indicators"]:
            self.assertIn(item["name"], text)
            if item["value"] is not None:
                self.assertIn(str(item["value"]), text)
            if item["note"]:
                self.assertIn(item["note"], text)
        for driver in self.report["drivers"]:
            self.assertIn(driver["title"], text)
            self.assertIn(driver["evidence"], text)
        for key, value in self.report["price_levels"].items():
            if value is not None:
                self.assertIn(str(value), text, key)
        for scenario in self.report["scenarios"]:
            for value in scenario.values():
                if value is not None:
                    self.assertIn(str(value), text)
        for value in self.report["provenance"].values():
            self.assertIn(value, text)
        self.assertIn(self.report["comparison"]["narrative"], text)
        for value in self.report["limitations"]:
            self.assertIn(value, text)
        for point in self.report["price_history"]:
            self.assertIn(point["date"], text)
            self.assertIn(str(point["close"]), text)
        self.assertIn("不能视为实时信号", text)
        links = all_links(blocks)
        self.assertIn(self.overview_url, links)
        self.assertIn(self.symbol_url, links)
        for source in self.report["sources"]:
            self.assertIn(source["url"], links)
            self.assertIn(source["label"], text)
            if source["published_at"]:
                self.assertIn(source["published_at"], text)

    def test_blocked_missing_fields_are_explicit_not_zero_or_neutral(self):
        report = copy.deepcopy(self.report)
        report.update({"outlook": "Unknown", "analysis_quality": "blocked", "data_as_of": None,
                       "headline": "数据不足", "summary": "未形成方向判断", "price_levels": None,
                       "indicators": [], "drivers": [], "scenarios": [], "sections": [],
                       "sources": [], "comparison": None, "price_history": []})
        report["market_snapshot"] = {key: None for key in report["market_snapshot"]}
        report["limitations"] = ["缺少可核验收盘行情。"]
        text = text_of(self.render_report(report))
        self.assertIn("Unknown（未形成判断）", text)
        self.assertIn("blocked（分析阻塞）", text)
        self.assertIn("—（未记录价位）", text)
        self.assertIn("原报告未记录行情截止时间", text)
        self.assertIn("缺少可核验收盘行情", text)
        self.assertNotIn("Neutral（中性）", text)
        self.assertNotIn("0.00 CNY", text)

    def test_symbol_versions_link_individually_and_GOOG_has_empty_state(self):
        revised = copy.deepcopy(self.report)
        revised["revision"] = 2
        revised["headline"] = "第二修订独立标题"
        version_urls = {(self.report["trade_date"], 1): "https://example.feishu.cn/docx/v1",
                        (self.report["trade_date"], 2): "https://example.feishu.cn/docx/v2"}
        blocks = render.render_symbol(self.symbol, [revised, self.report], report_urls=version_urls, overview_url=self.overview_url)
        self.assertEqual(blocks[0], render.managed_notice("symbol:002594.SZ"))
        links = all_links(blocks)
        self.assertIn(version_urls[(self.report["trade_date"], 1)], links)
        self.assertIn(version_urls[(self.report["trade_date"], 2)], links)
        self.assertIn("第二修订独立标题", text_of(blocks))
        self.assertIn("当日最新修订", text_of(blocks))
        goog = next(symbol for symbol in self.symbols if symbol["symbol"] == "GOOG.US")
        # Empty-state coverage must not depend on GOOG having no live reports.
        empty = render.render_symbol(goog, [], report_urls={}, overview_url=self.overview_url)
        self.assertIn("等待首份研究", text_of(empty))
        self.assertIn("不生成方向", text_of(empty))
        self.assertNotIn("当时目标：", text_of(empty))

    def test_overview_has_all_symbols_currency_groups_and_report_links(self):
        symbol_urls = {symbol["symbol"]: "https://example.feishu.cn/docx/" + symbol["slug"] for symbol in self.symbols}
        report_urls = {(report["symbol"], report["trade_date"], report["revision"]): "https://example.feishu.cn/docx/" + report["symbol"] for report in self.reports}
        blocks = render.render_overview(self.symbols, self.reports, symbol_urls=symbol_urls, report_urls=report_urls)
        self.assertEqual(blocks[0], render.managed_notice("overview"))
        text = text_of(blocks)
        for symbol in self.symbols:
            self.assertIn(symbol["symbol"], text)
            self.assertIn(symbol_urls[symbol["symbol"]], all_links(blocks))
        # Keep the full-data checks above and exercise an explicit empty dataset.
        empty_blocks = render.render_overview(self.symbols, [], symbol_urls=symbol_urls, report_urls={})
        self.assertIn("等待首份研究", text_of(empty_blocks))
        self.assertIn("A股 / CNY", text)
        self.assertIn("港股 / HKD", text)
        self.assertIn("美股 / USD", text)
        self.assertIn("不同币种价格不混绘", text)
        for url in report_urls.values():
            self.assertIn(url, all_links(blocks))

    def test_unsafe_link_and_mismatched_symbol_fail(self):
        with self.assertRaises(ValueError):
            render.render_report(self.symbol, self.report, symbol_url="javascript:alert(1)", overview_url=self.overview_url)
        other = copy.deepcopy(self.symbol)
        other["symbol"] = "GOOG.US"
        with self.assertRaises(ValueError):
            render.render_report(other, self.report, symbol_url=self.symbol_url, overview_url=self.overview_url)

    def test_latest_series_does_not_fall_back_splice_or_include_validation(self):
        newer = copy.deepcopy(self.report)
        newer["trade_date"] = "2026-10-20"
        newer["price_history"] = []
        report, history = render._series(self.symbol, [self.report, newer])
        self.assertEqual(report["trade_date"], "2026-10-20")
        self.assertEqual(history, [])
        validation = copy.deepcopy(newer)
        validation["provenance"]["kind"] = "validation"
        report, history = render._series(self.symbol, [self.report, validation])
        self.assertEqual(report["trade_date"], self.report["trade_date"])
        self.assertEqual(history, self.report["price_history"][-120:])
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder)/"missing.png"
            self.assertFalse(render.trend_png(self.symbol, [newer], destination))
            self.assertFalse(destination.exists())

    def test_python_310_syntax(self):
        ast.parse((ROOT/"scripts/feishu_render.py").read_text(), feature_version=(3, 10))


class PriceImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import matplotlib
        except ImportError:
            raise unittest.SkipTest("matplotlib is not installed; renderer core has no plotting dependency")
        matplotlib.use("Agg", force=True)
        cls.symbol = {"symbol": "TEST.US", "name": "Test", "market": "US", "currency": "USD", "slug": "test-us"}
        _, reports = load_dataset(ROOT)
        cls.report = copy.deepcopy(reports[0])
        cls.report["symbol"] = "TEST.US"
        cls.report["trade_date"] = "2026-10-19"
        first = date(2026, 1, 1)
        cls.report["price_history"] = [{"date": (first + timedelta(days=i)).isoformat(), "close": 10 + i/10} for i in range(150)]
        cls.report["price_levels"] = copy.deepcopy(cls.report["price_levels"])
        if cls.report["price_levels"]:
            cls.report["price_levels"]["target"] = 999999

    def test_trend_png_plots_exact_last_120_observed_values_and_currency(self):
        from matplotlib.axes import Axes
        original = Axes.plot
        plotted = []
        def capture(axis, x, y, *args, **kwargs):
            plotted.append((list(x), list(y), kwargs.get("label")))
            return original(axis, x, y, *args, **kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(Axes, "plot", capture):
            destination = Path(folder)/"trend.png"
            self.assertTrue(render.trend_png(self.symbol, [self.report], destination))
            png = destination.read_bytes()
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(struct.unpack(">II", png[16:24]), (1792, 768))
            self.assertIn(b"Currency: USD", png)
            self.assertIn(b"report: 2026-10-19", png)
        self.assertEqual(len(plotted), 1)
        self.assertEqual(plotted[0][1], [point["close"] for point in self.report["price_history"][-120:]])
        self.assertEqual([day.date().isoformat() for day in plotted[0][0]], [point["date"] for point in self.report["price_history"][-120:]])
        self.assertNotIn(999999, plotted[0][1])

    def test_overview_uses_common_dates_and_never_mixes_currency_panels(self):
        other = dict(self.symbol, symbol="OTHER.US", slug="other-us")
        second = copy.deepcopy(self.report)
        second["symbol"] = other["symbol"]
        second["price_history"] = [{"date": point["date"], "close": point["close"]*2} for point in self.report["price_history"][60:]]
        hong_kong = dict(self.symbol, symbol="0001.HK", name="HK", market="HK", currency="HKD", slug="0001-hk")
        hk_report = copy.deepcopy(self.report)
        hk_report["symbol"] = hong_kong["symbol"]
        groups = render._comparison_groups([self.symbol, other, hong_kong], [self.report, second, hk_report])
        us = next(group for group in groups if group["currency"] == "USD")
        hk = next(group for group in groups if group["currency"] == "HKD")
        self.assertEqual(us["dates"], [point["date"] for point in second["price_history"]])
        self.assertEqual(len(us["series"]), 2)
        self.assertTrue(hk["reason"])
        self.assertTrue(all(symbol["currency"] == "USD" for symbol, _, _ in us["series"]))
        from matplotlib.axes import Axes
        original = Axes.plot
        plotted = []
        def capture(axis, x, y, *args, **kwargs):
            plotted.append((axis, list(x), list(y), kwargs["label"]))
            return original(axis, x, y, *args, **kwargs)
        with tempfile.TemporaryDirectory() as folder, patch.object(Axes, "plot", capture):
            self.assertTrue(render.overview_png([self.symbol, other, hong_kong], [self.report, second, hk_report], Path(folder)/"overview.png"))
        self.assertEqual(len(plotted), 2)
        self.assertIs(plotted[0][0], plotted[1][0])
        self.assertEqual(plotted[0][2][0], 0)
        self.assertEqual(plotted[1][2][0], 0)
        self.assertEqual(plotted[0][1], plotted[1][1])
        self.assertEqual(plotted[0][2], plotted[1][2])
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(render.overview_png([self.symbol, hong_kong], [self.report, hk_report], Path(folder)/"none.png"))


if __name__ == "__main__":
    unittest.main()
