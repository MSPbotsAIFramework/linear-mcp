import sys

from .config import get_settings
from .server import GatewayTokenMiddleware, create_mcp_server


def _build_http_app(mcp, settings):
    """Wrap the FastMCP Starlette app with a /health route and the gateway
    credential middleware. HTTP transport is always gateway mode — there is
    no header-optional variant, so the middleware is unconditional here.
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    mcp_app = mcp.streamable_http_app()  # Starlette app owning the session-manager lifespan
    mounted = GatewayTokenMiddleware(mcp_app, settings)

    # Mount() does NOT run a sub-app's lifespan, so the streamable-http session
    # manager's task group would never start ("Task group is not initialized").
    # Propagate the MCP app's lifespan to the outer app explicitly.
    return Starlette(
        routes=[Route("/health", health), Mount("/", app=mounted)],
        lifespan=lambda app: mcp_app.router.lifespan_context(app),
    )


def main() -> None:
    settings = get_settings()
    mcp = create_mcp_server(settings)

    if settings.mcp_transport == "http":
        import uvicorn

        app = _build_http_app(mcp, settings)

        print(
            f"Linear MCP server listening on "
            f"http://{settings.mcp_http_host}:{settings.mcp_http_port}/mcp",
            file=sys.stderr,
        )
        uvicorn.run(app, host=settings.mcp_http_host, port=settings.mcp_http_port)
    else:
        print("Linear MCP server running on stdio (local dev mode)", file=sys.stderr)
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
