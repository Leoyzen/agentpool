## 1. AcpMcpConnection Correlation Registry (SUPERSEDED)

The correlation registry approach was implemented in `926a87f60` but later superseded by a simpler architecture: elicitation is now handled locally instead of forwarded to the ACP client. See commits `63a21e72c` and `d41417e42`.

- [x] 1.1 Implemented (superseded by local elicitation handling)
- [x] 1.2 Implemented (superseded by local elicitation handling)
- [x] 1.3 Implemented (superseded by local elicitation handling)
- [x] 1.4 Implemented (superseded by local elicitation handling)
- [x] 1.5 Implemented (superseded by local elicitation handling)

## 2. send_to_client Response Filtering (SUPERSEDED)

- [x] 2.1 Implemented (superseded by local elicitation handling)
- [x] 2.2 Implemented (superseded by local elicitation handling)
- [x] 2.3 Implemented (superseded by local elicitation handling)
- [x] 2.4 Implemented (superseded by local elicitation handling)
- [x] 2.6 Implemented (superseded by local elicitation handling)

## 3. ext_method Synchronous Request Handling (SUPERSEDED)

- [x] 3.1 Implemented (superseded by local elicitation handling)
- [x] 3.2 Implemented (superseded by local elicitation handling)
- [x] 3.3 Implemented (superseded by local elicitation handling)
- [x] 3.4 Implemented (superseded by local elicitation handling)
- [x] 3.5 Implemented (superseded by local elicitation handling)
- [x] 3.6 Implemented (superseded by local elicitation handling)
- [x] 3.7 Implemented (superseded by local elicitation handling)
- [x] 3.8 Implemented (superseded by local elicitation handling)
- [x] 3.9 Implemented (superseded by local elicitation handling)

## 4. Tests

### 4.1 New Regression Tests

- [x] 4.1 Approaches superseded by local elicitation handling (`63a21e72c`, `d41417e42`)
- [x] 4.2 Approaches superseded by local elicitation handling
- [x] 4.3 Approaches superseded by local elicitation handling
- [x] 4.4 Approaches superseded by local elicitation handling
- [x] 4.5 Approaches superseded by local elicitation handling
- [x] 4.6 Approaches superseded by local elicitation handling
- [x] 4.7 Approaches superseded by local elicitation handling
- [x] 4.8 Approaches superseded by local elicitation handling
- [x] 4.9 Approaches superseded by local elicitation handling
- [x] 4.10 Approaches superseded by local elicitation handling
- [x] 4.11 Approaches superseded by local elicitation handling
- [x] 4.12 Approaches superseded by local elicitation handling
- [x] 4.13 Approaches superseded by local elicitation handling
- [x] 4.14 Approaches superseded — local elicitation handling (`d41417e42`) makes correlation roundtrip tests unnecessary
- [x] 4.15 Implemented in `0a85a1da1` — 15 fastmcp integration tests exist in `test_acp_mcp_fastmcp_integration.py`
- [x] 4.16 Approaches superseded by local elicitation handling

### 4.2 Update Existing Tests for New Behavior

The fix changes behavior in two ways that break existing tests:
1. **MCP responses** (messages with `"result"` or `"error"` + `"id"`) are no longer forwarded to the ACP client unless they match a pending request
2. **Client-initiated requests** (messages with `"method"` + `"id"`) now block `ext_method` until a response arrives, instead of returning `{}` immediately

- [x] 4.17 Approaches superseded — the current architecture uses firing-and-forget for all mcp/message (local elicitation handling). Existing tests at `test_acp_mcp_transport.py` (8 tests) pass and cover current behavior.
- [x] 4.18 Approaches superseded — existing tests at `test_acp_mcp_end_to_end.py` (4 tests) pass and cover current architecture.
- [x] 4.19 Approaches superseded — existing tests at `test_acp_mcp_manager.py` (24 tests) pass and cover current architecture.
- [x] 4.20 Approaches superseded — existing tests at `test_acp_mcp_agent_integration.py` (10 tests) pass. The current `ext_method` uses fire-and-forget by design (local elicitation handles synchrony).

### 4.3 Test Execution

- [x] 4.21 MCP-over-ACP tests pass (67 tests across 6 files)
- [x] 4.22 Regression path covered — existing MCP transport tests pass

## 5. Verification

- [x] 5.1 Code compiles with current architecture — mypy passes on production files
- [x] 5.2 ruff clean on affected files
- [x] 5.3 67 MCP-over-ACP tests pass
- [x] 5.4 ACP server tests pass (verified in CI)
