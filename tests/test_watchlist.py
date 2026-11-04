"""Observation changes must not remove immutable archive coverage."""

import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_research_site import build
from scripts.feishu_render import render_overview, render_report
from scripts.validate_research import ValidationError, load_dataset, load_watchlist, quote_unit


ROOT = Path(__file__).resolve().parents[1]


def text_of(blocks):
    return "\n".join(element.get("text_run", {}).get("content", "")
                     for block in blocks
                     for value in block.values() if isinstance(value, dict)
                     for element in value.get("elements", []))


class WatchlistTests(unittest.TestCase):
    def test_benchmark_indices_use_points_without_changing_security_units(self):
        from copy import deepcopy
        from test_research import sample_report
        symbols, _ = load_dataset(ROOT)
        for code in ["000001.SH", "399006.SZ"]:
            symbol = next(s for s in symbols if s["symbol"] == code)
            self.assertEqual(quote_unit(symbol), "点")
            report = deepcopy(sample_report())
            report["symbol"] = code
            output = text_of(render_report(symbol, report, symbol_url="https://example.com/index", overview_url="https://example.com/overview"))
            self.assertIn("市场 / 单位", output)
            self.assertIn("100.00 点", output)
            self.assertNotIn("100.00 CNY", output)
        self.assertEqual(quote_unit(next(s for s in symbols if s["symbol"] == "002594.SZ")), "CNY")

    def test_invalid_watchlists_fail_instead_of_silently_changing_scope(self):
        symbols, _ = load_dataset(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            research = root / "research"
            research.mkdir()
            path = research / "watchlist.json"
            self.assertEqual(load_watchlist(root, symbols), symbols)
            for value in ({"schema_version": True, "symbols": []},
                          {"schema_version": 1, "symbols": ["UNKNOWN.US"]},
                          {"schema_version": 1, "symbols": ["002594.SZ", "002594.SZ"]},
                          {"schema_version": 1, "symbols": [], "extra": "not allowed"}):
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    with self.assertRaises(ValidationError):
                        load_watchlist(root, symbols)
            path.unlink()
            path.symlink_to(ROOT / "research/watchlist.json")
            with self.assertRaises(ValidationError):
                load_watchlist(root, symbols)

    def test_current_cn_list_and_feishu_archive_links_are_separate(self):
        symbols, reports = load_dataset(ROOT)
        observed = load_watchlist(ROOT, symbols)
        self.assertEqual([s["symbol"] for s in observed if s["market"] == "CN"],
                         ["002463.SZ", "002594.SZ", "601138.SH", "513130.SH", "300476.SZ", "000001.SH", "399006.SZ"])
        links = {s["symbol"]: "https://example.com/" + s["symbol"] for s in symbols}
        blocks = render_overview(symbols, reports, symbol_urls=links,
                                 report_urls={}, observed_symbols=observed)
        text = text_of(blocks)
        cn = text.split("A股观察标的\n", 1)[1].split("港股观察标的\n", 1)[0]
        archive = text.split("历史档案（已停止日常跟踪）\n", 1)[1]
        for code in ["688350.SH", "603588.SH", "688322.SH", "000636.SZ"]:
            self.assertNotIn(code, cn)
            self.assertIn(code, archive)
            self.assertTrue(any(r["symbol"] == code for r in reports))
        self.assertEqual(len(symbols), 19)
        self.assertEqual(len(observed), 15)

    def test_site_keeps_removed_symbol_routes_and_original_report_json(self):
        symbols, reports = load_dataset(ROOT)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site"
            manifest = build(ROOT, output, "/trading-analyst")
            self.assertEqual(manifest["symbol_count"], len(symbols))
            self.assertEqual(manifest["observed_symbol_count"], 15)
            self.assertEqual(manifest["report_count"], len(reports))
            index = (output / "index.html").read_text()
            coverage = index.split('<section id="coverage"', 1)[1].split("</section>", 1)[0]
            for code in ["688350.SH", "603588.SH", "688322.SH", "000636.SZ"]:
                self.assertNotIn(code, coverage)
                symbol = next(s for s in symbols if s["symbol"] == code)
                self.assertTrue((output / "symbols" / symbol["slug"] / "index.html").is_file())
                for original in (ROOT / "research/reports" / code).glob("*.json"):
                    exported = output / "data/reports" / code / original.name
                    self.assertEqual(json.loads(exported.read_text()), json.loads(original.read_text()))


if __name__ == "__main__":
    unittest.main()
