from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..linear_client import LinearClient
from ._common import NO_TOKEN, run

_MUTATION = """
mutation CreateComment($input: CommentCreateInput!) {
  commentCreate(input: $input) {
    success
    comment { id }
  }
}
"""


def register(mcp: FastMCP, client_factory: Callable[[], LinearClient | None]) -> None:
    @mcp.tool()
    async def linear_create_comment(
        issue_id: Annotated[
            str | None, Field(description="Issue id or identifier to comment on (required).")
        ] = None,
        body: Annotated[
            str | None, Field(description="Comment body, Markdown supported (required).")
        ] = None,
    ) -> str:
        """Add a comment to an issue.

        Returns {id} for the created comment.
        """
        if not issue_id:
            return error_envelope("invalid_argument", "issue_id is required", False)
        if not body:
            return error_envelope("invalid_argument", "body is required", False)
        client = client_factory()
        if client is None:
            return NO_TOKEN
        data, error = await run(client, _MUTATION, {"input": {"issueId": issue_id, "body": body}})
        if error:
            return error
        result = (data or {}).get("commentCreate") or {}
        if not result.get("success"):
            return error_envelope(
                "upstream_error", "Linear reported commentCreate did not succeed", True
            )
        return dump_json_capped(result.get("comment") or {})
