"""Gateway credential middleware tests: missing-header 401, header values
correctly reaching the per-request contextvar (no global-state leakage
across requests), proof that HTTP/gateway transport never falls back to an
env-var key when the header is absent (AC11/TC13), and a true concurrent,
HTTP-level isolation test for two different tenant keys (AC10/TC12).
"""

import asyncio
import json

import httpx
import pytest
import respx
from starlette.testclient import TestClient

from linear_mcp.__main__ import _build_http_app
from linear_mcp.config import Settings
from linear_mcp.linear_client import DEFAULT_BASE_URL
from linear_mcp.server import create_mcp_server, get_client_from_context


def _make_app(**overrides):
    settings = Settings(**overrides)
    mcp = create_mcp_server(settings)
    return _build_http_app(mcp, settings), settings


def test_health_is_local_and_does_not_require_credentials():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_missing_header_returns_401_with_required_headers_listed():
    app, _ = _make_app()
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert "X-Linear-Api-Key" in body["required_headers"]


def test_missing_header_401s_even_when_env_var_key_is_set():
    """TC13/AC11: HTTP/gateway transport must 401 on a missing header
    regardless of any env-var key configured on the process, because the
    middleware and header-only credential resolution are unconditional for
    HTTP transport (see server.py) — there is no code path that could fall
    back to an operator credential.
    """
    app, settings = _make_app(mcp_transport="http", linear_api_key="lin_api_env_leaked")
    assert settings.linear_api_key == "lin_api_env_leaked"  # sanity: env key IS configured
    with TestClient(app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401


def test_header_present_reaches_request_context():
    from linear_mcp.server import GatewayTokenMiddleware, _gateway_token_var

    settings = Settings()
    seen = {}

    async def fake_app(scope, receive, send):
        seen["token"] = _gateway_token_var.get()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = GatewayTokenMiddleware(fake_app, settings)

    async def run():
        scope = {
            "type": "http",
            "path": "/mcp",
            "headers": [(b"x-linear-api-key", b"test-key-123")],
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        sent = []

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)

    asyncio.run(run())
    assert seen["token"] == "test-key-123"
    # After the request completes, the contextvar must be reset — a fresh
    # get() outside any request context sees no leftover credential.
    assert _gateway_token_var.get() is None


def test_client_factory_returns_none_without_context():
    settings = Settings(mcp_transport="http")
    assert get_client_from_context(settings) is None


def test_client_factory_returns_none_even_with_env_key_in_http_mode():
    settings = Settings(mcp_transport="http", linear_api_key="lin_api_env_leaked")
    assert get_client_from_context(settings) is None


def test_client_factory_uses_env_key_in_stdio_mode():
    settings = Settings(mcp_transport="stdio", linear_api_key="lin_api_local_dev")
    client = get_client_from_context(settings)
    assert client is not None
    assert client._api_key == "lin_api_local_dev"


class _LifespanManager:
    """Minimal manual ASGI lifespan driver.

    httpx.ASGITransport does not run the app's lifespan on its own — the
    streamable-http session manager's task group is started in `lifespan.
    startup` (see server.py's `_build_http_app`: the MCP app's lifespan is
    propagated to the outer Starlette app for exactly this reason). Without
    driving lifespan.startup here first, every /mcp request fails with
    "Task group is not initialized."
    """

    def __init__(self, app):
        self.app = app
        self._startup_complete = asyncio.Event()
        self._shutdown_complete = asyncio.Event()
        self._receive_queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def _receive(self):
        return await self._receive_queue.get()

    async def _send(self, message):
        if message["type"] == "lifespan.startup.complete":
            self._startup_complete.set()
        elif message["type"] == "lifespan.shutdown.complete":
            self._shutdown_complete.set()

    async def __aenter__(self):
        self._task = asyncio.create_task(self.app({"type": "lifespan"}, self._receive, self._send))
        await self._receive_queue.put({"type": "lifespan.startup"})
        await self._startup_complete.wait()
        return self

    async def __aexit__(self, *exc):
        await self._receive_queue.put({"type": "lifespan.shutdown"})
        await self._shutdown_complete.wait()
        await self._task


async def _mcp_call(
    client: httpx.AsyncClient, api_key: str, tool: str, arguments: dict, req_id: int
):
    resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-Linear-Api-Key": api_key,
        },
    )
    return resp


def _parse_tool_result(resp: httpx.Response) -> dict:
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        # Streamable HTTP may respond as SSE even for json_response=True in
        # some SDK versions; the final "data:" line carries the JSON-RPC body.
        data_line = [line for line in resp.text.splitlines() if line.startswith("data:")][-1]
        payload = json.loads(data_line[len("data:") :].strip())
    else:
        payload = resp.json()
    block = payload["result"]["content"][0]
    return json.loads(block["text"])


@pytest.mark.asyncio
@respx.mock
async def test_concurrent_requests_with_different_keys_do_not_cross_contaminate():
    """AC10/TC12: two concurrent requests to the running HTTP server, each
    with its own X-Linear-Api-Key, must each see only their own (mocked)
    account's data — asserted against actual HTTP responses, not only
    against the middleware's unit-level contextvar behavior.
    """
    app, _ = _make_app(mcp_transport="http")

    def _route(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("authorization")
        if auth == "lin_api_tenant_a":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "teams": {
                            "nodes": [{"id": "a1", "key": "AAA", "name": "Tenant A Team"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                },
            )
        if auth == "lin_api_tenant_b":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "teams": {
                            "nodes": [{"id": "b1", "key": "BBB", "name": "Tenant B Team"}],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                },
            )
        return httpx.Response(401, json={"errors": [{"message": "bad key"}]})

    respx.post(DEFAULT_BASE_URL).mock(side_effect=_route)

    transport = httpx.ASGITransport(app=app)
    async with _LifespanManager(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp_a, resp_b = await asyncio.gather(
                _mcp_call(client, "lin_api_tenant_a", "linear_list_teams", {}, 1),
                _mcp_call(client, "lin_api_tenant_b", "linear_list_teams", {}, 2),
            )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    result_a = _parse_tool_result(resp_a)
    result_b = _parse_tool_result(resp_b)
    assert result_a["teams"] == [{"id": "a1", "key": "AAA", "name": "Tenant A Team"}]
    assert result_b["teams"] == [{"id": "b1", "key": "BBB", "name": "Tenant B Team"}]
