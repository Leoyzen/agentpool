## Why

The MCP-over-ACP elicitation passthrough fix (correlation registry + synchronous `ext_method`) was validated only with mock-based unit tests. Production bugs in this area (e.g., `elicitation/create` returning `{}` immediately, responses not matching requests) were caught late because no test exercises the real fastmcp `ClientSession` → `AcpMcpTransport` → `AcpMcpConnection` → `ext_method` chain. We need e2e tests that use real fastmcp components to prevent regressions.

## What Changes

- Add fastmcp end-to-end integration tests in `tests/agentpool_server/acp_server/`
- Test real `ClientSession` through `AcpMcpTransport` with `AcpMcpConnection`
- Verify correlation registry works correctly with actual fastmcp request/response flow
- Test elicitation passthrough: `elicitation/create` request → wait for response → return result
- Add `@pytest.mark.slow` to tests that spawn real fastmcp processes
- Update existing mock tests if needed to align with new patterns

## Capabilities

### New Capabilities
- `mcp-acp-e2e-testing`: End-to-end test infrastructure for MCP-over-ACP using real fastmcp ClientSession and memory streams

### Modified Capabilities
- (None — this is purely test infrastructure, no behavioral changes to existing code)

## Impact

- New test files under `tests/agentpool_server/acp_server/`
- `pytest` dependency already present, no new runtime dependencies
- Test duration increase for slow tests (real fastmcp client/server lifecycle)
- CI may need `pytest -m "not slow"` for fast checks, full suite for merges
