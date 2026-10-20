"""Small, credential-safe Feishu REST client (Python 3.10+, standard library).

The production transport only contacts accounts.feishu.cn and open.feishu.cn,
disables redirects, and never logs request/response bodies or resource tokens.
Use one client per publisher; requests are serialized and spaced by >= 0.4 s.

Retries deliberately distinguish reads from writes. Append requires a caller's
client_token to retry. Delete and image replacement generate one UUID per call
and reuse it through retries/token refresh. Wiki creation and media upload have
no documented idempotency key: uncertain outcomes raise AmbiguousWriteError.
An upload may have left an unreferenced media object; do not blindly re-upload.
After a process restart, reconcile remote state before repeating any operation.

Official interface references:
https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/tenant_access_token
https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/create
https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/batch_delete
https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/patch
https://open.feishu.cn/document/server-docs/docs/drive-v1/media/upload_all
https://open.feishu.cn/document/docs/docs/faq
https://open.feishu.cn/document/faq/trouble-shooting/how-to-fix-the-99991672-error
"""

from __future__ import annotations

import http.client
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, ProxyHandler, Request, build_opener
import uuid


_TOKEN_URL = "https://accounts.feishu.cn/oauth/v3/token"
_API_ORIGIN = "https://open.feishu.cn"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,512}\Z")
_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# These errors can describe a failure after a write reached its upstream service.
_UNCERTAIN_CODES = {131001, 131007, 1061001, 1061006, 1061045, 1771001, 1771002, 1771003, 1771004, 1771005, 1771006}
_SAFE_REASONS = {
    "api_error", "authentication_error", "invalid_response", "invalid_pagination",
    "transport_error", "rate_limited", "ambiguous_write", "redirect_rejected",
}
# Never derive a log label by formatting a request path, URL, or query.
_RESOURCE_PART = r"([A-Za-z0-9_-]{1,512})"
_OPERATION_ROUTES = (
    ("GET", re.compile(r"/open-apis/wiki/v2/spaces/get_node"), "wiki.get_node"),
    ("GET", re.compile(r"/open-apis/wiki/v2/spaces/" + _RESOURCE_PART + r"/nodes"), "wiki.list_nodes"),
    ("POST", re.compile(r"/open-apis/wiki/v2/spaces/" + _RESOURCE_PART + r"/nodes"), "wiki.create_node"),
    ("GET", re.compile(r"/open-apis/docx/v1/documents/" + _RESOURCE_PART + r"/blocks"), "docx.list_blocks"),
    ("GET", re.compile(r"/open-apis/docx/v1/documents/" + _RESOURCE_PART + r"/blocks/" + _RESOURCE_PART + r"/children"), "docx.list_children"),
    ("POST", re.compile(r"/open-apis/docx/v1/documents/" + _RESOURCE_PART + r"/blocks/" + _RESOURCE_PART + r"/children"), "docx.append_blocks"),
    ("DELETE", re.compile(r"/open-apis/docx/v1/documents/" + _RESOURCE_PART + r"/blocks/" + _RESOURCE_PART + r"/children/batch_delete"), "docx.delete_children"),
    ("PATCH", re.compile(r"/open-apis/docx/v1/documents/" + _RESOURCE_PART + r"/blocks/" + _RESOURCE_PART), "docx.replace_image"),
    ("POST", re.compile(r"/open-apis/drive/v1/medias/upload_all"), "drive.upload_image"),
)
_OPERATIONS = frozenset(route[2] for route in _OPERATION_ROUTES) | {"unknown"}
_SCOPE = re.compile(r"(?:wiki|docx|docs|drive|board):[a-z][a-z0-9_]*(?:[.:][a-z0-9_]+)*\Z", re.ASCII)
_MAX_SCOPE_LENGTH = 128
_MAX_REQUIRED_SCOPES = 16
_MAX_PERMISSION_VIOLATIONS = 32


def _operation_context(method, path):
    for expected_method, pattern, operation in _OPERATION_ROUTES:
        if method == expected_method:
            match = pattern.fullmatch(path)
            if match:
                return operation, match.groups()
    return "unknown", ()


