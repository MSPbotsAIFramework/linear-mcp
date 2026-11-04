"""tools/list snapshot + functional tests for each automated test case in
docs/tickets/PRD-21310.ac.json. Tool calls run via mcp.call_tool() with
mcp_transport="stdio" (so a plain settings.linear_api_key is used — this
bypasses the HTTP gateway middleware entirely, which is covered separately
in test_middleware.py) and Linear's GraphQL endpoint mocked with respx.
"""

import json

import httpx
import pytest
import respx

from linear_mcp.config import Settings
from linear_mcp.linear_client import DEFAULT_BASE_URL, LinearError
from linear_mcp.server import create_mcp_server

EXPECTED_REQUIRED: dict[str, set[str]] = {
    "linear_list_teams": set(),
    "linear_list_workflow_states": set(),
    "linear_list_labels": set(),
    "linear_list_users": set(),
    "linear_list_projects": set(),
    "linear_list_issues": set(),
    "linear_get_issue": {"issue_id"},
    "linear_create_issue": set(),
    "linear_update_issue": set(),
    "linear_create_comment": set(),
}

READ_ONLY_TOOLS = {
    "linear_list_teams",
    "linear_list_workflow_states",
    "linear_list_labels",
    "linear_list_users",
    "linear_list_projects",
    "linear_list_issues",
    "linear_get_issue",
}

IDEMPOTENT_TOOLS = {"linear_update_issue"}


async def _call(tool: str, **arguments):
    mcp = create_mcp_server(Settings(mcp_transport="stdio", linear_api_key="lin_api_test"))
    blocks, _structured = await mcp.call_tool(tool, arguments)
    return json.loads(blocks[0].text)


# ───────────────────────── structural / contract tests ─────────────────────


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings(mcp_transport="http"))
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_REQUIRED), f"unexpected tool set: {names}"
    assert len(tools) == 10


@pytest.mark.asyncio
async def test_every_tool_description_bounds():
    mcp = create_mcp_server(Settings(mcp_transport="http"))
    tools = await mcp.list_tools()
    for tool in tools:
        description = tool.description or ""
        assert len(description) <= 500, f"{tool.name}: description too long ({len(description)})"
        first_line = description.strip().splitlines()[0] if description.strip() else ""
        assert len(first_line) <= 100, f"{tool.name}: first line too long: {first_line!r}"


@pytest.mark.asyncio
async def test_read_only_tools_are_annotated():
    mcp = create_mcp_server(Settings(mcp_transport="http"))
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}
    for name in READ_ONLY_TOOLS:
        tool = by_name[name]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True, f"{name} should be readOnlyHint=True"


@pytest.mark.asyncio
async def test_idempotent_tools_are_annotated():
    mcp = create_mcp_server(Settings(mcp_transport="http"))
    tools = await mcp.list_tools()
    by_name = {t.name: t for t in tools}
    for name in IDEMPOTENT_TOOLS:
        tool = by_name[name]
        assert tool.annotations is not None
        assert tool.annotations.idempotentHint is True, f"{name} should be idempotentHint=True"


