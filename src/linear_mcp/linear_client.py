"""Thin async GraphQL client for Linear's API.

Linear returns GraphQL errors as HTTP 200 with a top-level "errors" array
(standard GraphQL semantics), not as 4xx/5xx — so classification below reads
both the HTTP status code and, when present, errors[0].extensions.code. See
https://linear.app/developers/graphql and /rate-limiting.
"""

import asyncio
import time
from typing import Any

import httpx

from ._json import error_envelope

DEFAULT_BASE_URL = "https://api.linear.app/graphql"

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_MAX_RETRIES = 3
_MAX_BACKOFF_SECONDS = 20.0

# GraphQL error codes Linear documents (developers.linear.app) that this
# client recognizes. Anything else falls through to "upstream_error" rather
# than guessing.
_AUTH_CODES = {"AUTHENTICATION_ERROR", "FORBIDDEN"}
_RATE_LIMIT_CODES = {"RATELIMITED"}
_INVALID_INPUT_CODES = {"INVALID_INPUT", "BAD_USER_INPUT", "GRAPHQL_VALIDATION_FAILED"}

# One shared connection pool for the process lifetime. No credentials are
# ever stored on it — the API key is passed per-request via headers, so this
# is safe to share across tenants/requests (see server.py's contextvar-based
# credential isolation, which is what actually keeps tenants apart).
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=_TIMEOUT)
    return _http_client


class LinearError(Exception):
    def __init__(self, code: str, message: str, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(f"Linear API error [{code}]: {message}")

    def to_envelope(self, **details: Any) -> str:
        return error_envelope(self.code, self.message, self.retryable, **details)


def _classify_status(status_code: int) -> tuple[str, bool]:
    if status_code in (401, 403):
        return "unauthorized", False
    if status_code == 429:
        return "rate_limited", True
    if status_code in (400, 422):
        return "invalid_argument", False
    if status_code >= 500:
        return "upstream_error", True
    return "upstream_error", True


def _classify_graphql_error(err: dict) -> tuple[str, bool]:
    code = ((err.get("extensions") or {}).get("code") or "").upper()
    if code in _AUTH_CODES:
        return "unauthorized", False
    if code in _RATE_LIMIT_CODES:
        return "rate_limited", True
    if code in _INVALID_INPUT_CODES:
        return "invalid_argument", False
    return "upstream_error", True


class LinearClient:
    """Async httpx client wrapping Linear's GraphQL API.

    One instance per request (see server.get_client_from_context) — reuses
    the module-level connection pool across every call, never opens a new
    connection per call.
    """

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL):
        self._api_key = api_key
        self._base_url = base_url

    def _headers(self) -> dict[str, str]:
        # Linear's personal-API-key convention: the raw key goes straight
        # into Authorization, no "Bearer" prefix. The service never
        # validates or reformats this value, so a caller already holding an
        # OAuth2 Bearer token can also use this header — Linear accepts
        # that form on the same header (see tech design "Data & API
        # contract" in docs/tickets/PRD-21310.md).
        return {
            "Authorization": self._api_key,
            "Content-Type": "application/json",
        }

    async def execute(self, query: str, variables: dict | None = None) -> Any:
        """Run one GraphQL query/mutation. Returns the `data` field.

        Raises LinearError on any condition described in the tech design's
        error table: unauthorized / not_found (caller's own responsibility
        for "found but null") / invalid_argument / rate_limited /
        upstream_error.
        """
        client = _get_http_client()
        url = self._base_url
        headers = self._headers()
        body = {"query": query, "variables": variables or {}}

        last_exc: Exception | None = None
        resp: httpx.Response | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await client.post(url, headers=headers, json=body)
            except httpx.RequestError as e:
                last_exc = e
                if attempt < _MAX_RETRIES:
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    continue
                raise LinearError(
                    "upstream_error", f"{e or type(e).__name__} (url={url})", True
                ) from e

            if resp.status_code == 429 and attempt < _MAX_RETRIES:
                await asyncio.sleep(self._retry_delay(resp, attempt))
                continue
            if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                await asyncio.sleep(self._retry_delay(resp, attempt))
                continue
            break

        if resp is None:
            if last_exc:
                raise LinearError("upstream_error", f"{last_exc}", True) from last_exc
            raise LinearError("upstream_error", "request failed with no response", True)

        if resp.status_code != 200:
            code, retryable = _classify_status(resp.status_code)
            raise LinearError(code, self._error_message(resp), retryable)

        payload = self._parse_body(resp)
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if errors:
            first = errors[0] if isinstance(errors, list) and errors else {"message": str(errors)}
            code, retryable = _classify_graphql_error(first if isinstance(first, dict) else {})
            if code == "rate_limited":
                # GraphQL-level RATELIMITED can arrive with a 200 status, so
                # the retry loop above (status-code based) never sees it.
                # Retry at this level too, once per remaining attempt budget.
                for attempt in range(_MAX_RETRIES):
                    await asyncio.sleep(min(2**attempt, _MAX_BACKOFF_SECONDS))
                    retry_resp = await client.post(url, headers=headers, json=body)
                    retry_payload = self._parse_body(retry_resp)
                    retry_errors = (
                        retry_payload.get("errors") if isinstance(retry_payload, dict) else None
                    )
                    if not retry_errors:
                        return retry_payload.get("data")
                raise LinearError(
                    "rate_limited",
                    first.get("message", "Linear API rate limit exceeded")
                    if isinstance(first, dict)
                    else "Linear API rate limit exceeded",
                    True,
                )
            message = (
                first.get("message", "Linear GraphQL error")
                if isinstance(first, dict)
                else str(first)
            )
            raise LinearError(code, message, retryable)

        return payload.get("data") if isinstance(payload, dict) else None

    def _retry_delay(self, resp: httpx.Response, attempt: int) -> float:
        # Prefer Linear's own Retry-After on a 429 (developers.linear.app/rate-limiting),
        # fall back to blind exponential backoff for 5xx or a missing header.
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        reset_at = resp.headers.get("X-RateLimit-Requests-Reset")
        if reset_at:
            try:
                delay = float(reset_at) / 1000.0 - time.time()
                if delay > 0:
                    return min(delay, _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        return min(2**attempt, _MAX_BACKOFF_SECONDS)

    def _parse_body(self, resp: httpx.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return {"raw_response": resp.text}

    def _error_message(self, resp: httpx.Response) -> str:
        try:
            detail = resp.json()
            if isinstance(detail, dict):
                return str(detail.get("error") or detail.get("message") or detail)
            return str(detail)
        except ValueError:
            return resp.text
