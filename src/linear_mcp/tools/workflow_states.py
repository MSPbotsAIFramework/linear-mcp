from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..linear_client import LinearClient
from ._common import NO_TOKEN, run

_QUERY = """
query ListWorkflowStates($teamId: ID!) {
  workflowStates(filter: { team: { id: { eq: $teamId } } }) {
    nodes { id name type position }
  }
}
"""


def register(mcp: FastMCP, client_factory: Callable[[], LinearClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def linear_list_workflow_states(
        team_id: Annotated[
            str | None, Field(description="Team ID whose workflow states to list (required).")
        ] = None,
    ) -> str:
        """List the workflow states (e.g. Todo, In Progress, Done) for a team.

        Returns {states: [{id, name, type, position}]}.
        """
        if not team_id:
            return error_envelope("invalid_argument", "team_id is required", False)
        client = client_factory()
        if client is None:
            return NO_TOKEN
        data, error = await run(client, _QUERY, {"teamId": team_id})
        if error:
            return error
        states_conn = (data or {}).get("workflowStates") or {}
        return dump_json_capped({"states": states_conn.get("nodes", []) or []})
