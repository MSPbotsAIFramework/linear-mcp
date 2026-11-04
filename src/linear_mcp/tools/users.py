from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..linear_client import LinearClient
from ._common import NO_TOKEN, clamp_limit, run

_QUERY = """
query ListUsers($first: Int!, $after: String) {
  users(first: $first, after: $after) {
    nodes { id name email active }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def register(mcp: FastMCP, client_factory: Callable[[], LinearClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def linear_list_users(
        limit: Annotated[
            int | None, Field(description="Max users to return (1-50, default 25).")
        ] = None,
        cursor: Annotated[
            str | None, Field(description="Pagination cursor from a previous call's next_cursor.")
        ] = None,
    ) -> str:
        """List the members of this Linear workspace.

        Returns {users: [{id, name, email, active}], has_more, next_cursor}.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        data, error = await run(client, _QUERY, {"first": clamp_limit(limit), "after": cursor})
        if error:
            return error
        users_conn = (data or {}).get("users") or {}
        page_info = users_conn.get("pageInfo") or {}
        return dump_json_capped(
            {
                "users": users_conn.get("nodes", []) or [],
                "has_more": bool(page_info.get("hasNextPage")),
                "next_cursor": page_info.get("endCursor"),
            }
        )
