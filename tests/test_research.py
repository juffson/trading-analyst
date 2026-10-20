"""Meaningful publication-boundary tests; fixture prices are synthetic."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from scripts.validate_research import ValidationError, load_dataset, validate_append_only, validate_report, validate_symbols


CATALOG = {
    "schema_version": 1,
    "symbols": [
        {"symbol": "002594.SZ", "name": "比亚迪", "market": "CN", "currency": "CNY", "slug": "byd"},
        {"symbol": "GOOG.US", "name": "Alphabet", "market": "US", "currency": "USD", "slug": "alphabet"},
    ],
}
SYMBOLS = {item["symbol"]: item for item in CATALOG["symbols"]}


def sample_report():
    """A schema fixture, not an assertion about real market prices."""
    return {
        "schema_version": 1,
        "symbol": "002594.SZ",
        "trade_date": "2026-10-19",
        "revision": 1,
        "data_as_of": "2026-10-19T15:00:00+08:00",
        "generated_at": "2026-10-20T08:00:00Z",
        "report_kind": "closing",
        "outlook": "Neutral",
        "analysis_quality": "pass",
        "headline": "单元测试示例",
        "summary": "公司营业成本下降，毛利率改善；需求仍需观察。",
        "market_snapshot": {"close": 100, "previous_close": 99, "change_pct": 1.01, "open": 99, "high": 101, "low": 98},
        "indicators": [{"name": "样本指标", "value": 1.2, "unit": "%", "note": None}],
        "drivers": [{"title": "需求", "direction": "mixed", "evidence": "等待下一期公开数据。", "source_ids": ["exchange"]}],
        "price_levels": None,
        "scenarios": [],
        "sections": [{"heading": "观察事项", "body": "公司经营成本变化与需求节奏。"}],
        "comparison": None,
        "sources": [{"id": "exchange", "label": "交易所", "url": "https://www.szse.cn/", "published_at": None}],
        "limitations": [],
        "price_history": [{"date": "2026-10-16", "close": 99}, {"date": "2026-10-19", "close": 100}],
        "provenance": {"kind": "historical_backfill", "source": "测试夹具", "note": "合成数字仅用于单元测试。"},
    }


class ResearchValidationTests(unittest.TestCase):
    def write_dataset(self, root, reports=None):
        research = Path(root) / "research"
        research.mkdir()
        (research / "symbols.json").write_text(json.dumps(CATALOG), encoding="utf-8")
        (research / "reports").mkdir()
        for report in reports if reports is not None else [sample_report()]:
            directory = research / "reports" / report["symbol"]
            directory.mkdir(exist_ok=True)
            suffix = "" if report["revision"] == 1 else f".v{report['revision']}"
            path = directory / f"{report['trade_date']}{suffix}.json"
            path.write_text(json.dumps(report), encoding="utf-8")
        return research

    def assert_invalid(self, report, path=None):
        with self.assertRaises(ValidationError):
            validate_report(report, SYMBOLS, path)

    def test_valid_backfill_and_company_cost_text_are_accepted(self):
        report = sample_report()
        self.assertIs(validate_report(report, SYMBOLS, "research/reports/002594.SZ/2026-10-19.json"), report)
        self.assertNotEqual(report["trade_date"], report["generated_at"][:10])
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root)
            symbols, reports = load_dataset(root)
            self.assertEqual(len(symbols), 2)
            self.assertEqual(reports, [report])
            self.assertEqual(set(reports[0]), set(report))

    def test_raw_json_rejects_private_fields_and_personal_text(self):
        for field in ("positions", "account", "transactions", "cost_basis"):
            with self.subTest(field=field):
                report = sample_report()
                report[field] = {"quantity": 900}
                self.assert_invalid(report)
        report = sample_report()
        report["market_snapshot"]["private_note"] = "anything"
        self.assert_invalid(report)
        for text in (
            "我当前持有比亚迪 900 股。",
            "持仓数量为 900，成本价为 80。",
            "账户余额：10000。",
            "My portfolio contains 900 shares.",
            "Unrealized PnL is 200 USD.",
        ):
            with self.subTest(text=text):
                report = sample_report()
                report["provenance"]["note"] = text
                self.assert_invalid(report)

    def test_links_are_checked_even_in_unrendered_plain_text(self):
        for url in (
            "https://app.notion.com/secret-page",
            "https://app.notion%2ecom/secret-page",
            "https://workspace.notion.site/secret",
            "https://tenant.feishu.cn/wiki/private",
            "https://tenant.feishu%2ecn/wiki/private",
            "https://docs.google.com/document/d/private",
            "https://127.0.0.1/research",
            "https://169.254.169.254/latest/meta-data",
            "https://internal.local/report",
            "https://user:password@www.szse.cn/report",
            "https://www.szse.cn/report?access_token=private",
            "https://www.szse.cn/report#session=private",
            "http://www.szse.cn/report",
        ):
            with self.subTest(url=url):
                report = sample_report()
                report["sources"][0]["url"] = url
                self.assert_invalid(report)
                report = sample_report()
                report["provenance"]["note"] = "来源参见 " + url
                self.assert_invalid(report)
        report = sample_report()
        report["drivers"][0]["source_ids"] = ["nonexistent"]
        self.assert_invalid(report)

    def test_html_and_active_urls_never_enter_published_json(self):
        for text in (
            '<img src=x onerror="alert(1)">',
            '<script>alert(1)</script>',
            '&lt;svg onload=alert(1)&gt;',
            '[source](javascript:alert(1))',
            'java\nscript:alert(1)',
            'data:text/html,script',
            'file:///private/file.txt',
        ):
            with self.subTest(text=text):
                report = sample_report()
                report["sections"][0]["body"] = text
                self.assert_invalid(report)

    def test_market_dates_timestamps_and_currency_have_real_semantics(self):
        for key, value in (
            ("trade_date", "2026-02-30"),
            ("trade_date", "2026-10-18"),
            ("generated_at", "2026-10-20T08:00:00"),
            ("generated_at", "2026-10-19T06:59:59Z"),
            ("data_as_of", "2026-10-21T08:00:00Z"),
            ("data_as_of", "2026-10-19T06:59:59Z"),
        ):
            with self.subTest(key=key, value=value):
                report = sample_report()
                report[key] = value
                self.assert_invalid(report)
        report = sample_report()
        report.update(symbol="GOOG.US", generated_at="2026-10-19T19:59:59Z", data_as_of="2026-10-19T19:59:59Z")
        self.assert_invalid(report)
        report.update(generated_at="2026-10-19T20:01:00Z", data_as_of="2026-10-19T20:00:00Z")
        validate_report(report, SYMBOLS)
        for key, value in (("currency", "USD"), ("market", "HK"), ("symbol", "../../secret")):
            with self.subTest(key=key):
                catalog = deepcopy(CATALOG)
                catalog["symbols"][0][key] = value
                with self.assertRaises(ValidationError):
                    validate_symbols(catalog)

    def test_price_consistency_finite_values_and_unknown_quality(self):
        for field, value in (("close", float("nan")), ("close", float("inf")), ("close", -1), ("low", 102), ("change_pct", 20), ("open", True)):
            with self.subTest(field=field, value=value):
                report = sample_report()
                report["market_snapshot"][field] = value
                self.assert_invalid(report)
        report = sample_report()
        report["indicators"][0]["value"] = float("nan")
        self.assert_invalid(report)
        report = sample_report()
        report["revision"] = True
        self.assert_invalid(report)
        report = sample_report()
        report["market_snapshot"]["close"] = None
        self.assert_invalid(report)
        report.update(outlook="Unknown", analysis_quality="blocked", limitations=["缺少收盘数据。"], price_history=[])
        validate_report(report, SYMBOLS)
        report["outlook"] = "Neutral"
        self.assert_invalid(report)
        report = sample_report()
        report["provenance"]["kind"] = "validation"
        self.assert_invalid(report)

    def test_history_never_backdates_or_duplicates_prices(self):
        for history in (
            [{"date": "2026-10-20", "close": 100}],
            [{"date": "2026-10-16", "close": 99}, {"date": "2026-10-16", "close": 100}],
            [{"date": "2026-10-19", "close": 100}, {"date": "2026-10-16", "close": 99}],
            [{"date": "2026-10-19", "close": 90}],
        ):
            with self.subTest(history=history):
                report = sample_report()
                report["price_history"] = history
                self.assert_invalid(report)

    def test_paths_and_revisions_preserve_distinct_report_identity(self):
        for path in (
            "research/reports/002594.SZ/2026-10-19.v1.json",
            "research/reports/002594.SZ/2026-10-19.v2.json",
            "research/reports/GOOG.US/2026-10-19.json",
            "research/reports/002594.SZ/2026-10-20.json",
            "research/reports/../reports/002594.SZ/2026-10-19.json",
            "research/reports/002594.SZ/../../secret.json",
        ):
            with self.subTest(path=path):
                self.assert_invalid(sample_report(), path)
        first, second = sample_report(), sample_report()
        second.update(revision=2, generated_at="2026-10-20T08:01:00Z", headline="修订后的研究")
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root, [first, second])
            _, reports = load_dataset(root)
            self.assertEqual([report["revision"] for report in reports], [1, 2])
            self.assertEqual(reports[0]["headline"], first["headline"])
        for changes in ({"revision": 3}, {"generated_at": first["generated_at"]}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as root:
                bad = deepcopy(second)
                bad.update(changes)
                self.write_dataset(root, [first, bad])
                with self.assertRaises(ValidationError):
                    load_dataset(root)
        with tempfile.TemporaryDirectory() as root:
            self.write_dataset(root, [second])
            with self.assertRaises(ValidationError):
                load_dataset(root)

    def test_dataset_rejects_symlinks_unexpected_files_duplicate_keys_and_nan(self):
        with tempfile.TemporaryDirectory() as root:
            research = self.write_dataset(root)
            target = research / "reports" / "002594.SZ" / "2026-10-19.json"
            text = target.read_text(encoding="utf-8")
            for invalid in (text.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'), text.replace('"close": 100', '"close": NaN', 1)):
                target.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValidationError):
                    load_dataset(root)
            target.write_text(text, encoding="utf-8")
            secret = Path(root) / "outside.json"
            secret.write_text(text, encoding="utf-8")
            target.unlink()
            target.symlink_to(secret)
            with self.assertRaises(ValidationError):
                load_dataset(root)
        with tempfile.TemporaryDirectory() as root:
            research = self.write_dataset(root)
            (research / "reports" / "002594.SZ" / "notes.txt").write_text("must not publish", encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_dataset(root)

    def test_git_gate_rejects_overwrite_and_deletion_but_accepts_new_revision(self):
        with tempfile.TemporaryDirectory() as root:
            research = self.write_dataset(root)

            def git(*args):
                return subprocess.run(["git", "-C", root, *args], check=True, text=True, capture_output=True).stdout.strip()

            git("init", "-q")
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-q", "-m", "Synthetic validation fixture")
            base = git("rev-parse", "HEAD")
            report_path = research / "reports" / "002594.SZ" / "2026-10-19.json"
            original_text = report_path.read_text(encoding="utf-8")
            changed = sample_report()
            changed["headline"] = "不可覆盖原版"
            report_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValidationError):
                validate_append_only(root, base)
            report_path.write_text(original_text, encoding="utf-8")
            revised = sample_report()
            revised.update(revision=2, generated_at="2026-10-20T08:01:00Z", headline="新增修订")
            revision_path = report_path.with_name("2026-10-19.v2.json")
            revision_path.write_text(json.dumps(revised), encoding="utf-8")
            git("add", str(revision_path))
            validate_append_only(root, base)
            self.assertEqual(len(load_dataset(root)[1]), 2)
            report_path.unlink()
            with self.assertRaises(ValidationError):
                validate_append_only(root, base)
            with self.assertRaises(ValidationError):
                validate_append_only(root, "--arbitrary-git-option")


if __name__ == "__main__":
    unittest.main()
