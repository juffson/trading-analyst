#!/usr/bin/env python3
"""Publish validated research to a dedicated Feishu Wiki subtree.

No credentials, private Wiki URLs, or remote document identifiers are written to
the repository, public artifacts, or logs. Feishu itself is the durable state:
the publisher reconciles deterministic blocks, recovers partial appends, and
only updates pages carrying its exact management notice.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.parse import unquote, urlsplit
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.feishu_api import AmbiguousWriteError, FeishuAPIError, FeishuClient
from scripts.feishu_render import (
    completed_notice,
    image_placeholder,
    managed_notice,
    overview_png,
    render_overview,
    render_report,
    render_symbol,
    trend_png,
)
from scripts.validate_research import ValidationError, _public_url, load_dataset


class PublishError(ValueError):
    """Safe-to-log publishing error without private service values."""


IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
REPORT_TITLE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}) · v([1-9][0-9]*)\Z")
TEXT_KEYS = {
    2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
    6: "heading4", 7: "heading5", 8: "heading6", 9: "heading7",
    10: "heading8", 11: "heading9", 12: "bullet", 13: "ordered",
}


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PublishError("飞书返回了无效资源标识，发布已停止。")
    return value


def _origin(value: str) -> str:
    try:
        parts = urlsplit(value)
        host = (parts.hostname or "").lower()
        if (
            parts.scheme != "https" or parts.username or parts.password
            or parts.port not in (None, 443) or parts.path not in ("", "/")
            or parts.query or parts.fragment or "%" in parts.netloc
            or not host.endswith(".feishu.cn") or host == "open.feishu.cn"
            or not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.feishu\.cn", host)
        ):
            raise ValueError
    except ValueError:
        raise PublishError("FEISHU_WIKI_BASE_URL 必须是租户的 HTTPS 飞书域名，不含路径或参数。") from None
    return "https://" + host


@dataclass(frozen=True)
class Config:
    app_id: str = field(repr=False)
    app_secret: str = field(repr=False)
    root_token: str = field(repr=False)
    wiki_origin: str = field(repr=False)
    root_title: str = "复盘分析（自动发布）"
    pages_url: str | None = None

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        env = dict(os.environ) if env is None else env
        missing = [key for key in ("FEISHU_APP_ID", "FEISHU_APP_SECRET", "FEISHU_WIKI_ROOT_TOKEN") if not env.get(key, "").strip()]
        if missing:
            raise PublishError("缺少配置：" + ", ".join(missing))
        root = env["FEISHU_WIKI_ROOT_TOKEN"].strip()
        base = env.get("FEISHU_WIKI_BASE_URL", "").strip()
        if root.startswith("https://"):
            parts = urlsplit(root)
            if parts.query or parts.fragment or parts.username or parts.password:
                raise PublishError("Wiki 入口请使用不带查询参数或片段的完整链接。")
            match = re.fullmatch(r"/wiki/([A-Za-z0-9_-]{1,128})/?", parts.path)
            if not match:
                raise PublishError("FEISHU_WIKI_ROOT_TOKEN 的完整链接必须是 /wiki/<节点标识>。")
            detected_origin = _origin(parts.scheme + "://" + parts.netloc)
            if base and _origin(base) != detected_origin:
                raise PublishError("Wiki 入口域名与 FEISHU_WIKI_BASE_URL 不一致。")
            base, root = detected_origin, match.group(1)
        elif not base:
            raise PublishError("使用裸 Wiki Token 时还需设置 FEISHU_WIKI_BASE_URL；也可以直接填写完整 Wiki 入口链接。")
        if not IDENTIFIER.fullmatch(root):
            raise PublishError("FEISHU_WIKI_ROOT_TOKEN 格式无效。")
        base = _origin(base)
        title = env.get("FEISHU_ROOT_TITLE", "").strip() or "复盘分析（自动发布）"
        if len(title) > 100 or any(ord(c) < 32 for c in title):
            raise PublishError("FEISHU_ROOT_TITLE 应为 1–100 字的单行标题。")
        pages = env.get("RESEARCH_PAGES_URL", "").strip().rstrip("/") or None
        if pages:
            _public_url(pages, "RESEARCH_PAGES_URL")
            if urlsplit(pages).query or urlsplit(pages).fragment:
                raise PublishError("RESEARCH_PAGES_URL 应为站点首页，不含参数或片段。")
        return cls(env["FEISHU_APP_ID"].strip(), env["FEISHU_APP_SECRET"].strip(), root, base, title, pages)

    def wiki_url(self, node: dict) -> str:
        return self.wiki_origin + "/wiki/" + _identifier(node.get("node_token"))

    def page_url(self, symbol: dict | None = None, report: dict | None = None) -> str | None:
        if not self.pages_url:
            return None
        if symbol is None:
            return self.pages_url + "/"
        result = self.pages_url + "/symbols/" + symbol["slug"] + "/"
        if report is not None:
            result += report["trade_date"] + "/v" + str(report["revision"]) + ".html"
        return result


def canonical(block: dict) -> tuple:
    """Ignore server IDs/default styles, retain visible content and link targets."""
    if block.get("children"):
        raise PublishError("自动页面含非发布器生成的嵌套块，停止修改以保留现有内容。")
    kind = block.get("block_type")
    if kind == 27:
        return (27,)
    if kind == 22:
        return (22,)
    key = TEXT_KEYS.get(kind)
    if not key:
        raise PublishError("自动页面含不支持的文档块，停止修改以保留现有内容。")
    payload = block.get(key, {})
    runs: list[tuple] = []
    for element in payload.get("elements", []):
        run = element.get("text_run")
        if not isinstance(run, dict) or not isinstance(run.get("content"), str):
            raise PublishError("自动页面含无法核对的富文本内容，停止修改。")
        style = run.get("text_element_style", {})
        target = style.get("link", {}).get("url", "")
        signature = (unquote(target), bool(style.get("bold")), bool(style.get("italic")), bool(style.get("inline_code")))
        content = run["content"].replace("\r\n", "\n")
        if runs and runs[-1][1:] == signature:
            runs[-1] = (runs[-1][0] + content,) + signature
        else:
            runs.append((content,) + signature)
    return (kind, tuple(runs))


def _same(actual: list[dict], expected: list[dict]) -> bool:
    return len(actual) == len(expected) and all(canonical(a) == canonical(b) for a, b in zip(actual, expected))


def _prefix(actual: list[dict], expected: list[dict]) -> bool:
    return len(actual) <= len(expected) and _same(actual, expected[:len(actual)])


def _with_chart(blocks: list[dict], image: Path | None) -> list[dict]:
    result = deepcopy(blocks)
    if image is not None:
        result.insert(1, image_placeholder())
    return result


def _has_images(actual: list[dict]) -> bool:
    return all(block.get("image", {}).get("token") for block in actual if block.get("block_type") == 27)


def _blank_document(actual: list[dict]) -> bool:
    # Some Docx creation paths include an initial empty text paragraph. Only
    # whitespace-only leaf text blocks with no links may be treated as empty.
    return all(block.get("block_type") == 2 and not block.get("children")
               and all(not run[0].strip() and not run[1] for run in canonical(block)[1]) for block in actual)


def _node(node: dict, *, docx: bool = True) -> dict:
    for field in ("node_token", "obj_token", "space_id"):
        _identifier(node.get(field))
    if docx and (node.get("obj_type") != "docx" or node.get("node_type", "origin") != "origin"):
        raise PublishError("同名目标不是原始新版文档，发布已停止。")
    return node


@dataclass
class Stats:
    created_nodes: int = 0
    updated_documents: int = 0
    unchanged_documents: int = 0
    verified_reports: int = 0


DIAGNOSTIC_STAGES = {
    "unknown": "未知阶段",
    "dataset_validation": "研究数据校验",
    "render_validation": "离线渲染校验",
    "configuration": "配置校验",
    "entry_read": "入口读取",
    "overview_node_create": "总览节点查找／创建",
    "symbol_node_create": "标的节点查找／创建",
    "report_node_create": "报告节点查找／创建",
    "report_sync": "报告渲染／同步核对",
    "symbol_index_sync": "标的索引同步核对",
    "overview_index_sync": "总览索引同步核对",
}


def _failure_context(stage: str, stats: Stats | None = None) -> str:
    """Only fixed stages and confirmed integer counters may enter diagnostics."""
    if not isinstance(stage, str) or stage not in DIAGNOSTIC_STAGES:
        stage = "unknown"
    created = stats.created_nodes if stats is not None else 0
    verified = stats.verified_reports if stats is not None else 0
    created = created if type(created) is int and created >= 0 else 0
    verified = verified if type(verified) is int and verified >= 0 else 0
    return (f"stage={stage}（{DIAGNOSTIC_STAGES[stage]}）；"
            f"本次已确认 created_nodes={created}, verified_reports={verified}")


class Publisher:
    def __init__(self, client: FeishuClient, config: Config, *, sleep=None):
        import time
        self.client, self.config = client, config
        self.sleep = sleep or time.sleep
        self.stats = Stats()
        self.stage = "unknown"
        self._cache: dict[tuple[str, str], list[dict]] = {}

    def children(self, space: str, parent: str, *, refresh: bool = False) -> list[dict]:
        key = (space, parent)
        if refresh or key not in self._cache:
            self._cache[key] = self.client.list_nodes(space, parent)
        return self._cache[key]

    def find_child(self, space: str, parent: str, title: str, *, symbol: str | None = None, refresh: bool = False) -> dict | None:
        matches = [n for n in self.children(space, parent, refresh=refresh)
                   if n.get("title") == title or (symbol and str(n.get("title", "")).startswith(symbol + " · "))]
        if len(matches) > 1:
            raise PublishError("同一父页面下存在重复的自动发布节点，请先整理重复页面。")
        if not matches:
            return None
        node = _node(matches[0])
        if node.get("space_id") != space or node.get("parent_node_token") != parent:
            raise PublishError("已有节点不在预期知识空间或父页面下，发布已停止。")
        return node

    def ensure_child(self, space: str, parent: str, title: str, *, symbol: str | None = None) -> dict:
        found = self.find_child(space, parent, title, symbol=symbol)
        if found:
            return found
        try:
            created = _node(self.client.create_node(space, parent, title))
        except AmbiguousWriteError:
            # The server may have accepted the POST. Only READ to reconcile;
            # never blindly create a second node after a lost response.
            for delay in (1, 2, 4):
                self.sleep(delay)
                found = self.find_child(space, parent, title, symbol=symbol, refresh=True)
                if found:
                    return found
            raise PublishError("创建节点的结果暂时无法确认；本次已停止，稍后重跑会先查找已有节点。") from None
        if created.get("space_id") != space or created.get("parent_node_token") != parent:
            raise PublishError("新节点未落在预期父页面下，发布已停止。")
        self.stats.created_nodes += 1
        self._cache.pop((space, parent), None)
        return created

    def check(self) -> dict:
        self.stage = "entry_read"
        root = _node(self.client.get_node(self.config.root_token), docx=False)
        if root["node_token"] != self.config.root_token:
            raise PublishError("返回的 Wiki 入口与配置不一致。")
        self.children(root["space_id"], root["node_token"], refresh=True)
        return root

    def _append(self, document: str, actual: list[dict], wanted: list[dict]) -> list[dict]:
        for offset in range(len(actual), len(wanted), 50):
            batch = wanted[offset:offset + 50]
            created = self.client.append_blocks(document, batch, client_token=str(uuid.uuid4()))
            if not _same(created, batch):
                raise PublishError("飞书返回的新增文档块与预期不一致；保留现状等待重跑核对。")
            actual.extend(created)
        return actual

    def _finish_images(self, document: str, blocks: list[dict], image: Path | None) -> list[dict]:
        if image is None:
            return blocks
        for block in blocks:
            if block.get("block_type") == 27 and not block.get("image", {}).get("token"):
                block_id = _identifier(block.get("block_id"))
                token = self.client.upload_image(document, block_id, image)
                self.client.replace_image(document, block_id, token)
        return self.client.list_children(document)

    def complete(self, node: dict, wanted: list[dict]) -> bool:
        actual = self.client.list_children(node["obj_token"])
        return _same(actual, wanted) and _has_images(actual)

    def assert_managed_or_empty(self, node: dict, key: str) -> None:
        actual = self.client.list_children(node["obj_token"])
        if actual and not _blank_document(actual) and canonical(actual[0]) != canonical(managed_notice(key)):
            raise PublishError("同名页面已有非本发布器管理的内容，已停止且未创建其子页面。")

    def sync(self, node: dict, key: str, wanted: list[dict], *, image: Path | None = None, immutable: bool = False) -> None:
        document = node["obj_token"]
        notice = canonical(managed_notice(key))
        if not wanted or canonical(wanted[0]) != notice:
            raise PublishError("渲染器没有提供一致的自动管理标识。")
        actual = self.client.list_children(document)
        if actual and _blank_document(actual):
            # Remove only verified empty paragraphs before the first notice.
            # An interruption leaves either empty paragraphs or an empty doc,
            # both recognisable on restart; no nonempty user content is erased.
            while actual:
                start = max(0, len(actual) - 50)
                before = self.client.list_children(document)
                if before != actual:
                    raise PublishError("空白页面在初始化期间变化，停止写入。")
                self.client.delete_children(document, start, len(actual))
                remaining = self.client.list_children(document)
                if remaining != actual[:start]:
                    raise PublishError("空白段落初始化结果不一致，停止写入。")
                actual = remaining
        if actual and canonical(actual[0]) != notice:
            raise PublishError("同名页面已有非本发布器管理的内容，已停止且未接管页面。")
        if immutable:
            end = canonical(completed_notice(key))
            if canonical(wanted[-1]) != end or sum(canonical(block) == end for block in wanted) != 1:
                raise PublishError("日期报告缺少唯一的正文完成标记。")
            if any(canonical(block) == end for block in actual) and not _same(actual, wanted):
                raise PublishError("已归档报告与当前输入或渲染不一致；请检查原始数据及发布器版本，后续研究使用新的 JSON 修订。")
        if _same(actual, wanted) and _has_images(actual):
            self.stats.unchanged_documents += 1
            return

        old_count = 0
        if _prefix(actual, wanted):
            tail = self._append(document, actual, wanted)
        else:
            if immutable:
                raise PublishError("已有日期报告与当前输入或渲染不一致；保留原文，检查原始数据及发布器版本后再重试。")
            # A repeated management notice starts the new generation appended
            # by an interrupted prior update. Recover that suffix first.
            boundaries = [i for i, block in enumerate(actual) if canonical(block) == notice]
            if len(boundaries) > 1:
                old_count = boundaries[-1]
                pending = actual[old_count:]
                if not _prefix(pending, wanted):
                    # A newer dataset arrived after an interrupted append.
                    # Keep every existing block until this generation verifies.
                    old_count = len(actual)
                    pending = []
            else:
                old_count, pending = len(actual), []
            tail = self._append(document, pending, wanted)

        live = self._finish_images(document, tail, image)
        if image is None:
            live = self.client.list_children(document)
        # _finish_images returns the complete doc (including the old prefix).
        if len(live) != old_count + len(wanted) or not _same(live[old_count:], wanted) or not _has_images(live[old_count:]):
            raise PublishError("新内容回读核对未通过；旧内容已保留，下次运行可继续。")

        # Delete only the previously observed prefix, backwards in small
        # batches. Keeping its first notice until the final batch makes an
        # interrupted delete recognisable on the next run.
        while old_count:
            before_ids = [_identifier(block.get("block_id")) for block in live]
            check = self.client.list_children(document)
            if [_identifier(block.get("block_id")) for block in check] != before_ids:
                raise PublishError("发布期间页面结构发生变化，停止清理旧内容。")
            start = max(0, old_count - 50)
            self.client.delete_children(document, start, old_count)
            expected_ids = before_ids[:start] + before_ids[old_count:]
            live = self.client.list_children(document)
            if [_identifier(block.get("block_id")) for block in live] != expected_ids:
                raise PublishError("旧内容清理结果不一致，已停止等待下次核对。")
            old_count = start
        if not _same(live, wanted) or not _has_images(live):
            raise PublishError("发布最终核对未通过。")
        self.stats.updated_documents += 1

    def publish(self, symbols: list[dict], reports: list[dict], *, selected: str | None = None) -> Stats:
        parent = self.check()
        space = parent["space_id"]
        self.stage = "overview_node_create"
        overview = self.ensure_child(space, parent["node_token"], self.config.root_title)
        self.assert_managed_or_empty(overview, "overview")
        overview_url = self.config.wiki_url(overview)
        by_symbol = {s["symbol"]: sorted([r for r in reports if r["symbol"] == s["symbol"]], key=lambda r: (r["trade_date"], r["revision"])) for s in symbols}
        homes: dict[str, dict] = {}
        report_urls: dict[tuple[str, str, int], str] = {}
        verified: list[dict] = []

        with tempfile.TemporaryDirectory(prefix="feishu-research-") as temporary:
            work = Path(temporary)
            for symbol in symbols:
                code = symbol["symbol"]
                title = code + " · " + symbol["name"]
                active = selected is None or selected == code
                self.stage = "symbol_node_create"
                home = (self.ensure_child(space, overview["node_token"], title, symbol=code) if active else
                        self.find_child(space, overview["node_token"], title, symbol=code))
                if home is None:
                    continue
                self.assert_managed_or_empty(home, "symbol:" + code)
                homes[code] = home
                symbol_url = self.config.wiki_url(home)
                self.stage = "report_node_create"
                local_versions = {(r["trade_date"], r["revision"]) for r in by_symbol[code]}
                for child in self.children(space, home["node_token"]):
                    match = REPORT_TITLE.fullmatch(str(child.get("title", "")))
                    if match and (match.group(1), int(match.group(2))) not in local_versions:
                        raise PublishError("远端含本地缺失的日期或修订；请先更新到最新 main，发布不会回退历史目录。")
                for report in by_symbol[code]:
                    title = report["trade_date"] + " · v" + str(report["revision"])
                    self.stage = "report_node_create"
                    node = (self.ensure_child(space, home["node_token"], title) if active else
                            self.find_child(space, home["node_token"], title))
                    if node is None:
                        continue
                    self.stage = "report_sync"
                    chart = work / (symbol["slug"] + "-" + report["trade_date"] + "-v" + str(report["revision"]) + ".png")
                    picture = chart if trend_png(symbol, [report], chart) else None
                    wanted = _with_chart(render_report(symbol, report, symbol_url=symbol_url, overview_url=overview_url,
                                                      page_url=None), picture)
                    if active:
                        self.sync(node, report_key(report), wanted, image=picture, immutable=True)
                    elif not self.complete(node, wanted):
                        continue  # Never link an unfinished or divergent report.
                    key = (code, report["trade_date"], report["revision"])
                    report_urls[key] = self.config.wiki_url(node)
                    verified.append(report)
                    self.stats.verified_reports += 1

            # Report pages are complete before any directory advertises them.
            ready_homes: dict[str, dict] = {}
            for symbol in symbols:
                code = symbol["symbol"]
                if code not in homes:
                    continue
                self.stage = "symbol_index_sync"
                symbol_reports = [r for r in verified if r["symbol"] == code]
                urls = {(day, revision): url for (item, day, revision), url in report_urls.items() if item == code}
                chart = work / (symbol["slug"] + "-history.png")
                picture = chart if trend_png(symbol, symbol_reports, chart) else None
                wanted = _with_chart(render_symbol(symbol, symbol_reports, report_urls=urls, overview_url=overview_url,
                                                  page_url=self.config.page_url(symbol)), picture)
                if selected is None or code == selected:
                    self.sync(homes[code], "symbol:" + code, wanted, image=picture)
                    ready_homes[code] = homes[code]
                elif self.complete(homes[code], wanted):
                    ready_homes[code] = homes[code]

            self.stage = "overview_index_sync"
            chart = work / "overview.png"
            picture = chart if overview_png(symbols, verified, chart) else None
            wanted = _with_chart(render_overview(symbols, verified, symbol_urls={code: self.config.wiki_url(node) for code, node in ready_homes.items()},
                                                report_urls=report_urls, page_url=self.config.page_url()), picture)
            self.sync(overview, "overview", wanted, image=picture)
        return self.stats


def report_key(report: dict) -> str:
    return "report:" + report["symbol"] + ":" + report["trade_date"] + ":" + str(report["revision"])


def validate_rendering(symbols: list[dict], reports: list[dict], selected: str | None = None) -> dict:
    """Exercise every production renderer and PNG generation without network."""
    symbols = [s for s in symbols if selected is None or s["symbol"] == selected]
    reports = [r for r in reports if any(s["symbol"] == r["symbol"] for s in symbols)]
    symbol_urls = {s["symbol"]: "https://example.feishu.cn/wiki/preview" + s["slug"].replace("-", "") for s in symbols}
    report_urls = {(r["symbol"], r["trade_date"], r["revision"]): "https://example.feishu.cn/wiki/previewreport" + str(i) for i, r in enumerate(reports)}
    overview_url = "https://example.feishu.cn/wiki/previewoverview"
    blocks, images = 0, 0
    with tempfile.TemporaryDirectory(prefix="feishu-validate-") as directory:
        work = Path(directory)
        for symbol in symbols:
            group = [r for r in reports if r["symbol"] == symbol["symbol"]]
            for report in group:
                rendered = render_report(symbol, report, symbol_url=symbol_urls[symbol["symbol"]], overview_url=overview_url)
                if canonical(rendered[0]) != canonical(managed_notice(report_key(report))):
                    raise PublishError("报告渲染管理标识不一致。")
                if canonical(rendered[-1]) != canonical(completed_notice(report_key(report))):
                    raise PublishError("报告渲染完成标识不一致。")
                for block in rendered:
                    canonical(block)
                blocks += len(rendered)
                images += bool(trend_png(symbol, [report], work / "report.png"))
            rendered = render_symbol(symbol, group, report_urls={(day, rev): url for (code, day, rev), url in report_urls.items() if code == symbol["symbol"]}, overview_url=overview_url)
            for block in rendered:
                canonical(block)
            blocks += len(rendered)
            images += bool(trend_png(symbol, group, work / "symbol.png"))
        rendered = render_overview(symbols, reports, symbol_urls=symbol_urls, report_urls=report_urls)
        for block in rendered:
            canonical(block)
        blocks += len(rendered)
        images += bool(overview_png(symbols, reports, work / "overview.png"))
    return {"symbols": len(symbols), "reports": len(reports), "blocks": blocks, "charts": images}


def _summary(message: str) -> None:
    print(message)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with open(destination, "a", encoding="utf-8") as stream:
            stream.write(message + "\n\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--mode", choices=("validate", "check", "publish"), default="validate")
    parser.add_argument("--symbol", help="只发布一个已登记标的；默认全部。")
    args = parser.parse_args(argv)
    publisher = None
    stage = "dataset_validation"
    try:
        symbols, reports = load_dataset(args.root)
        if args.symbol and args.symbol not in {s["symbol"] for s in symbols}:
            raise PublishError("--symbol 必须是 research/symbols.json 中已登记的代码。")
        if args.mode == "validate":
            stage = "render_validation"
            result = validate_rendering(symbols, reports, args.symbol)
            _summary("飞书本地验证通过：{symbols} 个标的、{reports} 份分析、{blocks} 个正文块、{charts} 张趋势图；未调用飞书 API。".format(**result))
            return 0
        stage = "configuration"
        config = Config.from_env()
        publisher = Publisher(FeishuClient(config.app_id, config.app_secret), config)
        if args.mode == "check":
            publisher.check()
            _summary("飞书入口读取与子节点列表检查通过；未创建或修改页面。创建、编辑及图片权限仍需通过 publish 验证。")
            return 0
        result = publisher.publish(symbols, reports, selected=args.symbol)
        _summary(f"飞书发布并回读核对完成：新增 {result.created_nodes} 个节点，写入或更新 {result.updated_documents} 篇文档，跳过 {result.unchanged_documents} 篇相同文档，确认 {result.verified_reports} 份分析。")
        return 0
    except (PublishError, ValidationError, FeishuAPIError) as error:
        # These types deliberately contain only safe messages, not raw service
        # responses, Wiki URLs, request bodies, credentials, or document IDs.
        context = _failure_context(publisher.stage, publisher.stats) if publisher is not None else _failure_context(stage)
        _summary("飞书发布未完成：" + str(error) + "；" + context)
        return 1
    except Exception:
        # Never include repr/tracebacks with request objects or environment data.
        context = _failure_context(publisher.stage, publisher.stats) if publisher is not None else _failure_context(stage)
        _summary("飞书发布未完成：发生未预期错误，已停止；请先运行 validate 并检查依赖和配置。" + context)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
