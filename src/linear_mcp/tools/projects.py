from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..linear_client import LinearClient
from ._common import NO_TOKEN, clamp_limit, run

_QUERY_BY_TEAM = """
query ListProjectsByTeam($first: Int!, $after: String, $teamId: ID!) {
  projects(first: $first, after: $after, filter: { accessibleTeams: { id: { eq: $teamId } } }) {
    nodes { id name state }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_QUERY_ALL = """
query ListProjects($first: Int!, $after: String) {
  projects(first: $first, after: $after) {
    nodes { id name state }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def register(mcp: FastMCP, client_factory: Callable[[], LinearClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def linear_list_projects(
        team_id: Annotated[
            str | None,
            Field(
                description=(
                    "Restrict to projects accessible to this team. Omit for "
                    "every project visible to this API key."
                )
            ),
        ] = None,
        limit: Annotated[
            int | None, Field(description="Max projects to return (1-50, default 25).")
        ] = None,
        cursor: Annotated[
            str | None, Field(description="Pagination cursor from a previous call's next_cursor.")
        ] = None,
    ) -> str:
        """List projects, optionally scoped to one team.

        Returns {projects: [{id, name, state}], has_more, next_cursor}.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        variables = {"first": clamp_limit(limit), "after": cursor}
        if team_id:
            variables["teamId"] = team_id
            data, error = await run(client, _QUERY_BY_TEAM, variables)
        else:
            data, error = await run(client, _QUERY_ALL, variables)
        if error:
            return error
        projects_conn = (data or {}).get("projects") or {}
        page_info = projects_conn.get("pageInfo") or {}
        return dump_json_capped(
            {
                "projects": projects_conn.get("nodes", []) or [],
                "has_more": bool(page_info.get("hasNextPage")),
                "next_cursor": page_info.get("endCursor"),
            }
        )
