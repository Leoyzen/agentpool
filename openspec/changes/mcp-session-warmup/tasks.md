# Tasks: MCP Session Warmup

## Task 1: Add `warmup()` to MCPResourceProvider

**What**: Add `warmup()` method to `MCPResourceProvider` that triggers lazy connection.

**File**: `src/agentpool/resource_providers/mcp_provider.py`

**Expected**:
- `warmup()` calls `_ensure_client_connected()`
- Idempotent (safe to call multiple times)
- No-op for already-connected providers

## Task 2: Add `warmup_all()` to MCPManager

**What**: Add `warmup_all()` method that warms up all lazy providers concurrently.

**File**: `src/agentpool/mcp_server/manager.py`

**Expected**:
- Gather all unconnected providers
- Run concurrently with `asyncio.gather(..., return_exceptions=True)`
- Log failures but don't raise

## Task 3: Add warmup hook to ACP Server

**What**: Call `warmup_all()` when ACP session is established.

**File**: `src/agentpool_server/acp_server/`

**Expected**:
- Find session initialization point
- Call `mcp_manager.warmup_all()` after agent/provider setup
- Handle missing manager gracefully

## Task 4: Add warmup hook to OpenCode Server

**What**: Call `warmup_all()` when OpenCode session is established.

**File**: `src/agentpool_server/opencode_server/`

**Expected**:
- Find `get_or_create_agent` or equivalent
- Call warmup after agent creation
- Handle missing manager gracefully

## Task 5: Add warmup hook to AG-UI Server

**What**: Call `warmup_all()` when AG-UI session is established.

**File**: `src/agentpool_server/agui_server/`

**Expected**:
- Find session initialization point
- Call warmup after agent/provider setup

## Task 6: Write tests

**What**: Test warmup behavior.

**File**: `tests/test_mcp_warmup.py`

**Expected**:
- `warmup()` triggers connection for lazy provider
- `warmup()` is no-op for eager provider
- `warmup_all()` handles multiple providers
- `warmup_all()` handles partial failures

## Task 7: Verify no regressions

**What**: Run full test suite.

**Expected**:
- All existing tests pass
- New tests pass
- Lint/type check pass
