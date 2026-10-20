"""Exercise recovery and publication ordering without any external service."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.feishu_api import AmbiguousWriteError
from scripts.feishu_render import completed_notice, image_placeholder, managed_notice
from scripts.publish_feishu import Config, PublishError, Publisher, canonical, main
from scripts.validate_research import load_dataset


ROOT = Path(__file__).resolve().parents[1]


def text(value):
    return {"block_type": 2, "text": {"elements": [{"text_run": {"content": value}}]}}


class FakeClient:
    def __init__(self):
        self.nodes = {"root": {"node_token": "root", "obj_token": "rootdoc", "obj_type": "docx", "node_type": "origin", "space_id": "space", "parent_node_token": "", "title": "User page"}}
        self.docs = {"rootdoc": [dict(text("原有入口正文"), block_id="original")]}
        self.writes = []
        self.serial = 0
        self.fail_create_after_commit = False
        self.fail_append_after_commit = False
        self.fail_delete_after_commit = False
        self.fail_image_upload = False

    def _id(self, prefix):
        self.serial += 1
        return prefix + str(self.serial)

    def get_node(self, token):
        return deepcopy(self.nodes[token])

    def list_nodes(self, space, parent):
        return deepcopy([node for node in self.nodes.values() if node["space_id"] == space and node["parent_node_token"] == parent])

    def create_node(self, space, parent, title):
        node = {"node_token": self._id("node"), "obj_token": self._id("doc"), "obj_type": "docx", "node_type": "origin", "space_id": space, "parent_node_token": parent, "title": title}
        self.nodes[node["node_token"]] = node
        self.docs[node["obj_token"]] = []
        self.writes.append(("create", parent, title))
        if self.fail_create_after_commit:
            self.fail_create_after_commit = False
            raise AmbiguousWriteError()
        return deepcopy(node)

    def list_children(self, document):
        return deepcopy(self.docs[document])

    def append_blocks(self, document, children, client_token=None):
        assert client_token is not None
        assert 1 <= len(children) <= 50
        batch = deepcopy(children)
        for block in batch:
            block["block_id"] = self._id("block")
            block["parent_id"] = document
        self.docs[document].extend(batch)
        self.writes.append(("append", document, len(batch)))
        if self.fail_append_after_commit:
            self.fail_append_after_commit = False
            raise RuntimeError("process interrupted after a committed append")
        return deepcopy(batch)

    def delete_children(self, document, start, end):
        self.writes.append(("delete", document, start, end))
        del self.docs[document][start:end]
        if self.fail_delete_after_commit:
            self.fail_delete_after_commit = False
            raise RuntimeError("process interrupted after a committed deletion")

    def upload_image(self, document, block, path):
        self.writes.append(("upload", document, block))
        if self.fail_image_upload:
            self.fail_image_upload = False
            raise RuntimeError("process interrupted during upload")
        return "filetoken"

    def replace_image(self, document, block, token):
        self.writes.append(("image", document, block))
        next(item for item in self.docs[document] if item["block_id"] == block)["image"]["token"] = token


class PublishRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.config = Config("test_app", "test_secret", "root", "https://example.feishu.cn")
        self.publisher = Publisher(self.client, self.config, sleep=lambda _: None)
        self.node = self.publisher.ensure_child("space", "root", "Auto")
        self.key = "symbol:QCOM.US"

    def wanted(self, count=3, prefix="body"):
        return [managed_notice(self.key)] + [text(prefix + str(i)) for i in range(count)]

    def assert_content(self, wanted):
        actual = self.client.list_children(self.node["obj_token"])
        self.assertEqual([canonical(b) for b in actual], [canonical(b) for b in wanted])

    def test_duplicate_run_is_zero_writes(self):
        wanted = self.wanted(64) + [completed_notice(self.key)]
        self.publisher.sync(self.node, self.key, wanted, immutable=True)
        self.client.writes.clear()
        self.publisher.sync(self.node, self.key, wanted, immutable=True)
        self.assertEqual(self.client.writes, [])
        self.assert_content(wanted)

    def test_lost_node_create_is_reconciled_without_duplicate(self):
        self.client.fail_create_after_commit = True
        node = self.publisher.ensure_child("space", "root", "Lost response")
        self.assertEqual(node["title"], "Lost response")
        self.assertEqual(sum(n["title"] == "Lost response" for n in self.client.nodes.values()), 1)
        self.assertEqual(sum(w[0] == "create" and w[2] == "Lost response" for w in self.client.writes), 1)

    def test_interrupted_report_append_resumes_prefix(self):
        wanted = self.wanted(110) + [completed_notice(self.key)]
        self.client.fail_append_after_commit = True
        with self.assertRaises(RuntimeError):
            self.publisher.sync(self.node, self.key, wanted, immutable=True)
        self.assertEqual(len(self.client.docs[self.node["obj_token"]]), 50)
        self.publisher.sync(self.node, self.key, wanted, immutable=True)
        self.assert_content(wanted)

    def test_existing_report_difference_fails_without_writing(self):
        self.publisher.sync(self.node, self.key, self.wanted() + [completed_notice(self.key)], immutable=True)
        self.client.writes.clear()
        with self.assertRaises(PublishError):
            self.publisher.sync(self.node, self.key, self.wanted(prefix="changed") + [completed_notice(self.key)], immutable=True)
        self.assertEqual(self.client.writes, [])

    def test_completed_report_is_not_treated_as_an_interrupted_prefix(self):
        wanted = self.wanted() + [completed_notice(self.key)]
        self.publisher.sync(self.node, self.key, wanted, immutable=True)
        self.client.writes.clear()
        with self.assertRaises(PublishError):
            self.publisher.sync(self.node, self.key, wanted + [text("new appended analysis")], immutable=True)
        self.assertEqual(self.client.writes, [])

    def test_default_empty_docx_paragraph_can_be_initialized(self):
        self.client.docs[self.node["obj_token"]] = [dict(text("\n"), block_id="initialempty")]
        wanted = self.wanted() + [completed_notice(self.key)]
        self.publisher.assert_managed_or_empty(self.node, self.key)
        self.publisher.sync(self.node, self.key, wanted, immutable=True)
        self.assert_content(wanted)

    def test_nested_content_in_blank_paragraph_is_not_deleted(self):
        self.client.docs[self.node["obj_token"]] = [dict(text(""), block_id="notempty", children=["nesteduserblock"])]
        self.client.writes.clear()
        with self.assertRaises(PublishError):
            self.publisher.sync(self.node, self.key, self.wanted())
        self.assertEqual(self.client.writes, [])

    def test_unmanaged_nonempty_page_is_never_taken_over(self):
        self.client.docs[self.node["obj_token"]] = [dict(text("用户手写正文"), block_id="user")]
        self.client.writes.clear()
        with self.assertRaises(PublishError):
            self.publisher.sync(self.node, self.key, self.wanted())
        self.assertEqual(self.client.writes, [])

    def test_new_generation_verifies_before_old_blocks_removed(self):
        old, new = self.wanted(70, "old"), self.wanted(90, "new")
        self.publisher.sync(self.node, self.key, old)
        self.client.writes.clear()
        self.client.fail_append_after_commit = True
        with self.assertRaises(RuntimeError):
            self.publisher.sync(self.node, self.key, new)
        self.assertFalse(any(w[0] == "delete" for w in self.client.writes))
        self.assertEqual([canonical(b) for b in self.client.docs[self.node["obj_token"]][:len(old)]], [canonical(b) for b in old])
        self.publisher.sync(self.node, self.key, new)
        self.assert_content(new)

    def test_backward_cleanup_survives_process_restart(self):
        self.publisher.sync(self.node, self.key, self.wanted(125, "old"))
        new = self.wanted(80, "new")
        self.client.fail_delete_after_commit = True
        with self.assertRaises(RuntimeError):
            self.publisher.sync(self.node, self.key, new)
        self.assertEqual(canonical(self.client.docs[self.node["obj_token"]][0]), canonical(managed_notice(self.key)))
        restarted = Publisher(self.client, self.config, sleep=lambda _: None)
        restarted.sync(self.node, self.key, new)
        self.assert_content(new)

    def test_newer_input_supersedes_incomplete_mutable_generation(self):
        self.publisher.sync(self.node, self.key, self.wanted(70, "v1"))
        self.client.fail_append_after_commit = True
        with self.assertRaises(RuntimeError):
            self.publisher.sync(self.node, self.key, self.wanted(80, "v2"))
        newest = self.wanted(91, "v3")
        self.publisher.sync(self.node, self.key, newest)
        self.assert_content(newest)

    def test_image_failure_keeps_previous_generation_and_recovers(self):
        old = self.wanted(3, "old")
        self.publisher.sync(self.node, self.key, old)
        new = [managed_notice(self.key), image_placeholder(), text("new")]
        with tempfile.TemporaryDirectory() as directory:
            png = Path(directory) / "test.png"
            png.write_bytes(b"fake image for transport stub")
            self.client.fail_image_upload = True
            with self.assertRaises(RuntimeError):
                self.publisher.sync(self.node, self.key, new, image=png)
            self.assertFalse(any(w[0] == "delete" for w in self.client.writes))
            self.publisher.sync(self.node, self.key, new, image=png)
        self.assert_content(new)
        self.assertTrue(self.client.docs[self.node["obj_token"]][1]["image"]["token"])

    def test_full_hierarchy_preserves_original_and_reruns_without_writes(self):
        symbols, reports = load_dataset(ROOT)
        symbols = [s for s in symbols if s["symbol"] in {"QCOM.US", "GOOG.US"}]
        reports = [r for r in reports if r["symbol"] == "QCOM.US"]
        original = deepcopy(self.client.docs["rootdoc"])
        with patch("scripts.publish_feishu.trend_png", return_value=False), patch("scripts.publish_feishu.overview_png", return_value=False):
            result = self.publisher.publish(symbols, reports)
            self.assertEqual(result.verified_reports, 1)
            self.client.writes.clear()
            Publisher(self.client, self.config, sleep=lambda _: None).publish(symbols, reports)
        self.assertEqual(self.client.writes, [])
        self.assertEqual(self.client.docs["rootdoc"], original)
        overview = next(n for n in self.client.nodes.values() if n["title"] == self.config.root_title)
        home = next(n for n in self.client.nodes.values() if n["title"].startswith("QCOM.US · "))
        report = next(n for n in self.client.nodes.values() if n["title"] == "2026-10-19 · v1")
        self.assertEqual(home["parent_node_token"], overview["node_token"])
        self.assertEqual(report["parent_node_token"], home["node_token"])

    def test_unmanaged_overview_blocks_descendant_creation(self):
        symbols, reports = load_dataset(ROOT)
        collision = self.client.create_node("space", "root", self.config.root_title)
        self.client.docs[collision["obj_token"]] = [dict(text("用户已有内容"), block_id="existing")]
        self.client.writes.clear()
        with self.assertRaises(PublishError):
            self.publisher.publish(symbols, reports)
        self.assertEqual(self.client.writes, [])

    def test_unmanaged_symbol_blocks_report_creation(self):
        symbols, reports = load_dataset(ROOT)
        symbols = [s for s in symbols if s["symbol"] == "QCOM.US"]
        reports = [r for r in reports if r["symbol"] == "QCOM.US"]
        overview = self.client.create_node("space", "root", self.config.root_title)
        collision = self.client.create_node("space", overview["node_token"], "QCOM.US · 高通")
        self.client.docs[collision["obj_token"]] = [dict(text("用户已有分析"), block_id="existing")]
        self.client.writes.clear()
        with self.assertRaises(PublishError):
            self.publisher.publish(symbols, reports)
        self.assertEqual(self.client.writes, [])

    def test_stale_local_dataset_does_not_roll_back_remote_archive(self):
        symbols, reports = load_dataset(ROOT)
        symbols = [s for s in symbols if s["symbol"] == "QCOM.US"]
        reports = [r for r in reports if r["symbol"] == "QCOM.US"]
        with patch("scripts.publish_feishu.trend_png", return_value=False), patch("scripts.publish_feishu.overview_png", return_value=False):
            self.publisher.publish(symbols, reports)
            self.client.writes.clear()
            with self.assertRaises(PublishError):
                Publisher(self.client, self.config, sleep=lambda _: None).publish(symbols, [])
        self.assertEqual(self.client.writes, [])

    def test_selected_publish_does_not_advertise_empty_other_home(self):
        symbols, reports = load_dataset(ROOT)
        symbols = [s for s in symbols if s["symbol"] in {"QCOM.US", "GOOG.US"}]
        reports = [r for r in reports if r["symbol"] == "QCOM.US"]
        overview = self.client.create_node("space", "root", self.config.root_title)
        goog = self.client.create_node("space", overview["node_token"], "GOOG.US · Alphabet")
        with patch("scripts.publish_feishu.trend_png", return_value=False), patch("scripts.publish_feishu.overview_png", return_value=False):
            self.publisher.publish(symbols, reports, selected="QCOM.US")
        self.assertEqual(self.client.docs[goog["obj_token"]], [])
        serialized = str([canonical(b) for b in self.client.docs[overview["obj_token"]]])
        self.assertNotIn(self.config.wiki_url(goog), serialized)

    def test_pages_configuration_and_display_name_change_only_mutable_pages(self):
        symbols, reports = load_dataset(ROOT)
        symbols = [s for s in symbols if s["symbol"] == "QCOM.US"]
        reports = [r for r in reports if r["symbol"] == "QCOM.US"]
        with patch("scripts.publish_feishu.trend_png", return_value=False), patch("scripts.publish_feishu.overview_png", return_value=False):
            self.publisher.publish(symbols, reports)
            report_node = next(n for n in self.client.nodes.values() if n["title"] == "2026-10-19 · v1")
            original_report = deepcopy(self.client.docs[report_node["obj_token"]])
            self.client.writes.clear()
            symbols = deepcopy(symbols)
            symbols[0]["name"] = "Qualcomm"
            config = Config("test_app", "test_secret", "root", "https://example.feishu.cn", pages_url="https://example.com/research")
            Publisher(self.client, config, sleep=lambda _: None).publish(symbols, reports)
        self.assertEqual(self.client.docs[report_node["obj_token"]], original_report)
        self.assertFalse(any(w[1] == report_node["obj_token"] for w in self.client.writes))


class ConfigurationTests(unittest.TestCase):
    def env(self):
        return {"FEISHU_APP_ID": "private_app", "FEISHU_APP_SECRET": "private_secret", "FEISHU_WIKI_ROOT_TOKEN": "https://example.feishu.cn/wiki/root"}

    def test_full_url_extracts_origin_and_does_not_leak_repr(self):
        config = Config.from_env(self.env())
        self.assertEqual(config.root_token, "root")
        self.assertEqual(config.wiki_origin, "https://example.feishu.cn")
        self.assertNotIn("private_secret", repr(config))
        self.assertNotIn("example.feishu.cn", repr(config))

    def test_raw_token_requires_tenant_and_origin_mismatch_fails(self):
        env = self.env()
        env["FEISHU_WIKI_ROOT_TOKEN"] = "root"
        with self.assertRaises(PublishError):
            Config.from_env(env)
        env["FEISHU_WIKI_BASE_URL"] = "https://example.feishu.cn"
        self.assertEqual(Config.from_env(env).root_token, "root")
        env["FEISHU_WIKI_ROOT_TOKEN"] = "https://different.feishu.cn/wiki/root"
        with self.assertRaises(PublishError):
            Config.from_env(env)

    def test_bad_origins_and_credentials_in_url_rejected(self):
        for root in ("http://example.feishu.cn/wiki/root", "https://example.feishu.cn.attacker.test/wiki/root", "https://secret@example.feishu.cn/wiki/root", "https://example.feishu.cn/wiki/root?token=private", "https://example.feishu.cn:8443/wiki/root"):
            env = self.env()
            env["FEISHU_WIKI_ROOT_TOKEN"] = root
            with self.subTest(root=root), self.assertRaises(PublishError):
                Config.from_env(env)

    def test_check_is_read_only(self):
        client = FakeClient()
        publisher = Publisher(client, Config.from_env(self.env()))
        publisher.check()
        self.assertEqual(client.writes, [])

    def test_validate_does_not_construct_network_client_or_load_secrets(self):
        with patch("scripts.publish_feishu.validate_rendering", return_value={"symbols": 9, "reports": 8, "blocks": 1, "charts": 0}), patch("scripts.publish_feishu.FeishuClient") as network, patch("scripts.publish_feishu.Config.from_env") as config, patch("scripts.publish_feishu._summary"):
            self.assertEqual(main(["--root", str(ROOT), "--mode", "validate"]), 0)
            network.assert_not_called()
            config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
