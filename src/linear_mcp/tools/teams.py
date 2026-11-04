from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..linear_client import LinearClient
from ._common import NO_TOKEN, clamp_limit, run

_QUERY = """
query ListTeams($first: Int!, $after: String) {
  teams(first: $first, after: $after) {
    nodes { id key name }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def register(mcp: FastMCP, client_factory: Callable[[], LinearClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def linear_list_teams(
        limit: Annotated[
            int | None, Field(description="Max teams to return (1-50, default 25).")
        ] = None,
        cursor: Annotated[
            str | None, Field(description="Pagination cursor from a previous call's next_cursor.")
        ] = None,
    ) -> str:
        """List the teams visible to this API key.

        Returns {teams: [{id, key, name}], has_more, next_cursor}.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        data, error = await run(client, _QUERY, {"first": clamp_limit(limit), "after": cursor})
        if error:
            return error
        teams_conn = (data or {}).get("teams") or {}
        page_info = teams_conn.get("pageInfo") or {}
        return dump_json_capped(
            {
                "teams": teams_conn.get("nodes", []) or [],
                "has_more": bool(page_info.get("hasNextPage")),
                "next_cursor": page_info.get("endCursor"),
            }
        )