def _safe_scopes(values, protected=()):
    """Bounded public scope identifiers only, never arbitrary response strings."""
    if not isinstance(values, (list, tuple)):
        return []
    result = []
    # Real app/resource/access tokens are long; short test identifiers can also
    # be ordinary scope words and must not suppress an entire API namespace.
    protected = tuple(value for value in protected if isinstance(value, str) and len(value) >= 8)
    for value in values[:_MAX_PERMISSION_VIOLATIONS]:
        if (
            not isinstance(value, str) or not 1 <= len(value) <= _MAX_SCOPE_LENGTH
            or not _SCOPE.fullmatch(value) or any(secret in value for secret in protected)
        ):
            continue
        if value not in result:
            result.append(value)
        if len(result) == _MAX_REQUIRED_SCOPES:
            break
    return result


def _required_any_scope(payload, code, protected=()):
    if code != 99991672 or not isinstance(payload, dict):
        return []
    error = payload.get("error")
    violations = error.get("permission_violations") if isinstance(error, dict) else None
    if not isinstance(violations, list):
        return []
    candidates = []
    for violation in violations[:_MAX_PERMISSION_VIOLATIONS]:
        if not isinstance(violation, dict):
            continue
        if violation.get("type") == "action_scope_required":
            candidates.append(violation.get("subject"))
        elif "type" not in violation:
            # The older official schema has scope/url instead of type/subject.
            # Never inspect url, msg, helps, log_id, or other free-form fields.
            candidates.append(violation.get("scope"))
    return _safe_scopes(candidates, protected)


class FeishuError(RuntimeError):
    """Public base class; errors contain no remote message, URL, or identifiers."""


class FeishuAPIError(FeishuError):
    def __init__(self, reason="api_error", *, status=None, code=None, operation=None, required_any_scope=None):
        self.reason = reason if reason in _SAFE_REASONS else "api_error"
        self.status = status if type(status) is int else None
        self.code = code if type(code) is int else None
        self.operation = operation if isinstance(operation, str) and operation in _OPERATIONS else None
        self.required_any_scope = _safe_scopes(required_any_scope) if self.code == 99991672 else []
        message = (
            f"Feishu {self.reason} (HTTP {self.status if self.status is not None else 'unknown'}, "
            f"code {self.code if self.code is not None else 'unknown'})"
        )
        if self.operation:
            message += " operation=" + self.operation
        if self.code == 99991672:
            message += " required_any_scope=" + json.dumps(self.required_any_scope, separators=(",", ":"))
        super().__init__(message)


class AmbiguousWriteError(FeishuAPIError):
    """A write may have succeeded. Reconcile remote state before another write."""

    def __init__(self, *, status=None, code=None, operation=None, required_any_scope=None):
        super().__init__("ambiguous_write", status=status, code=code, operation=operation,
                         required_any_scope=required_any_scope)


