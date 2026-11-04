import contextvars
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .linear_client import LinearClient

# ─────────────────────────────────────────────────────────────────────────────
# Per-request key contextvar for HTTP/gateway transport.
# GatewayTokenMiddleware sets this before the MCP handler runs.
# Python asyncio copies context per task, so concurrent requests are isolated
# (AC10 / TC12).
# ─────────────────────────────────────────────────────────────────────────────
_gateway_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "linear_gateway_token", default=None
)


def get_client_from_context(settings: Settings) -> LinearClient | None:
    """Resolve the active LinearClient for the current request.

    HTTP/gateway transport: the key comes ONLY from the per-request
    contextvar populated by GatewayTokenMiddleware, which itself 401s any
    request missing the X-Linear-Api-Key header. There is deliberately no
    fallback to an env-var key on this path — a request with no header must
    fail closed, never silently borrow another tenant's (or the operator's
    own) credentials (AC11 / TC13).

    stdio transport (local dev only, e.g. Claude Desktop): single process,
    single user, no gateway involved — the key comes from
    settings.linear_api_key (LINEAR_API_KEY).
    """
    if settings.mcp_transport == "http":
        key = _gateway_token_var.get()
    else:
        key = settings.linear_api_key

    if not key:
        return None
    return LinearClient(key, settings.linear_base_url)


class GatewayTokenMiddleware:
    """ASGI middleware for HTTP/gateway transport.

    Reads X-Linear-Api-Key from request headers and stores it in the
    contextvar. Returns 401 if the header is missing on /mcp requests. There
    is no other way for a request handled through this middleware to obtain
    a key — see get_client_from_context.
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not path.startswith("/mcp"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        token = request.headers.get("x-linear-api-key")
        if not token:
            response = JSONResponse(
                {
                    "error": "Missing credentials",
                    "message": "Gateway mode requires the X-Linear-Api-Key header",
                    "required_headers": ["X-Linear-Api-Key"],
                },
                status_code=401,
            )
            await response(scope, receive, send)
            return

        ctx_token = _gateway_token_var.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            _gateway_token_var.reset(ctx_token)


SERVICE_INSTRUCTIONS = (
    "Linear is a project/issue tracking tool. Tool categories: organizational "
    "structure (linear_list_teams, linear_list_workflow_states, "
    "linear_list_labels, linear_list_users, linear_list_projects) for "
    "browsing a workspace's teams, states, labels, members and projects; "
    "issues (linear_list_issues, linear_get_issue, linear_create_issue, "
    "linear_update_issue) for the core work-item read/write flow; comments "
    "(linear_create_comment) for discussion on an issue. Typical flow: "
    "linear_list_teams to find a team_id, linear_list_workflow_states or "
    "linear_list_labels to resolve state/label ids for that team, then the "
    "issue tools to read or act on work items. All list tools return "
    "has_more/next_cursor for pagination. No tool here deletes or archives "
    "anything — this connector only reads and writes/comments."
)


def create_mcp_server(settings: Settings) -> FastMCP:
    """Build the FastMCP server instance and register all tools."""
    # Container runs behind mcp-gateway on an internal network, which
    # forwards requests with a Host header that isn't localhost. The MCP
    # SDK's DNS-rebinding protection (a browser-oriented safeguard) would
    # reject that Host with 421 Misdirected Request, so disable it — the
    # container is never exposed publicly.
    mcp = FastMCP(
        name="linear-mcp",
        instructions=SERVICE_INSTRUCTIONS,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        stateless_http=True,
        json_response=True,
    )

    def client_factory() -> LinearClient | None:
        return get_client_from_context(settings)

    if not settings.has_credentials:
        # Graceful degradation: register only a diagnostic tool when no
        # credentials (stdio transport only — HTTP/gateway transport always
        # has_credentials=True).
        @mcp.tool()
        async def linear_test_connection() -> str:
            """Test Linear connection. Shows config requirements when credentials are missing."""
            return (
                "Error: Missing Linear credentials.\n\n"
                "Set the required environment variable:\n"
                "  LINEAR_API_KEY=lin_api_xxx\n\n"
                "Or run with MCP_TRANSPORT=http (the default) behind the gateway, "
                "sending header: X-Linear-Api-Key: lin_api_xxx"
            )

        print(
            "Warning: No Linear credentials found. Only the diagnostic tool is available.",
            file=sys.stderr,
        )
        return mcp

    from .tools import comments, issues, labels, projects, teams, users, workflow_states

    teams.register(mcp, client_factory)
    workflow_states.register(mcp, client_factory)
    labels.register(mcp, client_factory)
    users.register(mcp, client_factory)
    projects.register(mcp, client_factory)
    issues.register(mcp, client_factory)
    comments.register(mcp, client_factory)

    return mcp