@pytest.mark.asyncio
async def test_no_destructive_tools_in_v1():
    """Design decision (docs/tickets/PRD-21310.md "Non-goals (v1)"): no
    delete/archive tools ship in the first version.
    """
    mcp = create_mcp_server(Settings(mcp_transport="http"))
    tools = await mcp.list_tools()
    for tool in tools:
        if tool.annotations is not None:
            assert not tool.annotations.destructiveHint, (
                f"{tool.name} must not be destructive in v1"
            )


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings(mcp_transport="http"))
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.parametrize(
    "status_code,expected_code,expected_retryable",
    [
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (400, "invalid_argument", False),
        (422, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ],
)
def test_status_classification(status_code, expected_code, expected_retryable):
    from linear_mcp.linear_client import _classify_status

    code, retryable = _classify_status(status_code)
    assert code == expected_code
    assert retryable is expected_retryable


def test_linear_error_envelope_shape():
    err = LinearError("not_found", "boom", False)
    envelope = json.loads(err.to_envelope())
    assert envelope == {"error": {"code": "not_found", "message": "boom", "retryable": False}}


@pytest.mark.asyncio
async def test_no_credentials_diagnostic_tool_in_stdio_mode_without_key():
    mcp = create_mcp_server(Settings(mcp_transport="stdio", linear_api_key=None))
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == {"linear_test_connection"}


# ───────────────────────── functional tests (ac.json TCs) ──────────────────


@pytest.mark.asyncio
@respx.mock
async def test_list_teams_returns_id_key_name():
    """TC2 (AC2)."""
    respx.post(DEFAULT_BASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "teams": {
                        "nodes": [{"id": "t1", "key": "ENG", "name": "Engineering"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        )
    )
    result = await _call("linear_list_teams")
    assert result["teams"] == [{"id": "t1", "key": "ENG", "name": "Engineering"}]
    assert result["has_more"] is False


@pytest.mark.asyncio
@respx.mock
async def test_list_teams_pagination_cursor_continues():
    """TC3 (AC2)."""
    route = respx.post(DEFAULT_BASE_URL)
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "data": {
                    "teams": {
                        "nodes": [{"id": "t1", "key": "ENG", "name": "Engineering"}],
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                    }
                }
            },
        ),
        httpx.Response(
            200,
            json={
                "data": {
                    "teams": {
                        "nodes": [{"id": "t2", "key": "OPS", "name": "Operations"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        ),
    ]
    first = await _call("linear_list_teams")
    assert first["has_more"] is True
    assert first["next_cursor"] == "cursor-1"
    second = await _call("linear_list_teams", cursor=first["next_cursor"])
    assert second["teams"] == [{"id": "t2", "key": "OPS", "name": "Operations"}]
    assert second["has_more"] is False
    sent_variables = json.loads(route.calls[1].request.content)["variables"]
    assert sent_variables["after"] == "cursor-1"


def _issue_node(**overrides):
    base = {
        "id": "issue-1",
        "identifier": "ENG-1",
        "title": "Fix the thing",
        "description": "details",
        "state": {"id": "s1", "name": "Todo", "type": "unstarted"},
        "assignee": {"id": "u1", "name": "Alice"},
        "priority": 2,
        "project": {"id": "p1", "name": "Q4"},
        "team": {"id": "t1", "key": "ENG"},
        "labels": {"nodes": [{"id": "l1", "name": "bug"}]},
        "url": "https://linear.app/issue/ENG-1",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-02T00:00:00Z",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
@respx.mock
async def test_list_issues_team_filter_scopes_results():
    """TC4 (AC3)."""
    route = respx.post(DEFAULT_BASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [_issue_node(team={"id": "t1", "key": "ENG"})],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        )
    )
    result = await _call("linear_list_issues", team_id="t1")
    assert all(issue["team"]["id"] == "t1" for issue in result["issues"])
    sent_variables = json.loads(route.calls[0].request.content)["variables"]
    assert sent_variables["filter"] == {"team": {"id": {"eq": "t1"}}}


@pytest.mark.asyncio
@respx.mock
async def test_get_issue_unknown_identifier_returns_not_found_envelope():
    """TC6 (AC5)."""
    respx.post(DEFAULT_BASE_URL).mock(
        return_value=httpx.Response(200, json={"data": {"issue": None}})
    )
    result = await _call("linear_get_issue", issue_id="ENG-9999")
    assert result["error"]["code"] == "not_found"
    assert result["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_create_issue_missing_team_id_returns_invalid_argument():
    """TC8 (AC6) — validated before any HTTP call, so no respx mock needed;
    a stray outbound call with no mock registered would raise and fail the test.
    """
    result = await _call("linear_create_issue", title="New issue")
    assert result["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_retries_then_succeeds_after_retry_after():
    """TC11 (AC9), retry-then-succeed half."""
    route = respx.post(DEFAULT_BASE_URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, json={"error": "rate limited"}),
        httpx.Response(
            200,
            json={
                "data": {
                    "teams": {
                        "nodes": [{"id": "t1", "key": "ENG", "name": "Engineering"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        ),
    ]
    result = await _call("linear_list_teams")
    assert "error" not in result
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_rate_limit_exhausted_returns_rate_limited_envelope():
    """TC11 (AC9), retry-exhausted half."""
    respx.post(DEFAULT_BASE_URL).mock(
        return_value=httpx.Response(
            429, headers={"Retry-After": "0"}, json={"error": "rate limited"}
        )
    )
    result = await _call("linear_list_teams")
    assert result["error"]["code"] == "rate_limited"
    assert result["error"]["retryable"] is True


@pytest.mark.asyncio
@respx.mock
async def test_no_env_fallback_for_http_transport():
    """TC13 (AC11), tool-level restatement of the middleware-level test in
    test_middleware.py — client_factory() is None for HTTP transport with no
    request-context key set, regardless of any env-configured key.
    """
    from linear_mcp.server import get_client_from_context

    settings = Settings(mcp_transport="http", linear_api_key="lin_api_should_never_be_used")
    assert get_client_from_context(settings) is None


@pytest.mark.asyncio
@respx.mock
async def test_list_issues_empty_team_returns_empty_array_not_error():
    """TC14 (AC3, edge/empty)."""
    respx.post(DEFAULT_BASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
                }
            },
        )
    )
    result = await _call("linear_list_issues", team_id="empty-team")
    assert result == {"issues": [], "has_more": False, "next_cursor": None}


@pytest.mark.asyncio
async def test_update_issue_invalid_priority_returns_invalid_argument():
    """TC15 (AC7, edge/invalid enum) — validated before any HTTP call."""
    result = await _call("linear_update_issue", issue_id="ENG-1", priority=99)
    assert result["error"]["code"] == "invalid_argument"
