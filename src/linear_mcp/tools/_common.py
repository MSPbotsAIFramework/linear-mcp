from .._json import error_envelope
from ..linear_client import LinearClient, LinearError

NO_TOKEN = error_envelope(
    "not_configured", "No Linear API key. Send the X-Linear-Api-Key header.", False
)

# Matches Linear's own default page size (50) while keeping typical
# responses smaller, per the SOP's token-economy guidance (default <= 50,
# hard cap <= 200 — this connector's design picks 50 as its own hard cap,
# see docs/tickets/PRD-21310.md "Decisions made by DEV01").
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 50


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_PAGE_SIZE
    return max(1, min(limit, MAX_PAGE_SIZE))


def project_issue(issue: dict) -> dict:
    """Reduce a Linear issue node to the fields an agent actually needs.

    Linear's raw issue carries nested objects for state/assignee/project/
    team/labels; this flattens the ones agents read and drops the rest
    (see tech design "Tool table").
    """
    state = issue.get("state") or {}
    assignee = issue.get("assignee")
    project = issue.get("project")
    team = issue.get("team") or {}
    labels_conn = issue.get("labels") or {}
    labels = [
        {"id": label.get("id"), "name": label.get("name")}
        for label in labels_conn.get("nodes", []) or []
    ]
    return {
        "id": issue.get("id"),
        "identifier": issue.get("identifier"),
        "title": issue.get("title"),
        "description": issue.get("description"),
        "state": {"id": state.get("id"), "name": state.get("name"), "type": state.get("type")}
        if state
        else None,
        "assignee": {"id": assignee.get("id"), "name": assignee.get("name")} if assignee else None,
        "priority": issue.get("priority"),
        "project": {"id": project.get("id"), "name": project.get("name")} if project else None,
        "team": {"id": team.get("id"), "key": team.get("key")} if team else None,
        "labels": labels,
        "url": issue.get("url"),
        "created_at": issue.get("createdAt"),
        "updated_at": issue.get("updatedAt"),
    }


def not_found_envelope(resource: str, identifier: str) -> str:
    return error_envelope(
        "not_found", f"{resource} '{identifier}' not found or not visible to this API key", False
    )


async def run(client: LinearClient, query: str, variables: dict | None = None):
    """Shared call wrapper: executes a GraphQL operation, converting any
    LinearError into its envelope rather than letting it propagate as an
    exception (tools never raise — see vendor-mcp SOP §4.1).
    """
    try:
        return await client.execute(query, variables), None
    except LinearError as e:
        return None, e.to_envelope()
