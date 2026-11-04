# linear-mcp

Linear MCP server — exposes the Linear issue tracker (teams, workflow states, labels, users, projects, issues, comments) as MCP tools for Agent platforms.

**Tech stack:** Python 3.12 + uv + FastMCP (Starlette)

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Authentication headers

| Header | Required | Description | How to get it |
|---|---|---|---|
| `X-Linear-Api-Key` | Yes | Linear personal API key (or an OAuth2 Bearer token, forwarded as-is on the same header). Scoped to exactly one Linear user/workspace. | Linear → Settings → Security & access → Personal API keys |

A request to `/mcp` with no `X-Linear-Api-Key` header gets `401` with the required header named in the body. There is no environment-variable fallback on the HTTP/gateway path — see Configuration below.

## Quick Start

```bash
# Install dependencies
cd linear-mcp
uv sync

# Run in HTTP/gateway mode (the default) — each request supplies its own
# key via the X-Linear-Api-Key header, nothing to set locally
uv run linear-mcp

# Or run in stdio mode for local single-user tools like Claude Desktop
export MCP_TRANSPORT=stdio
export LINEAR_API_KEY=lin_api_xxx
uv run linear-mcp
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

| Variable | Default | Description |
|----------|---------|--------------|
| `MCP_TRANSPORT` | `http` | `http` (gateway, default) or `stdio` (Claude Desktop / local dev only) |
| `LINEAR_API_KEY` | — | Linear personal API key (`lin_api_xxx`). Only read when `MCP_TRANSPORT=stdio` — the HTTP/gateway path never reads it, by design (no header = 401, never a silent fallback to this value) |
| `MCP_HTTP_PORT` | `8080` | HTTP server port |
| `LINEAR_BASE_URL` | `https://api.linear.app/graphql` | GraphQL endpoint |

## Claude Desktop Setup

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "linear": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/linear-mcp", "linear-mcp"],
      "env": {
        "LINEAR_API_KEY": "lin_api_xxx"
      }
    }
  }
}
```

## Transport Modes

### HTTP — gateway / multi-tenant (default)
```bash
uv run linear-mcp
# Each request must include: X-Linear-Api-Key: lin_api_xxx
# A request with no header gets 401 — there is no env-var fallback.
```

### stdio (Claude Desktop / CLI, local dev only)
```bash
export MCP_TRANSPORT=stdio
export LINEAR_API_KEY=lin_api_xxx
uv run linear-mcp
```

## Available Tools (10)

| Tool | Hint | Description |
|------|------|-------------|
| `linear_list_teams` | read-only | List the teams visible to this API key |
| `linear_list_workflow_states` | read-only | List the workflow states (e.g. Todo, In Progress, Done) for a team |
| `linear_list_labels` | read-only | List issue labels, optionally scoped to one team |
| `linear_list_users` | read-only | List the members of this Linear workspace |
| `linear_list_projects` | read-only | List projects, optionally scoped to one team |
| `linear_list_issues` | read-only | List issues, filterable by team/project/assignee/state/label/title |
| `linear_get_issue` | read-only | Get a single issue by id or identifier, including its description |
| `linear_create_issue` | write, non-destructive | Create a new issue in a team |
| `linear_update_issue` | write, non-destructive, idempotent | Update fields on an existing issue (partial update) |
| `linear_create_comment` | write, non-destructive | Add a comment to an issue |

No tool in this connector deletes or archives anything (v1 scope decision — see `docs/tickets/PRD-21310.md` "Decisions made by DEV01").

## Pagination

`linear_list_teams`, `linear_list_users`, `linear_list_projects`, and `linear_list_issues` accept `limit` (default 25, hard cap 50) and `cursor`. Each response includes `has_more` and `next_cursor`; pass `next_cursor` back as `cursor` to fetch the next page.

## Error format

Every tool returns either its result, or a structured error:

```json
{"error": {"code": "not_found", "message": "Issue ENG-9999 not found or not visible to this API key", "retryable": false}}
```

| `code` | Meaning | `retryable` |
|---|---|---|
| `not_configured` | No `X-Linear-Api-Key` was supplied (the gateway middleware 401s before this would normally trigger) | false |
| `unauthorized` | The API key is invalid, or Linear's GraphQL API returned an authentication error | false |
| `not_found` | The requested issue id/identifier does not exist, or isn't visible to this API key | false |
| `invalid_argument` | A required parameter was missing, out of range, or Linear rejected the GraphQL input | false |
| `rate_limited` | Linear's rate limit was hit and the retry budget was exhausted | true |
| `upstream_error` | Any other non-2xx response, network error, or unexpected GraphQL error | true |

## Rate limits and retries

Linear enforces 2,500 requests/hour and 3,000,000 complexity-points/hour for personal-API-key auth (see [linear.app/developers/rate-limiting](https://linear.app/developers/rate-limiting)), returning `429` with `Retry-After`. This connector retries `429`s (and `5xx`s) up to 3 times, honoring `Retry-After` when present, before surfacing a `rate_limited` error. GraphQL-level `RATELIMITED` errors arriving with HTTP 200 are retried the same way.

## Multi-tenant isolation

One container process serves every tenant. The caller's API key never leaves the current request's `contextvars.ContextVar`: the gateway middleware sets it per request and resets it in a `finally` block, so concurrent requests from different tenants never see each other's credential or data. There is no server-side cache of tenant data — Linear's own permission model (what the API key's user can see) is the only authorization boundary this service relies on.

## API Reference

- [Linear Developer Docs](https://linear.app/developers)
- [Linear GraphQL API](https://linear.app/developers/graphql)
- [Rate limiting](https://linear.app/developers/rate-limiting)
