from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..linear_client import LinearClient
from ._common import NO_TOKEN, clamp_limit, project_issue, run

_ISSUE_FIELDS = """
  id identifier title description
  state { id name type }
  assignee { id name }
  priority
  project { id name }
  team { id key }
  labels { nodes { id name } }
  url createdAt updatedAt
"""

_LIST_QUERY = f"""
query ListIssues($first: Int!, $after: String, $filter: IssueFilter) {{
  issues(first: $first, after: $after, filter: $filter) {{
    nodes {{ {_ISSUE_FIELDS} }}
    pageInfo {{ hasNextPage endCursor }}
  }}
}}
"""

_GET_QUERY = f"""
query GetIssue($id: String!) {{
  issue(id: $id) {{ {_ISSUE_FIELDS} }}
}}
"""

_CREATE_MUTATION = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier url }
  }
}
"""

_UPDATE_MUTATION = f"""
mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {{
  issueUpdate(id: $id, input: $input) {{
    success
    issue {{ {_ISSUE_FIELDS} }}
  }}
}}
"""

# Linear's own priority scale: 0 = No priority, 1 = Urgent, 2 = High, 3 = Medium, 4 = Low.
_VALID_PRIORITIES = {0, 1, 2, 3, 4}


def _build_issue_filter(
    team_id: str | None,
    project_id: str | None,
    assignee_id: str | None,
    state_id: str | None,
    label_id: str | None,
    query: str | None,
) -> dict | None:
    filter_: dict = {}
    if team_id:
        filter_["team"] = {"id": {"eq": team_id}}
    if project_id:
        filter_["project"] = {"id": {"eq": project_id}}
    if assignee_id:
        filter_["assignee"] = {"id": {"eq": assignee_id}}
    if state_id:
        filter_["state"] = {"id": {"eq": state_id}}
    if label_id:
        filter_["labels"] = {"id": {"eq": label_id}}
    if query:
        # Linear's GraphQL schema exposes a dedicated "searchableContent"
        # style contains-filter on title; this is the narrowest documented
        # approximation available without a separate full-text search
        # endpoint. See tech design "Data & API contract".
        filter_["title"] = {"containsIgnoreCase": query}
    return filter_ or None


def register(mcp: FastMCP, client_factory: Callable[[], LinearClient | None]) -> None:
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def linear_list_issues(
        team_id: Annotated[str | None, Field(description="Filter to issues in this team.")] = None,
        project_id: Annotated[
            str | None, Field(description="Filter to issues in this project.")
        ] = None,
        assignee_id: Annotated[
            str | None, Field(description="Filter to issues assigned to this user id.")
        ] = None,
        state_id: Annotated[
            str | None, Field(description="Filter to issues in this workflow state.")
        ] = None,
        label_id: Annotated[
            str | None, Field(description="Filter to issues carrying this label id.")
        ] = None,
        query: Annotated[
            str | None, Field(description="Free-text search against the issue title.")
        ] = None,
        limit: Annotated[
            int | None, Field(description="Max issues to return (1-50, default 25).")
        ] = None,
        cursor: Annotated[
            str | None, Field(description="Pagination cursor from a previous call's next_cursor.")
        ] = None,
    ) -> str:
        """List issues, filterable by team/project/assignee/state/label/title.

        Returns {issues: [...], has_more, next_cursor}. An empty result is
        a normal response (e.g. a team with zero issues), not an error.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        filter_ = _build_issue_filter(team_id, project_id, assignee_id, state_id, label_id, query)
        data, error = await run(
            client, _LIST_QUERY, {"first": clamp_limit(limit), "after": cursor, "filter": filter_}
        )
        if error:
            return error
        issues_conn = (data or {}).get("issues") or {}
        page_info = issues_conn.get("pageInfo") or {}
        return dump_json_capped(
            {
                "issues": [project_issue(i) for i in issues_conn.get("nodes", []) or []],
                "has_more": bool(page_info.get("hasNextPage")),
                "next_cursor": page_info.get("endCursor"),
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def linear_get_issue(
        issue_id: Annotated[
            str, Field(description="Issue id or human-readable identifier (e.g. ENG-123).")
        ],
    ) -> str:
        """Get a single issue by id or identifier, including its description.

        Returns a not_found error if the issue doesn't exist or isn't
        visible to this API key.
        """
        if not issue_id:
            return error_envelope("invalid_argument", "issue_id is required", False)
        client = client_factory()
        if client is None:
            return NO_TOKEN
        data, error = await run(client, _GET_QUERY, {"id": issue_id})
        if error:
            return error
        issue = (data or {}).get("issue")
        if not issue:
            return error_envelope(
                "not_found", f"Issue '{issue_id}' not found or not visible to this API key", False
            )
        return dump_json_capped(project_issue(issue))

    @mcp.tool()
    async def linear_create_issue(
        team_id: Annotated[
            str | None, Field(description="Team the new issue belongs to (required).")
        ] = None,
        title: Annotated[str | None, Field(description="Issue title (required).")] = None,
        description: Annotated[
            str | None, Field(description="Issue description (Markdown supported).")
        ] = None,
        assignee_id: Annotated[
            str | None, Field(description="User id to assign the issue to.")
        ] = None,
        project_id: Annotated[
            str | None, Field(description="Project to file the issue under.")
        ] = None,
        priority: Annotated[
            int | None, Field(description="0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low.")
        ] = None,
        label_ids: Annotated[list[str] | None, Field(description="Label ids to attach.")] = None,
    ) -> str:
        """Create a new issue in a team.

        Returns {id, identifier, url} for the created issue.
        """
        if not team_id:
            return error_envelope("invalid_argument", "team_id is required", False)
        if not title:
            return error_envelope("invalid_argument", "title is required", False)
        if priority is not None and priority not in _VALID_PRIORITIES:
            return error_envelope(
                "invalid_argument", f"priority must be one of {sorted(_VALID_PRIORITIES)}", False
            )
        client = client_factory()
        if client is None:
            return NO_TOKEN
        input_: dict = {"teamId": team_id, "title": title}
        if description is not None:
            input_["description"] = description
        if assignee_id is not None:
            input_["assigneeId"] = assignee_id
        if project_id is not None:
            input_["projectId"] = project_id
        if priority is not None:
            input_["priority"] = priority
        if label_ids is not None:
            input_["labelIds"] = label_ids
        data, error = await run(client, _CREATE_MUTATION, {"input": input_})
        if error:
            return error
        result = (data or {}).get("issueCreate") or {}
        if not result.get("success"):
            return error_envelope(
                "upstream_error", "Linear reported issueCreate did not succeed", True
            )
        return dump_json_capped(result.get("issue") or {})

    @mcp.tool(annotations=ToolAnnotations(idempotentHint=True))
    async def linear_update_issue(
        issue_id: Annotated[
            str | None, Field(description="Issue id or identifier to update (required).")
        ] = None,
        title: Annotated[str | None, Field(description="New title.")] = None,
        description: Annotated[str | None, Field(description="New description.")] = None,
        state_id: Annotated[str | None, Field(description="New workflow state id.")] = None,
        assignee_id: Annotated[str | None, Field(description="New assignee user id.")] = None,
        priority: Annotated[
            int | None, Field(description="0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low.")
        ] = None,
        label_ids: Annotated[
            list[str] | None, Field(description="Replace the issue's label ids with this set.")
        ] = None,
    ) -> str:
        """Update fields on an existing issue (partial update).

        Only the fields you pass are changed. Returns the updated issue.
        """
        if not issue_id:
            return error_envelope("invalid_argument", "issue_id is required", False)
        if priority is not None and priority not in _VALID_PRIORITIES:
            return error_envelope(
                "invalid_argument", f"priority must be one of {sorted(_VALID_PRIORITIES)}", False
            )
        client = client_factory()
        if client is None:
            return NO_TOKEN
        input_: dict = {}
        if title is not None:
            input_["title"] = title
        if description is not None:
            input_["description"] = description
        if state_id is not None:
            input_["stateId"] = state_id
        if assignee_id is not None:
            input_["assigneeId"] = assignee_id
        if priority is not None:
            input_["priority"] = priority
        if label_ids is not None:
            input_["labelIds"] = label_ids
        data, error = await run(client, _UPDATE_MUTATION, {"id": issue_id, "input": input_})
        if error:
            return error
        result = (data or {}).get("issueUpdate") or {}
        if not result.get("success"):
            return error_envelope(
                "upstream_error", "Linear reported issueUpdate did not succeed", True
            )
        return dump_json_capped(project_issue(result.get("issue") or {}))
