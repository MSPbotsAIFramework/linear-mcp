from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..linear_client import LinearClient
from ._common import NO_TOKEN, run

_QUERY_BY_TEAM = """
query ListLabelsByTeam($teamId: ID!) {
  issueLabels(filter: { team: { id: { eq: $teamId } } }) {
    nodes { id name color }
  }
}
"""

_QUERY_ALL = """
query ListLabels {
  issueLabels {
    nodes { id name color }
  }
}
"""


def register(mcp: FastMCP, client_factory: Callable[[], LinearClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def linear_list_labels(
        team_id: Annotated[
            str | None,
            Field(
                description=(
                    "Restrict to labels visible to this team. Omit for every "
                    "label visible to this API key."
                )
            ),
        ] = None,
    ) -> str:
        """List issue labels, optionally scoped to one team.

        Returns {labels: [{id, name, color}]}.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if team_id:
            data, error = await run(client, _QUERY_BY_TEAM, {"teamId": team_id})
        else:
            data, error = await run(client, _QUERY_ALL)
        if error:
            return error
        labels_conn = (data or {}).get("issueLabels") or {}
        return dump_json_capped({"labels": labels_conn.get("nodes", []) or []})