class _TransportFailure(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    handler_order = 0

    def http_error_302(self, request, response, code, message, headers):
        # urllib would otherwise forward the Authorization header on redirects.
        raise HTTPError(request.full_url, code, "redirect disabled", headers, response)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("Invalid resource identifier")
    return value


def _json_bytes(value):
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Request content is not valid JSON") from None


def _code(payload):
    value = payload.get("code") if isinstance(payload, dict) else None
    return value if type(value) is int else None


class FeishuClient:
    """REST operations for an existing Wiki subtree.

    ``opener`` may be an urllib OpenerDirector or an in-memory fake exposing
    ``open(request, timeout=...)`` (a callable is also accepted). Fakes must not
    perform redirects. Real urllib openers receive the no-redirect handler.
    ``sleep`` is injectable; tests can pair it with a patched monotonic clock.
    """

    MIN_INTERVAL = 0.4
    MAX_ATTEMPTS = 4
    TIMEOUT = 30

    def __init__(self, app_id, app_secret, *, opener=None, sleep=None):
        if not isinstance(app_id, str) or not app_id.strip() or not isinstance(app_secret, str) or not app_secret:
            raise ValueError("Feishu application credentials are required")
        self._app_id = app_id
        self._app_secret = app_secret
        if opener is None:
            opener = build_opener(ProxyHandler({}), _NoRedirect())
        elif isinstance(opener, OpenerDirector):
            opener.add_handler(_NoRedirect())
        self._open = opener.open if hasattr(opener, "open") else opener
        if not callable(self._open):
            raise ValueError("Invalid HTTP transport")
        self._sleep = sleep if sleep is not None else time.sleep
        self._access_token = None
        self._expires_at = 0.0
        self._next_request_at = 0.0
        self._lock = threading.RLock()

    def _wait_for_slot(self):
        now = time.monotonic()
        if now < self._next_request_at:
            self._sleep(self._next_request_at - now)
        self._next_request_at = max(time.monotonic(), self._next_request_at) + self.MIN_INTERVAL

    def _backoff(self, attempt):
        self._sleep(min(2.0 ** attempt, 30.0))

    def _transmit(self, method, url, *, body=None, access_token=None, content_type=None):
        parts = urlsplit(url)
        allowed = (
            url == _TOKEN_URL
            or (parts.netloc == "open.feishu.cn" and parts.path.startswith("/open-apis/"))
        )
        if not allowed or parts.scheme != "https" or parts.fragment or parts.username or parts.password:
            raise ValueError("HTTP destination is not allowed")
        headers = {"Accept": "application/json", "User-Agent": "trading-analyst-feishu/1.0"}
        if access_token is not None:
            headers["Authorization"] = "Bearer " + access_token
        if content_type:
            headers["Content-Type"] = content_type
        if body is not None:
            headers["Content-Length"] = str(len(body))
        request = Request(url, data=body, headers=headers, method=method)
        self._wait_for_slot()
        try:
            response = self._open(request, timeout=self.TIMEOUT)
        except HTTPError as error:
            response = error
        except (URLError, OSError, http.client.HTTPException, ValueError):
            raise _TransportFailure() from None
        try:
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if type(status) is not int:
                raise _TransportFailure()
            if hasattr(response, "geturl") and response.geturl() != url:
                raise FeishuAPIError("redirect_rejected", status=status)
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                return status, None
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeError):
                payload = None
            return status, payload if isinstance(payload, dict) else None
        except (OSError, http.client.HTTPException, ValueError):
            raise _TransportFailure() from None
        finally:
            try:
                response.close()
            except (OSError, http.client.HTTPException):
                pass

    def _get_access_token(self):
        if self._access_token and self._expires_at > max(time.monotonic(), self._next_request_at):
            return self._access_token
        # OAuth v3 uses client_id/client_secret and top-level access_token/expires_in.
        # Never mix this client_secret_post form with HTTP Basic authentication.
        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": self._app_id,
            "client_secret": self._app_secret,
        }).encode("utf-8")
        for attempt in range(self.MAX_ATTEMPTS):
            started = time.monotonic()
            try:
                status, payload = self._transmit(
                    "POST", _TOKEN_URL, body=body, content_type="application/x-www-form-urlencoded"
                )
            except _TransportFailure:
                if attempt + 1 < self.MAX_ATTEMPTS:
                    self._backoff(attempt)
                    continue
                raise FeishuAPIError("transport_error") from None
            code = _code(payload)
            if 200 <= status < 300 and code == 0:
                token = payload.get("access_token")
                ttl = payload.get("expires_in")
                token_type = payload.get("token_type", "Bearer")
                if (
                    not isinstance(token, str) or not token or not token.isascii()
                    or any(ord(c) <= 32 or ord(c) == 127 for c in token)
                    or type(ttl) not in (int, float) or not math.isfinite(ttl) or ttl <= 0
                    or not isinstance(token_type, str) or token_type.lower() != "bearer"
                ):
                    raise FeishuAPIError("invalid_response", status=status, code=code)
                # Use request start, not response arrival, with a refresh safety margin.
                self._expires_at = started + ttl - min(60.0, ttl * 0.1)
                self._access_token = token
                return token
            if status == 429 or code == 99991400 or 500 <= status < 600:
                if attempt + 1 < self.MAX_ATTEMPTS:
                    self._backoff(attempt)
                    continue
            raise FeishuAPIError("authentication_error", status=status, code=code)
        raise FeishuAPIError("authentication_error")

    def _request(self, method, path, *, query=None, body=None, content_type="application/json; charset=utf-8"):
        query = dict(query or {})
        operation, resource_ids = _operation_context(method, path)
        url = _API_ORIGIN + path + ("?" + urlencode(query) if query else "")
        write = method != "GET"
        retry_safe = not write or bool(query.get("client_token"))
        refreshed = False
        may_have_applied = False
        with self._lock:
            for attempt in range(self.MAX_ATTEMPTS):
                try:
                    token = self._get_access_token()
                except FeishuAPIError as error:
                    if may_have_applied:
                        raise AmbiguousWriteError(status=error.status, code=error.code, operation=operation) from None
                    raise
                try:
                    status, payload = self._transmit(
                        method, url, body=body, access_token=token, content_type=content_type
                    )
                except _TransportFailure:
                    may_have_applied = may_have_applied or write
                    if retry_safe and attempt + 1 < self.MAX_ATTEMPTS:
                        self._backoff(attempt)
                        continue
                    if write:
                        raise AmbiguousWriteError(operation=operation) from None
                    raise FeishuAPIError("transport_error", operation=operation) from None
                code = _code(payload)
                details = {
                    "status": status, "code": code, "operation": operation,
                    "required_any_scope": _required_any_scope(
                        payload, code, (self._app_id, self._app_secret, token, *resource_ids, *query.values())
                    ),
                }
                if 200 <= status < 300 and code == 0:
                    data = payload.get("data")
                    if isinstance(data, dict):
                        return data
                    if write:
                        raise AmbiguousWriteError(**details)
                    raise FeishuAPIError("invalid_response", **details)
                if (status == 401 or code == 99991663) and not refreshed and attempt + 1 < self.MAX_ATTEMPTS:
                    # An explicit auth rejection cannot have applied the operation.
                    self._access_token = None
                    self._expires_at = 0.0
                    refreshed = True
                    continue
                rate_limited = status == 429 or code == 99991400
                uncertain = 500 <= status < 600 or code in _UNCERTAIN_CODES
                may_have_applied = may_have_applied or (write and uncertain)
                if rate_limited or 500 <= status < 600:
                    if retry_safe and attempt + 1 < self.MAX_ATTEMPTS:
                        self._backoff(attempt)
                        continue
                    if may_have_applied:
                        raise AmbiguousWriteError(**details)
                    raise FeishuAPIError("rate_limited" if rate_limited else "api_error", **details)
                if may_have_applied or (write and 200 <= status < 300 and code is None):
                    raise AmbiguousWriteError(**details)
                raise FeishuAPIError("api_error", **details)
        raise FeishuAPIError("api_error", operation=operation)

    def _pages(self, path, query):
        result, seen = [], set()
        query = dict(query)
        while True:
            data = self._request("GET", path, query=query)
            items = data.get("items", [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise FeishuAPIError("invalid_response", status=200, code=0)
            result.extend(items)
            has_more = data.get("has_more")
            if type(has_more) is not bool:
                raise FeishuAPIError("invalid_pagination", status=200, code=0)
            if not has_more:
                return result
            page_token = data.get("page_token")
            # Empty items do NOT mean the end: Wiki permission filtering can do this.
            if not isinstance(page_token, str) or not page_token or page_token in seen:
                raise FeishuAPIError("invalid_pagination", status=200, code=0)
            seen.add(page_token)
            query["page_token"] = page_token

    @staticmethod
    def _node(data, *, write=False):
        node = data.get("node")
        if not isinstance(node, dict) or not isinstance(node.get("node_token"), str) or not node["node_token"]:
            if write:
                raise AmbiguousWriteError(status=200, code=0)
            raise FeishuAPIError("invalid_response", status=200, code=0)
        if write and (not isinstance(node.get("obj_token"), str) or not node["obj_token"]):
            raise AmbiguousWriteError(status=200, code=0)
        return node

    def get_node(self, token):
        """Resolve a Wiki node token; its obj_token is the Docx document_id."""
        data = self._request("GET", "/open-apis/wiki/v2/spaces/get_node", query={"token": _identifier(token)})
        return self._node(data)

    def list_nodes(self, space_id, parent_node_token):
        return self._pages(f"/open-apis/wiki/v2/spaces/{_identifier(space_id)}/nodes", {
            "parent_node_token": _identifier(parent_node_token), "page_size": 50,
        })

    def create_node(self, space_id, parent_node_token, title):
        """Create a real Docx child; an uncertain outcome must be reconciled by title."""
        if not isinstance(title, str) or not title.strip():
            raise ValueError("A nonempty node title is required")
        path = f"/open-apis/wiki/v2/spaces/{_identifier(space_id)}/nodes"
        data = self._request("POST", path, body=_json_bytes({
            "parent_node_token": _identifier(parent_node_token),
            "obj_type": "docx", "node_type": "origin", "title": title,
        }))
        return self._node(data, write=True)

    def list_blocks(self, document_id):
        return self._pages(f"/open-apis/docx/v1/documents/{_identifier(document_id)}/blocks", {
            "page_size": 500, "document_revision_id": -1,
        })

    def list_children(self, document_id):
        """Return all ordered immediate root children, not the flattened block tree."""
        document_id = _identifier(document_id)
        return self._pages(f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children", {
            "page_size": 500, "document_revision_id": -1, "with_descendants": "false",
        })

    def append_blocks(self, document_id, children, client_token=None):
        """Append one batch of 1–50 root blocks; provide a stable batch UUID to retry."""
        document_id = _identifier(document_id)
        if not isinstance(children, list) or not 1 <= len(children) <= 50 or any(not isinstance(c, dict) for c in children):
            raise ValueError("A batch must contain 1 to 50 block objects")
        query = {"document_revision_id": -1}
        if client_token is not None:
            query["client_token"] = _identifier(client_token)
        data = self._request(
            "POST", f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
            query=query, body=_json_bytes({"children": children, "index": -1}),
        )
        created = data.get("children")
        if not isinstance(created, list) or len(created) != len(children) or any(
            not isinstance(c, dict) or not isinstance(c.get("block_id"), str) or not c["block_id"] for c in created
        ):
            raise AmbiguousWriteError(status=200, code=0)
        return created

    def delete_children(self, document_id, start_index, end_index):
        """Delete a left-closed/right-open root-child range with per-call idempotency."""
        document_id = _identifier(document_id)
        if type(start_index) is not int or type(end_index) is not int or not 0 <= start_index < end_index:
            raise ValueError("Invalid child deletion range")
        self._request(
            "DELETE", f"/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children/batch_delete",
            query={"document_revision_id": -1, "client_token": str(uuid.uuid4())},
            body=_json_bytes({"start_index": start_index, "end_index": end_index}),
        )

    def upload_image(self, document_id, block_id, png_path):
        """Upload PNG bytes for an existing image block; never retry uncertain uploads."""
        document_id, block_id = _identifier(document_id), _identifier(block_id)
        try:
            with Path(png_path).open("rb") as source:
                png = source.read(_MAX_IMAGE_BYTES + 1)
        except (OSError, TypeError, ValueError):
            raise ValueError("PNG file could not be read") from None
        if not png.startswith(_PNG_SIGNATURE) or len(png) > _MAX_IMAGE_BYTES:
            raise ValueError("A PNG image no larger than 20 MiB is required")
        boundary = "----FeishuResearch" + uuid.uuid4().hex
        fields = {
            "file_name": "trend.png", "parent_type": "docx_image", "parent_node": block_id,
            "size": str(len(png)), "extra": _json_bytes({"drive_route_token": document_id}).decode("utf-8"),
        }
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
        parts.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="trend.png"\r\nContent-Type: image/png\r\n\r\n'.encode("ascii"),
            png, f"\r\n--{boundary}--\r\n".encode("ascii"),
        ])
        data = self._request("POST", "/open-apis/drive/v1/medias/upload_all", body=b"".join(parts),
                             content_type=f"multipart/form-data; boundary={boundary}")
        file_token = data.get("file_token")
        if not isinstance(file_token, str) or not file_token:
            raise AmbiguousWriteError(status=200, code=0)
        return file_token

    def replace_image(self, document_id, block_id, file_token):
        """Set an uploaded image token, reusing the same idempotency key on retry."""
        document_id, block_id, file_token = _identifier(document_id), _identifier(block_id), _identifier(file_token)
        self._request(
            "PATCH", f"/open-apis/docx/v1/documents/{document_id}/blocks/{block_id}",
            query={"document_revision_id": -1, "client_token": str(uuid.uuid4())},
            body=_json_bytes({"replace_image": {"token": file_token}}),
        )
