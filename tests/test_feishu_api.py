"""Offline transport tests for publication safety; all tokens/data are synthetic."""

import base64
import contextlib
from email.parser import BytesParser
from email.policy import default
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from scripts.feishu_api import AmbiguousWriteError, FeishuAPIError, FeishuClient, _NoRedirect


TOKEN_URL = "https://accounts.feishu.cn/oauth/v3/token"
NODE = {"node_token": "node-fixture", "obj_token": "doc-fixture", "space_id": "space-fixture", "obj_type": "docx"}
BLOCK = {"block_id": "block-fixture", "block_type": 2}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5n8AAAAASUVORK5CYII=")


def auth(token="token-fixture", ttl=3600):
    return 200, {"code": 0, "access_token": token, "expires_in": ttl, "token_type": "Bearer"}


def ok(data):
    return 200, {"code": 0, "data": data}


def query(request):
    return parse_qs(urlsplit(request.full_url).query)


class Clock:
    def __init__(self):
        self.value = 1000.0
        self.sleeps = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        if seconds < 0:
            raise AssertionError("negative delay")
        self.sleeps.append(seconds)
        self.value += seconds


class Response(io.BytesIO):
    def __init__(self, status, payload, url):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        super().__init__(raw)
        self.status, self.url = status, url

    def geturl(self):
        return self.url


class Transport:
    def __init__(self, clock, events):
        self.clock, self.events, self.calls = clock, list(events), []

    def open(self, request, *, timeout):
        self.calls.append((request, self.clock.now(), timeout))
        if not self.events:
            raise AssertionError("unexpected HTTP call")
        event = self.events.pop(0)
        if callable(event):
            event = event(request)
        if isinstance(event, BaseException):
            raise event
        return Response(*event, request.full_url)

    @property
    def requests(self):
        return [call[0] for call in self.calls]

    @property
    def api_requests(self):
        return [request for request in self.requests if request.full_url != TOKEN_URL]

    @property
    def auth_requests(self):
        return [request for request in self.requests if request.full_url == TOKEN_URL]


class FeishuClientTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.clock_patch = patch("scripts.feishu_api.time.monotonic", self.clock.now)
        self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def client(self, *events):
        transport = Transport(self.clock, events)
        client = FeishuClient("cli-fixture", "secret + & fixture", opener=transport, sleep=self.clock.sleep)
        return client, transport

    def test_new_oauth_form_top_level_fields_and_large_jwt(self):
        large_jwt = "a" * 5000 + ".payload.signature"
        client, transport = self.client(auth(large_jwt), ok({"node": NODE}))
        self.assertEqual(client.get_node("wiki-fixture"), NODE)
        request = transport.auth_requests[0]
        self.assertEqual(request.full_url, TOKEN_URL)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(parse_qs(request.data.decode()), {
            "grant_type": ["client_credentials"], "client_id": ["cli-fixture"],
            "client_secret": ["secret + & fixture"],
        })
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(transport.api_requests[0].get_header("Authorization"), "Bearer " + large_jwt)
        self.assertEqual(query(transport.api_requests[0]), {"token": ["wiki-fixture"]})
        self.assertEqual(transport.events, [])

    def test_token_cached_then_refreshed_before_expiration(self):
        client, transport = self.client(auth("first", 10), ok({"node": NODE}), ok({"node": NODE}),
                                        auth("second", 10), ok({"node": NODE}))
        client.get_node("root")
        client.get_node("root")
        self.clock.value += 10
        client.get_node("root")
        self.assertEqual(len(transport.auth_requests), 2)
        self.assertEqual([r.get_header("Authorization") for r in transport.api_requests],
                         ["Bearer first", "Bearer first", "Bearer second"])

    def test_legacy_token_fields_and_malformed_expiry_are_rejected(self):
        payloads = [
            {"code": 0, "tenant_access_token": "old-token", "expire": 7200},
            {"code": 0, "data": {"access_token": "nested", "expires_in": 3600}},
            {"code": 0, "access_token": "token", "expires_in": True},
            {"code": 0, "access_token": "token", "expires_in": 0},
            {"code": 0, "access_token": "token\r\nInjected: value", "expires_in": 3600},
            {"code": 0, "access_token": "token\x00", "expires_in": 3600},
        ]
        for payload in payloads:
            with self.subTest(payload_fields=list(payload)):
                client, transport = self.client((200, payload))
                with self.assertRaises(FeishuAPIError):
                    client.get_node("root")
                self.assertEqual(len(transport.api_requests), 0)

    def test_auth_failure_refreshes_once_with_identical_append_key_and_body(self):
        client, transport = self.client(auth("first"), (400, {"code": 99991663}), auth("second"),
                                        ok({"children": [BLOCK]}))
        result = client.append_blocks("doc-fixture", [{"block_type": 2}], "batch-key")
        self.assertEqual(result, [BLOCK])
        first, second = transport.api_requests
        self.assertEqual(first.full_url, second.full_url)
        self.assertEqual(first.data, second.data)
        self.assertEqual(query(first)["client_token"], ["batch-key"])
        self.assertEqual(second.get_header("Authorization"), "Bearer second")

    def test_repeated_auth_error_does_not_refresh_forever_or_treat_user_code_as_tenant(self):
        client, transport = self.client(auth(), (401, {"code": 99991663}), auth(), (401, {"code": 99991663}))
        with self.assertRaises(FeishuAPIError):
            client.get_node("root")
        self.assertEqual(len(transport.auth_requests), 2)
        client, transport = self.client(auth(), (400, {"code": 99991668}))
        with self.assertRaises(FeishuAPIError):
            client.get_node("root")
        self.assertEqual(len(transport.auth_requests), 1)

    def test_business_rate_code_http_rate_and_server_failures_back_off(self):
        client, transport = self.client(auth(), (400, {"code": 99991400}), (429, b"busy"),
                                        (503, {"code": 1771005}), ok({"node": NODE}))
        self.assertEqual(client.get_node("root"), NODE)
        self.assertEqual(len(transport.api_requests), 4)
        times = [call[1] for call in transport.calls[1:]]
        self.assertEqual([round(b - a, 6) for a, b in zip(times, times[1:])], [1, 2, 4])

    def test_urllib_http_error_body_is_checked_and_safely_retried(self):
        def rate_error(request):
            return HTTPError(request.full_url, 429, "sensitive message", {}, io.BytesIO(b'{"code":99991400}'))
        client, transport = self.client(auth(), rate_error, ok({"node": NODE}))
        self.assertEqual(client.get_node("root"), NODE)
        self.assertEqual(len(transport.api_requests), 2)

    def test_create_node_request_and_uncertain_results_never_duplicate(self):
        client, transport = self.client(auth(), ok({"node": NODE}))
        self.assertEqual(client.create_node("space", "parent", "比亚迪 / 日报"), NODE)
        self.assertEqual(json.loads(transport.api_requests[0].data), {
            "parent_node_token": "parent", "obj_type": "docx", "node_type": "origin", "title": "比亚迪 / 日报",
        })
        for failure in (TimeoutError("secret URL"), (503, {"code": 1771005}),
                        (400, {"code": 131001}), (200, b"not JSON"), ok({"node": {}})):
            with self.subTest(failure_type=type(failure).__name__):
                client, transport = self.client(auth(), failure)
                with self.assertRaises(AmbiguousWriteError):
                    client.create_node("space", "parent", "day")
                self.assertEqual(len(transport.api_requests), 1)

    def test_create_without_idempotency_does_not_blindly_retry_rate_limit(self):
        client, transport = self.client(auth(), (400, {"code": 99991400}))
        with self.assertRaises(FeishuAPIError) as caught:
            client.create_node("space", "parent", "day")
        self.assertNotIsInstance(caught.exception, AmbiguousWriteError)
        self.assertEqual(caught.exception.reason, "rate_limited")
        self.assertEqual(len(transport.api_requests), 1)

    def test_append_retries_only_with_key_and_always_uses_document_root(self):
        client, transport = self.client(auth(), TimeoutError(), ok({"children": [BLOCK]}))
        client.append_blocks("doc-fixture", [{"block_type": 2}], "same-key")
        requests = transport.api_requests
        self.assertEqual(len(requests), 2)
        self.assertEqual(urlsplit(requests[0].full_url).path,
                         "/open-apis/docx/v1/documents/doc-fixture/blocks/doc-fixture/children")
        self.assertEqual(requests[0].data, requests[1].data)
        self.assertEqual(requests[0].full_url, requests[1].full_url)
        self.assertEqual(json.loads(requests[0].data)["index"], -1)
        client, transport = self.client(auth(), TimeoutError())
        with self.assertRaises(AmbiguousWriteError):
            client.append_blocks("doc", [{"block_type": 2}])
        self.assertEqual(len(transport.api_requests), 1)

    def test_retry_exhaustion_remains_ambiguous_for_idempotent_write(self):
        client, transport = self.client(auth(), *[(503, {"code": 1771005})] * 4)
        with self.assertRaises(AmbiguousWriteError):
            client.append_blocks("doc", [{"block_type": 2}], "stable-key")
        self.assertEqual(len(transport.api_requests), 4)
        self.assertEqual({query(r)["client_token"][0] for r in transport.api_requests}, {"stable-key"})

    def test_a_lost_write_is_still_ambiguous_if_later_retry_is_rejected(self):
        client, transport = self.client(auth(), TimeoutError(), *[(429, {"code": 99991400})] * 3)
        with self.assertRaises(AmbiguousWriteError):
            client.append_blocks("doc", [{"block_type": 2}], "stable-key")
        self.assertEqual(len(transport.api_requests), 4)
        client, transport = self.client(auth(), TimeoutError(), (401, {"code": 99991663}),
                                        (403, {"code": 20001, "error_description": "private details"}))
        with self.assertRaises(AmbiguousWriteError):
            client.append_blocks("doc", [{"block_type": 2}], "stable-key")
        self.assertEqual(len(transport.api_requests), 2)

    def test_expiration_during_backoff_does_not_change_batch_key(self):
        client, transport = self.client(auth("short", 2), (429, {"code": 99991400}),
                                        (429, {"code": 99991400}), auth("fresh"), ok({"children": [BLOCK]}))
        client.append_blocks("doc", [{"block_type": 2}], "persistent-key")
        self.assertEqual(len(transport.auth_requests), 2)
        self.assertEqual(transport.api_requests[-1].get_header("Authorization"), "Bearer fresh")
        self.assertEqual({query(r)["client_token"][0] for r in transport.api_requests}, {"persistent-key"})

    def test_wiki_pagination_continues_after_empty_permission_filtered_page(self):
        client, transport = self.client(auth(), ok({"items": [], "has_more": True, "page_token": "opaque/+?="}),
                                        ok({"items": [{"title": "A"}], "has_more": True, "page_token": "next"}),
                                        ok({"items": [{"title": "B"}], "has_more": False}))
        self.assertEqual(client.list_nodes("space", "parent"), [{"title": "A"}, {"title": "B"}])
        self.assertEqual(query(transport.api_requests[0]), {"parent_node_token": ["parent"], "page_size": ["50"]})
        self.assertEqual(query(transport.api_requests[1])["page_token"], ["opaque/+?="])

    def test_broken_pagination_fails_instead_of_truncating_or_looping(self):
        for pages in (
            [ok({"items": [], "has_more": True})],
            [ok({"items": [], "has_more": True, "page_token": "same"})] * 2,
            [ok({"items": [], "has_more": "true"})],
            [ok({"items": []})],
        ):
            with self.subTest(page_count=len(pages)):
                client, transport = self.client(auth(), *pages)
                with self.assertRaises(FeishuAPIError) as caught:
                    client.list_nodes("space", "parent")
                self.assertEqual(caught.exception.reason, "invalid_pagination")
                self.assertEqual(len(transport.api_requests), len(pages))

    def test_list_children_preserves_order_across_pages_and_reads_image_token(self):
        image = {"block_id": "image", "block_type": 27, "image": {"token": "media-token"}}
        client, transport = self.client(auth(), ok({"items": [BLOCK], "has_more": True, "page_token": "next"}),
                                        ok({"items": [image], "has_more": False}),
                                        ok({"items": [{"block_id": "root"}, BLOCK, image], "has_more": False}))
        self.assertEqual(client.list_children("doc"), [BLOCK, image])
        self.assertEqual(urlsplit(transport.api_requests[0].full_url).path,
                         "/open-apis/docx/v1/documents/doc/blocks/doc/children")
        self.assertEqual(query(transport.api_requests[0])["document_revision_id"], ["-1"])
        self.assertEqual(query(transport.api_requests[0])["with_descendants"], ["false"])
        self.assertEqual(len(client.list_blocks("doc")), 3)
        self.assertEqual(urlsplit(transport.api_requests[-1].full_url).path, "/open-apis/docx/v1/documents/doc/blocks")

    def test_delete_range_uses_one_idempotency_key_despite_lost_response(self):
        client, transport = self.client(auth(), TimeoutError(), ok({"document_revision_id": 2}))
        self.assertIsNone(client.delete_children("doc", 2, 5))
        first, second = transport.api_requests
        self.assertEqual(first.get_method(), "DELETE")
        self.assertTrue(urlsplit(first.full_url).path.endswith("/blocks/doc/children/batch_delete"))
        self.assertEqual(json.loads(first.data), {"start_index": 2, "end_index": 5})
        self.assertTrue(query(first)["client_token"][0])
        self.assertEqual(first.full_url, second.full_url)
        self.assertEqual(first.data, second.data)

    def test_multipart_upload_uses_docx_image_block_and_document_route(self):
        client, transport = self.client(auth(), ok({"file_token": "media-fixture"}))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-private-filename.png"
            path.write_bytes(PNG)
            self.assertEqual(client.upload_image("doc", "image-block", path), "media-fixture")
        request = transport.api_requests[0]
        self.assertEqual(request.full_url, "https://open.feishu.cn/open-apis/drive/v1/medias/upload_all")
        mime = BytesParser(policy=default).parsebytes(
            ("Content-Type: " + request.get_header("Content-type") + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + request.data
        )
        fields = {p.get_param("name", header="content-disposition"): p for p in mime.iter_parts()}
        self.assertEqual(fields["parent_type"].get_content(), "docx_image")
        self.assertEqual(fields["parent_node"].get_content(), "image-block")
        self.assertEqual(json.loads(fields["extra"].get_content()), {"drive_route_token": "doc"})
        self.assertEqual(int(fields["size"].get_content()), len(PNG))
        self.assertEqual(fields["file"].get_payload(decode=True), PNG)
        self.assertEqual(fields["file"].get_filename(), "trend.png")
        self.assertEqual(fields["file"].get_content_type(), "image/png")
        self.assertEqual(int(request.get_header("Content-length")), len(request.data))
        self.assertNotIn(b"local-private-filename", request.data)

    def test_media_upload_stops_on_uncertain_result(self):
        for failure in (TimeoutError(), (503, {"code": 1061001}), (200, {"code": 1061006}),
                        (200, {"code": 1061045}), ok({})):
            client, transport = self.client(auth(), failure)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "trend.png"
                path.write_bytes(PNG)
                with self.assertRaises(AmbiguousWriteError):
                    client.upload_image("doc", "image", path)
            self.assertEqual(len(transport.api_requests), 1)

    def test_replace_image_is_idempotent_across_auth_refresh_and_retry(self):
        client, transport = self.client(auth("old"), (400, {"code": 99991663}), auth("new"),
                                        (503, {"code": 1771005}), ok({"block": {"image": {"token": "media"}}}))
        self.assertIsNone(client.replace_image("doc", "image", "media"))
        requests = transport.api_requests
        self.assertEqual(len(requests), 3)
        self.assertEqual({r.full_url for r in requests}, {requests[0].full_url})
        self.assertEqual({r.data for r in requests}, {requests[0].data})
        self.assertEqual(requests[0].get_method(), "PATCH")
        self.assertEqual(json.loads(requests[0].data), {"replace_image": {"token": "media"}})
        self.assertTrue(query(requests[0])["client_token"][0])

    def test_http_status_and_business_code_are_both_required_and_errors_are_redacted(self):
        sensitive = "secret + & fixture; token-fixture; https://tenant.feishu.cn/wiki/private; raw body"
        for status, payload in ((403, {"code": 0, "data": {"node": NODE}}),
                                (200, {"code": 131006, "msg": sensitive}),
                                (400, {"code": sensitive, "msg": sensitive})):
            client, transport = self.client(auth(), (status, payload))
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                with self.assertRaises(FeishuAPIError) as caught:
                    client.get_node("private-node")
            rendered = str(caught.exception) + repr(caught.exception)
            for forbidden in (sensitive, "secret", "token-fixture", "private-node", "https://"):
                self.assertNotIn(forbidden, rendered)
            self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
            self.assertEqual(len(transport.api_requests), 1)

    def test_default_transport_blocks_redirects_without_contacting_destination(self):
        client = FeishuClient("fixture", "fixture")
        handlers = client._open.__self__.handlers
        redirect_handler = next(h for h in handlers if isinstance(h, _NoRedirect))
        request = Request("https://open.feishu.cn/open-apis/test", headers={"Authorization": "Bearer fixture"})
        for status in (301, 302, 303, 307, 308):
            response = io.BytesIO()
            with self.assertRaises(HTTPError) as caught:
                getattr(redirect_handler, f"http_error_{status}")(
                    request, response, status, "redirect", {"Location": "https://untrusted.invalid/"}
                )
            caught.exception.close()
        def redirect(request):
            return HTTPError(request.full_url, 302, "private redirect", {"Location": "https://untrusted.invalid/"}, io.BytesIO())
        client, transport = self.client(auth(), redirect)
        with self.assertRaises(FeishuAPIError) as caught:
            client.get_node("root")
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(len(transport.api_requests), 1)

    def test_destination_validation_and_bad_inputs_never_retrieve_credentials(self):
        client, transport = self.client()
        for url in ("http://open.feishu.cn/open-apis/test", "https://open.feishu.cn.evil.invalid/open-apis/test",
                    "https://accounts.feishu.cn/oauth/v3/token?unexpected=1", "https://user@open.feishu.cn/open-apis/test",
                    "https://open.feishu.cn:443/open-apis/test", "https://open.feishu.cn/open-apis/test#fragment"):
            with self.assertRaises(ValueError):
                client._transmit("GET", url)
        for call in (lambda: client.get_node("https://tenant.feishu.cn/wiki/root"),
                     lambda: client.list_children("../escape"),
                     lambda: client.append_blocks("doc", []),
                     lambda: client.append_blocks("doc", [{}] * 51),
                     lambda: client.append_blocks("doc", [{}], ""),
                     lambda: client.delete_children("doc", True, 3),
                     lambda: client.delete_children("doc", 3, 3)):
            with self.assertRaises(ValueError):
                call()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.png"
            path.write_bytes(b"not a PNG")
            with self.assertRaises(ValueError):
                client.upload_image("doc", "image", path)
        self.assertEqual(transport.requests, [])

    def test_all_requests_are_spaced_at_least_point_four_seconds(self):
        client, transport = self.client(auth(), ok({"node": NODE}), ok({"items": [], "has_more": False}),
                                        ok({"items": [], "has_more": False}), ok({"children": [BLOCK]}))
        client.get_node("root")
        client.list_children("doc-a")
        client.list_children("doc-b")
        client.append_blocks("doc-a", [{"block_type": 2}], "batch")
        times = [call[1] for call in transport.calls]
        self.assertTrue(all(b - a >= 0.4 - 1e-9 for a, b in zip(times, times[1:])))


if __name__ == "__main__":
    unittest.main()
