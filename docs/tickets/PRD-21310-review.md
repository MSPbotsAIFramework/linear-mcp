# PRD-21310 · Code review

## Round 1 · 2026-11-04 · reviewed dev/PRD-21310 @ 97843ef (base main @ f143805)

Verdict: changes requested

### Findings

1. [must-fix] docs/tickets/PRD-21310.md:316 — The "Reuse & cleanup" paragraph in the Self-test section quotes a Chinese phrase ("复用与清理"), which fails the MCP repo's English-only requirement (vendor-mcp-development SOP §0.4 / §14 checklist: "仓库内容全部英文：代码、注释、工具描述、错误信息、日志、文档、commit message 均无中文"; `mcp-track.md` §4.3 review standard: "仓库里没有中文"). Worse, the same Self-test section's own gate table reports the no-Chinese scan (`grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .`) as "no output" — re-running that exact command on this worktree (`97843ef`) returns one hit, at this line. The self-test record is factually wrong about a gate it claims passed. Fix: remove or translate the Chinese phrase (write it as "Reuse & cleanup" / spell out the Chinese term in Latin transliteration only if a citation is unavoidable, e.g. quoting the Mandarin skill-doc section name is not necessary here — the English section name already identifies it), then re-run the scan and correct the Self-test row to reflect what was actually observed.

2. [must-fix] docs/tickets/PRD-21310.ac.json — Two of the "auto, passing" test-case references do not resolve to the test they claim to cite, breaking the ledger's traceability (the standard this pipeline holds auto cases to: a passing citation is the evidence, so a wrong citation is "a case claiming pass without evidence", `legs/review.md` R3 "测试" row):
   - TC1 (`"tests": ["tests/test_middleware.py::test_missing_header_returns_401_and_no_upstream_call"]`) — no such test exists anywhere in the suite. The actual test that covers AC1's missing-header-401 behavior is `tests/test_middleware.py::test_missing_header_returns_401_with_required_headers_listed` (confirmed by `grep -n 'def test_' tests/test_middleware.py` and a clean `uv run pytest -q` run, 33 passed, on `97843ef`).
   - TC13 (`"tests": ["tests/test_middleware.py::test_no_env_fallback_for_http_transport"]`) — wrong file: that test function is defined in `tests/test_tools.py:318`, not `tests/test_middleware.py`. The behavior itself is genuinely tested (confirmed by reading `tests/test_tools.py:316-326`), only the ledger's pointer is wrong.
   Fix: correct both `tests` entries in `docs/tickets/PRD-21310.ac.json` to the real `file::test_name` strings so the ledger can actually be used to re-locate the passing evidence.

### Verified independently during this review (not findings, recorded for the next reader)

- `uv run pytest -q` on `97843ef`: 33 passed (matches the Self-test claim).
- `uv run ruff check .` / `uv run ruff format --check .`: clean (matches the Self-test claim).
- `sudo docker build --platform linux/amd64 .`: builds, only the same 3 harmless `UndefinedVar` warnings on `$VERSION`/`$BUILD_DATE`/`$COMMIT_SHA` reported in the Self-test.
- Container smoke: `GET /health` → `200 {"status":"ok"}`; `whoami` inside the running container → `linear` (non-root), matching the Self-test claim.
- `gitleaks detect --source . --no-git`: no leaks found (Self-test recorded this as "not installed in this sandbox" with a grep fallback; it is installed in this review's sandbox and confirms clean).
- README's "MSPbots Connector" section (lines 1-77) is byte-for-byte identical to `~/.claude/skills/vendor-mcp-development/reference/readme-connector-section.md` and sits before the Header credential table, satisfying SOP §0.5/§14.
- Credential handling (`X-Linear-Api-Key` only, `ContextVar` + `finally` reset, no env-var fallback on the HTTP path, no shared mutable per-tenant state), error envelope shape, and the AC10 concurrency test (`tests/test_middleware.py::test_concurrent_requests_with_different_keys_do_not_cross_contaminate`, asserted against the real ASGI HTTP stack, not just middleware internals) all match the tech design and the SOP's three iron laws.
- Scope: this is a from-scratch build in a repo with no prior code; nothing outside the design's change list was touched.

### Fixes (round 1)

Both must-fix items addressed on `dev/PRD-21310`, no other changes made.

1. **docs/tickets/PRD-21310.md:316** — removed the Chinese phrase quoted in finding 1 above from the Reuse & cleanup paragraph (the English section name already identifies it, so no transliteration was needed). Also corrected the same Self-test section's No-Chinese scan row, which had falsely reported "no output": re-ran `grep -rnP '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .` and recorded the real result — zero hits outside this review record (which necessarily quotes the phrase and SOP text as evidence for this very finding). Commit `1406a35`.
2. **docs/tickets/PRD-21310.ac.json** — corrected both wrong test citations:
   - TC1: `tests/test_middleware.py::test_missing_header_returns_401_and_no_upstream_call` (does not exist) → `tests/test_middleware.py::test_missing_header_returns_401_with_required_headers_listed` (exists, matches AC1).
   - TC13: `tests/test_middleware.py::test_no_env_fallback_for_http_transport` (wrong file) → `tests/test_tools.py::test_no_env_fallback_for_http_transport` (correct file, confirmed at `tests/test_tools.py:318`).
   Commit `1406a35`.

Re-run after both fixes (commit `1406a35`, no code changed — doc/ledger only):
- `uv run pytest -q`: 33 passed (unchanged).
- Both corrected test IDs individually resolve and pass (`uv run pytest -q tests/test_middleware.py::test_missing_header_returns_401_with_required_headers_listed tests/test_tools.py::test_no_env_fallback_for_http_transport`: 2 passed).
- `uv run ruff check .` / `uv run ruff format --check .`: clean.
- No-Chinese scan: zero hits outside this review record.

Doc header's `Status: frozen @ <sha>` self-reference re-pinned to `1406a35` in a follow-up one-line commit (`25e9b95`), same pattern as round 0's `a5119f6`.

Docker build / container smoke / credential-isolation / concurrency gates were not re-run — this round changed only `docs/tickets/PRD-21310.md` and `docs/tickets/PRD-21310.ac.json`, no application code, so the round-0 results for those gates (already verified independently above) still hold.
