# Architectural Decisions

## 2026-05-22 - Initialize Guard Location
- DECISION: Add per-connection initialized state inside `AgentSideConnection`, NOT in generic `Connection`
- RATIONALE: `Connection` is shared protocol infrastructure; the guard is specific to server-side agent lifecycle
- LOCATION: `src/acp/agent/connection.py` near `_agent_handler()` or as a wrapper around request execution

## 2026-05-22 - Shutdown Behavior Ownership
- DECISION: Make `ACPServer.stop()` set `_shutdown_event` before delegating to base stop behavior
- RATIONALE: Current `BaseServer.stop()` cancels the task without signaling shutdown; the new transport needs the event to trigger uvicorn shutdown
- IMPLEMENTATION: Override `stop()` in `ACPServer` or modify `BaseServer.stop()` to set event before cancel

## 2026-05-22 - Starlette Dependency
- DECISION: Promote `starlette` to core dependency in `pyproject.toml`
- RATIONALE: ACP transport is intended as first-class server feature; should not rely on optional extra

## 2026-05-22 - Legacy Transport Deprecation
- DECISION: Keep `WebSocketTransport` working but emit deprecation warning; remove in v0.6.0 (2026-Q3)
- RATIONALE: Backward compatibility during migration period
