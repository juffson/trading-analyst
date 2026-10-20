#!/usr/bin/env python3
"""Validate every byte of the public research dataset using Python's stdlib.

JSON Schema documents the structure; this module additionally checks semantic,
privacy, identity, and path constraints before anything is copied to the site.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
import ipaddress
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit
from zoneinfo import ZoneInfo


class ValidationError(ValueError):
    """A dataset is unsafe, inconsistent, or does not satisfy contract v1."""


OUTLOOKS = {"Strong Bullish", "Bullish", "Neutral", "Bearish", "Strong Bearish", "Unknown"}
MARKETS = {
    "CN": ("CNY", "Asia/Shanghai", time(15, 0)),
    "HK": ("HKD", "Asia/Hong_Kong", time(16, 0)),
    "US": ("USD", "America/New_York", time(16, 0)),
}
SYMBOL_PATTERN = re.compile(r"(?:[0-9]{6}\.(?:SZ|SH)|[0-9]{4,5}\.HK|[A-Z][A-Z0-9.-]{0,14}\.US)\Z")
SLUG_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
TIME_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})\Z")
REPORT_PATTERN = re.compile(r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})(?:\.v(?P<revision>[1-9][0-9]*))?\.json\Z")
SOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
REPORT_FIELDS = {
    "schema_version", "symbol", "trade_date", "revision", "data_as_of", "generated_at",
    "report_kind", "outlook", "analysis_quality", "headline", "summary", "market_snapshot",
    "indicators", "drivers", "price_levels", "scenarios", "sections", "comparison", "sources",
    "limitations", "price_history", "provenance",
}
SNAPSHOT_FIELDS = {"close", "previous_close", "change_pct", "open", "high", "low"}
LEVEL_NUMBERS = {"entry_low", "entry_high", "target", "stop", "take_profit_1", "take_profit_2"}
LEVEL_FIELDS = LEVEL_NUMBERS | {"target_type", "target_horizon", "entry_valid_until", "status", "trigger", "invalidation", "basis"}
PUBLIC_FIELD_NAMES = REPORT_FIELDS | SNAPSHOT_FIELDS | LEVEL_FIELDS | {
    "symbols", "name", "market", "currency", "slug", "value", "unit", "note", "title", "direction",
    "evidence", "source_ids", "price", "condition", "horizon", "heading", "body", "previous_trade_date",
    "narrative", "id", "label", "url", "published_at", "date", "kind", "source",
}
PRIVATE_DOMAINS = {
    "notion.so", "notion.site", "notion.com", "feishu.cn", "larksuite.com", "larkoffice.com",
    "docs.google.com", "drive.google.com", "sharepoint.com", "box.com", "dropbox.com",
    "1drv.ms", "onedrive.live.com",
}
SECRET_QUERY = re.compile(r"(?:token|secret|password|passwd|auth|api[-_]?key|session|signature|credential|x-amz-|x-goog-)", re.I)
HTML_PATTERN = re.compile(r"<\s*(?:/?\s*[A-Za-z][^>]*>|!|\?)|&lt;\s*/?\s*(?:script|img|svg|iframe|object|embed|style)\b", re.I)
URL_IN_TEXT = re.compile(r"(?:https?://|(?:javascript|data|file|vbscript)\s*:)[^\s<>\"'，。；）】]+", re.I)
PRIVATE_TEXT = [
    re.compile(r"(?:持仓(?:数量|成本|市值|比例|股数|金额|明细|盈亏|信息)|仓位(?:占比|比例|金额|成本)|(?:账户|账号|帐号)(?:资产|余额|资金|净值|编号|号码)|浮动盈亏|浮盈|浮亏|累计盈亏|持仓盈亏|成交记录|交割单|交易流水|实盘持仓|私人持仓|个人持仓|成本价)"),
    re.compile(r"(?:我(?:们|的)?|本人|本账户|个人账户)[^。\n!?！？]{0,40}(?:持仓|仓位|持有|买入|卖出|成本|盈亏|余额|资金)"),
    re.compile(r"(?:持仓|仓位|持有股票|个人账户)\s*[:：=]"),
    re.compile(r"\b(?:my|our|personal)\s+(?:positions?|holdings?|portfolio|account|trades?)\b", re.I),
    re.compile(r"\b(?:account\s+(?:balance|number|equity)|position\s+size|cost\s+basis|average\s+(?:cost|entry\s+price)|unrealized\s+(?:pnl|p&l|profit|loss)|broker(?:age)?\s+account)\b", re.I),
    re.compile(r"\bI\s+(?:own|hold|bought|sold)\s+.{0,40}\b(?:shares?|stocks?)\b", re.I),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?:手机号码|身份证号|银行卡号|护照号码)\s*[:：]?\s*[A-Za-z0-9 -]{5,}"),
]


def _fail(where: str, message: str) -> None:
    # Never echo the sensitive input value into Actions logs.
    raise ValidationError(f"{where}: {message}")


def _object(value: Any, keys: set[str], where: str) -> dict:
    if not isinstance(value, dict):
        _fail(where, "expected an object")
    missing = keys - value.keys()
    extra = value.keys() - keys
    if missing:
        _fail(where, "missing required fields: " + ", ".join(sorted(missing)))
    if extra:
        _fail(where, "contains unexpected fields (unapproved data is not publishable)")
    return value


def _array(value: Any, where: str) -> list:
    if not isinstance(value, list):
        _fail(where, "expected an array")
    if len(value) > 10000:
        _fail(where, "too many entries")
    return value


def _text(value: Any, where: str, nullable: bool = False, max_length: int = 30000) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        _fail(where, "expected nonempty text within the length limit")
    return value


def _integer(value: Any, where: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        _fail(where, "expected a positive integer")
    return value


def _number(value: Any, where: str, nullable: bool = True, positive: bool = False) -> float | int | None:
    if value is None and nullable:
        return None
    if type(value) not in (float, int):
        _fail(where, "expected a finite number" + (" or null" if nullable else ""))
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (positive and value <= 0):
        _fail(where, "number must be finite" + (" and positive" if positive else ""))
    return value


def _enum(value: Any, options: set[str], where: str, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if not isinstance(value, str) or value not in options:
        _fail(where, "unsupported value")


def _date(value: Any, where: str, nullable: bool = False) -> date | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        _fail(where, "expected YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(where, "invalid calendar date")


def _timestamp(value: Any, where: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not TIME_PATTERN.fullmatch(value):
        _fail(where, "expected an ISO 8601 timestamp with seconds and an explicit timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("missing timezone")
        if abs(parsed.utcoffset().total_seconds()) > 14 * 3600:
            raise ValueError("invalid timezone offset")
        return parsed
    except ValueError:
        _fail(where, "invalid timestamp")


def _public_url(value: Any, where: str) -> str:
    _text(value, where, max_length=4000)
    if any(ord(char) < 33 for char in value) or "\\" in value:
        _fail(where, "URL contains whitespace, control characters, or backslashes")
    decoded = unquote(value)
    if any(ord(char) < 32 for char in decoded):
        _fail(where, "URL contains encoded control characters")
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        _fail(where, "invalid URL")
    if "%" in parts.netloc:
        _fail(where, "percent-encoded URL authority is not allowed")
    if parts.scheme != "https" or not host or parts.username or parts.password or port not in (None, 443):
        _fail(where, "only public HTTPS URLs without credentials are allowed")
    if any(host == banned or host.endswith("." + banned) for banned in PRIVATE_DOMAINS):
        _fail(where, "private document/storage links cannot be published")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".test", ".invalid")):
        _fail(where, "internal network addresses cannot be published")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or all(part.isdigit() for part in host.split(".")):
            _fail(where, "expected a public hostname")
    else:
        if not address.is_global:
            _fail(where, "nonpublic network addresses cannot be published")
    if any(SECRET_QUERY.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        _fail(where, "URLs with credential or session parameters cannot be published")
    if re.search(r"(?:token|secret|password|auth|session)\s*=", unquote(parts.fragment), re.I):
        _fail(where, "URLs with credential fragments cannot be published")
    return value


def _scan_public_text(value: Any, where: str = "report") -> None:
    """Scan the full raw tree, including values the renderer may not display."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(where, "object keys must be text")
            label = key if key in PUBLIC_FIELD_NAMES else "unapproved_field"
            _scan_public_text(item, f"{where}.{label}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_public_text(item, f"{where}[{index}]")
    elif isinstance(value, str):
        if len(value) > 30000:
            _fail(where, "text exceeds public-report length limit")
        if any(ord(char) < 32 and char not in "\n\t\r" for char in value):
            _fail(where, "control characters are not allowed")
        if HTML_PATTERN.search(value):
            _fail(where, "HTML and active markup are not allowed; use plain text")
        compact = re.sub(r"[\s\x00-\x1f]+", "", value).lower()
        if any(protocol in compact for protocol in ("javascript:", "vbscript:", "data:text/html", "file://")):
            _fail(where, "active or local URL schemes are not allowed")
        if any(pattern.search(value) for pattern in PRIVATE_TEXT):
            _fail(where, "appears to contain private account, position, trade, or credential data")
        for match in URL_IN_TEXT.finditer(value):
            # Plain-text URLs may be wrapped in Markdown punctuation.
            _public_url(match.group().rstrip(".,;:!?)］]}"), where)


def validate_symbols(catalog: Any) -> list[dict]:
    _object(catalog, {"schema_version", "symbols"}, "symbols")
    if type(catalog["schema_version"]) is not int or catalog["schema_version"] != 1:
        _fail("symbols.schema_version", "only schema version 1 is supported")
    entries = _array(catalog["symbols"], "symbols.symbols")
    if not entries:
        _fail("symbols.symbols", "register at least one symbol")
    _scan_public_text(catalog, "symbols")
    codes, slugs = set(), set()
    for index, item in enumerate(entries):
        where = f"symbols.symbols[{index}]"
        _object(item, {"symbol", "name", "market", "currency", "slug"}, where)
        code = _text(item["symbol"], where + ".symbol", max_length=32)
        if not SYMBOL_PATTERN.fullmatch(code):
            _fail(where + ".symbol", "invalid supported symbol format")
        _text(item["name"], where + ".name", max_length=200)
        _enum(item["market"], set(MARKETS), where + ".market")
        expected_market = "CN" if code.endswith((".SZ", ".SH")) else "HK" if code.endswith(".HK") else "US"
        if item["market"] != expected_market or item["currency"] != MARKETS[expected_market][0]:
            _fail(where, "symbol, market, and currency do not agree")
        slug = _text(item["slug"], where + ".slug", max_length=100)
        if not SLUG_PATTERN.fullmatch(slug):
            _fail(where + ".slug", "slug must contain lowercase letters, digits, and single hyphens")
        if code in codes or slug in slugs:
            _fail(where, "duplicate symbol or slug")
        codes.add(code)
        slugs.add(slug)
    return entries


def _report_path(report: dict, source_path: str | Path) -> None:
    raw = str(source_path)
    if "\\" in raw or "\x00" in raw:
        _fail("report path", "invalid path characters")
    parts = PurePosixPath(raw).parts
    if ".." in parts or len(parts) < 3 or parts[-3] != "reports":
        _fail("report path", "expected reports/<symbol>/<date>[.vN].json")
    match = REPORT_PATTERN.fullmatch(parts[-1])
    if not match:
        _fail("report path", "invalid report filename")
    revision = int(match.group("revision") or "1")
    if match.group("revision") == "1":
        _fail("report path", "revision 1 must use the unsuffixed filename")
    if parts[-2] != report["symbol"] or match.group("date") != report["trade_date"] or revision != report["revision"]:
        _fail("report path", "file identity does not match report symbol, trade date, and revision")


def validate_report(report: Any, symbols_by_code: dict[str, dict], source_path: str | Path | None = None) -> dict:
    """Validate one report and return it unchanged; no file or network writes."""
    _object(report, REPORT_FIELDS, "report")
    _scan_public_text(report)
    if type(report["schema_version"]) is not int or report["schema_version"] != 1:
        _fail("report.schema_version", "only schema version 1 is supported")
    code = _text(report["symbol"], "report.symbol", max_length=32)
    if not SYMBOL_PATTERN.fullmatch(code) or code not in symbols_by_code:
        _fail("report.symbol", "symbol is invalid or unregistered")
    _integer(report["revision"], "report.revision")
    trade_date = _date(report["trade_date"], "report.trade_date")
    generated = _timestamp(report["generated_at"], "report.generated_at")
    as_of = _timestamp(report["data_as_of"], "report.data_as_of", nullable=True)
    market = symbols_by_code[code]["market"]
    _, zone, close_time = MARKETS[market]
    closing = datetime.combine(trade_date, close_time, tzinfo=ZoneInfo(zone))
    if trade_date.weekday() > 4:
        _fail("report.trade_date", "closing reports cannot use a weekend trade date")
    if generated < closing:
        _fail("report.generated_at", "closing analysis cannot be generated before the market's regular close")
    if as_of is not None and as_of > generated:
        _fail("report.data_as_of", "market-data cutoff cannot be later than generation time")
    _enum(report["report_kind"], {"closing"}, "report.report_kind")
    _enum(report["outlook"], OUTLOOKS, "report.outlook")
    _enum(report["analysis_quality"], {"pass", "degraded", "blocked"}, "report.analysis_quality")
    _text(report["headline"], "report.headline", max_length=300)
    _text(report["summary"], "report.summary")
    snapshot = _object(report["market_snapshot"], SNAPSHOT_FIELDS, "report.market_snapshot")
    for key, value in snapshot.items():
        _number(value, "report.market_snapshot." + key, positive=key != "change_pct")
    low, high = snapshot["low"], snapshot["high"]
    if low is not None and high is not None and low > high:
        _fail("report.market_snapshot", "low cannot exceed high")
    for key in ("close", "open"):
        value = snapshot[key]
        if value is not None and ((low is not None and value < low) or (high is not None and value > high)):
            _fail("report.market_snapshot." + key, "price must lie inside the day's range")
    if all(snapshot[key] is not None for key in ("close", "previous_close", "change_pct")):
        expected = (snapshot["close"] / snapshot["previous_close"] - 1) * 100
        if not math.isfinite(expected) or abs(snapshot["change_pct"] - expected) > 0.025:
            _fail("report.market_snapshot.change_pct", "percentage does not agree with close and previous_close")
    if snapshot["close"] is not None and (as_of is None or as_of < closing):
        _fail("report.data_as_of", "a closing price requires a cutoff at or after that market close")

    sources = _array(report["sources"], "report.sources")
    source_ids = set()
    for index, item in enumerate(sources):
        where = f"report.sources[{index}]"
        _object(item, {"id", "label", "url", "published_at"}, where)
        source_id = _text(item["id"], where + ".id", max_length=80)
        if not SOURCE_ID_PATTERN.fullmatch(source_id) or source_id in source_ids:
            _fail(where + ".id", "source ID must be valid and unique")
        source_ids.add(source_id)
        _text(item["label"], where + ".label", max_length=500)
        _public_url(item["url"], where + ".url")
        published = _timestamp(item["published_at"], where + ".published_at", nullable=True)
        if published is not None and published > generated:
            _fail(where + ".published_at", "source publication cannot be later than generation time")
    for index, item in enumerate(_array(report["indicators"], "report.indicators")):
        where = f"report.indicators[{index}]"
        _object(item, {"name", "value", "unit", "note"}, where)
        _text(item["name"], where + ".name", max_length=200)
        if isinstance(item["value"], str):
            _text(item["value"], where + ".value", max_length=2000)
        else:
            _number(item["value"], where + ".value")
        _text(item["unit"], where + ".unit", nullable=True, max_length=200)
        _text(item["note"], where + ".note", nullable=True)
    for index, item in enumerate(_array(report["drivers"], "report.drivers")):
        where = f"report.drivers[{index}]"
        _object(item, {"title", "direction", "evidence", "source_ids"}, where)
        _text(item["title"], where + ".title", max_length=500)
        _enum(item["direction"], {"positive", "negative", "mixed", "neutral", "unknown"}, where + ".direction")
        _text(item["evidence"], where + ".evidence")
        references = _array(item["source_ids"], where + ".source_ids")
        if any(not isinstance(ref, str) or ref not in source_ids for ref in references):
            _fail(where + ".source_ids", "source reference is missing from this report")
        if len(references) != len(set(references)):
            _fail(where + ".source_ids", "source references must be unique")

    levels = report["price_levels"]
    if levels is not None:
        _object(levels, LEVEL_FIELDS, "report.price_levels")
        for key in LEVEL_NUMBERS:
            _number(levels[key], "report.price_levels." + key, positive=True)
        _enum(levels["target_type"], {"technical", "fundamental", "model"}, "report.price_levels.target_type", nullable=True)
        _enum(levels["status"], {"active", "conditional", "expired", "unavailable"}, "report.price_levels.status")
        for key in ("target_horizon", "trigger", "invalidation", "basis"):
            _text(levels[key], "report.price_levels." + key, nullable=True)
        valid_until = _date(levels["entry_valid_until"], "report.price_levels.entry_valid_until", nullable=True)
        entry_low, entry_high = levels["entry_low"], levels["entry_high"]
        if (entry_low is None) != (entry_high is None) or (entry_low is not None and entry_low > entry_high):
            _fail("report.price_levels", "entry range requires both bounds in ascending order")
        if levels["target"] is not None and any(levels[key] is None for key in ("target_type", "target_horizon", "basis")):
            _fail("report.price_levels", "a target requires a type, horizon, and evidence basis")
        if levels["status"] == "unavailable" and any(levels[key] is not None for key in LEVEL_NUMBERS):
            _fail("report.price_levels", "unavailable levels cannot include numeric price levels")
        if entry_low is not None and levels["status"] in {"active", "conditional"}:
            if any(levels[key] is None for key in ("trigger", "invalidation", "entry_valid_until")):
                _fail("report.price_levels", "an active entry range needs a trigger, invalidation, and expiry")
            if valid_until < trade_date:
                _fail("report.price_levels.entry_valid_until", "active entry expiry cannot precede the trade date")
    scenarios = _array(report["scenarios"], "report.scenarios")
    for index, item in enumerate(scenarios):
        where = f"report.scenarios[{index}]"
        _object(item, {"name", "price", "condition", "horizon"}, where)
        _text(item["name"], where + ".name", max_length=300)
        _number(item["price"], where + ".price", positive=True)
        _text(item["condition"], where + ".condition")
        _text(item["horizon"], where + ".horizon", nullable=True)
    for index, item in enumerate(_array(report["sections"], "report.sections")):
        where = f"report.sections[{index}]"
        _object(item, {"heading", "body"}, where)
        _text(item["heading"], where + ".heading", max_length=500)
        _text(item["body"], where + ".body")
    comparison = report["comparison"]
    if comparison is not None:
        _object(comparison, {"previous_trade_date", "narrative"}, "report.comparison")
        previous = _date(comparison["previous_trade_date"], "report.comparison.previous_trade_date", nullable=True)
        if previous is not None and previous >= trade_date:
            _fail("report.comparison.previous_trade_date", "comparison date must precede this trade date")
        _text(comparison["narrative"], "report.comparison.narrative")
    limitations = _array(report["limitations"], "report.limitations")
    for index, item in enumerate(limitations):
        _text(item, f"report.limitations[{index}]")
    previous_history_date = None
    for index, item in enumerate(_array(report["price_history"], "report.price_history")):
        where = f"report.price_history[{index}]"
        _object(item, {"date", "close"}, where)
        history_date = _date(item["date"], where + ".date")
        _number(item["close"], where + ".close", nullable=False, positive=True)
        if history_date > trade_date or (previous_history_date is not None and history_date <= previous_history_date):
            _fail(where + ".date", "history dates must be unique, increasing, and no later than trade_date")
        if history_date == trade_date and snapshot["close"] is not None and not math.isclose(item["close"], snapshot["close"], rel_tol=1e-9, abs_tol=1e-8):
            _fail(where + ".close", "history close must agree with the same day's snapshot")
        previous_history_date = history_date
    provenance = _object(report["provenance"], {"kind", "source", "note"}, "report.provenance")
    _enum(provenance["kind"], {"fresh_analysis", "historical_backfill", "validation"}, "report.provenance.kind")
    _text(provenance["source"], "report.provenance.source", max_length=1000)
    _text(provenance["note"], "report.provenance.note")
    quality, outlook = report["analysis_quality"], report["outlook"]
    if quality in {"degraded", "blocked"} and not limitations:
        _fail("report.limitations", "degraded or blocked analysis must explain its limitations")
    if snapshot["close"] is None and (quality != "blocked" or outlook != "Unknown"):
        _fail("report.analysis_quality", "missing close requires blocked quality and Unknown outlook")
    if quality == "pass" and (not sources or outlook == "Unknown"):
        _fail("report.analysis_quality", "pass requires public evidence and a supported outlook")
    if quality == "blocked" and (outlook != "Unknown" or levels is not None or scenarios):
        _fail("report.analysis_quality", "blocked analysis must be Unknown and cannot publish price levels or scenarios")
    if provenance["kind"] == "validation" and (quality != "blocked" or outlook != "Unknown"):
        _fail("report.provenance.kind", "validation records cannot masquerade as research signals")
    if source_path is not None:
        _report_path(report, source_path)
    return report


def _read_json(path: Path) -> Any:
    if path.is_symlink():
        _fail(str(path), "symlinks are not accepted in the public dataset")
    if path.stat().st_size > 2_000_000:
        _fail(str(path), "report exceeds the 2 MB size limit")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                _fail(str(path), "duplicate JSON object keys")
            result[key] = value
        return result

    def reject_constant(_: str) -> None:
        _fail(str(path), "NaN and Infinity are not valid report values")

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        _fail(str(path), "invalid UTF-8 JSON")


def load_dataset(root: str | Path) -> tuple[list[dict], list[dict]]:
    """Load and validate research/ below repo root (or a research root itself)."""
    root = Path(root)
    research = root if (root / "symbols.json").is_file() else root / "research"
    if research.is_symlink() or not research.is_dir():
        _fail(str(research), "expected a real research directory")
    catalog_path = research / "symbols.json"
    if not catalog_path.is_file():
        _fail(str(catalog_path), "symbol catalog is missing")
    symbols = validate_symbols(_read_json(catalog_path))
    by_code = {symbol["symbol"]: symbol for symbol in symbols}
    reports_dir = research / "reports"
    if reports_dir.is_symlink() or not reports_dir.is_dir():
        _fail(str(reports_dir), "expected a real reports directory")
    reports, identities, groups = [], set(), {}
    for path in sorted(reports_dir.rglob("*")):
        if path.is_symlink():
            _fail(str(path), "symlinks are not accepted in the public dataset")
        relative = path.relative_to(reports_dir)
        if path.is_dir():
            if len(relative.parts) != 1 or path.name not in by_code:
                _fail(str(path), "only registered symbol directories are allowed")
            continue
        if len(relative.parts) != 2 or path.suffix != ".json":
            _fail(str(path), "only canonical report JSON files may appear below reports/")
        report = validate_report(_read_json(path), by_code, path)
        identity = (report["symbol"], report["trade_date"], report["revision"])
        if identity in identities:
            _fail(str(path), "duplicate report identity")
        identities.add(identity)
        groups.setdefault(identity[:2], []).append(report)
        reports.append(report)
    for versions in groups.values():
        versions.sort(key=lambda item: item["revision"])
        previous_generated = None
        for expected_revision, report in enumerate(versions, start=1):
            if report["revision"] != expected_revision:
                _fail("report revisions", "revisions must start at 1 and remain consecutive")
            generated = _timestamp(report["generated_at"], "report.generated_at")
            if previous_generated is not None and generated <= previous_generated:
                _fail("report revisions", "each revision must have a later generation timestamp")
            previous_generated = generated
    reports.sort(key=lambda item: (item["symbol"], item["trade_date"], item["revision"]))
    return symbols, reports


def validate_append_only(root: str | Path, base_ref: str) -> None:
    """Reject changes to existing report paths relative to a trusted commit SHA.

    This optional CI gate uses the Git executable already needed by checkout.
    New report/revision files are allowed; old reports cannot be edited, moved,
    replaced with symlinks, or deleted. It compares the working tree with base.
    """
    if not isinstance(base_ref, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", base_ref) or not base_ref.strip("0"):
        _fail("append-only gate", "base-ref must be a nonzero full commit SHA")
    root = Path(root).resolve()
    repo = root.parent if (root / "symbols.json").is_file() else root
    try:
        exists = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", base_ref + "^{commit}"],
            capture_output=True, check=False, timeout=30,
        )
        if exists.returncode != 0:
            _fail("append-only gate", "base commit is unavailable; fetch full history before validation")
        changes = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", "--no-renames", "--diff-filter=DMRTUXB", "-z", base_ref, "--", "research/reports"],
            capture_output=True, check=False, timeout=30,
        )
        if changes.returncode != 0:
            _fail("append-only gate", "could not compare report history with the base commit")
        if changes.stdout:
            _fail("append-only gate", "existing report paths were changed or deleted; add a new revision instead")
    except (OSError, subprocess.TimeoutExpired):
        _fail("append-only gate", "Git comparison failed or timed out")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="repository root or research directory")
    parser.add_argument("--base-ref", help="base commit SHA for the optional append-only history gate")
    args = parser.parse_args(argv)
    try:
        symbols, reports = load_dataset(args.root)
        if args.base_ref:
            validate_append_only(args.root, args.base_ref)
    except (ValidationError, OSError) as error:
        print(f"Research validation failed: {error}", file=sys.stderr)
        return 1
    suffix = " Append-only history verified." if args.base_ref else ""
    print(f"Research validation passed: {len(symbols)} symbols, {len(reports)} report revisions.{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
