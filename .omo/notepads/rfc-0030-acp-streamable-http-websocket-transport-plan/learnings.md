# Learnings and Conventions

## Codebase Patterns
- Uses `from __future__ import annotations` everywhere
- Type hints required, checked with mypy --strict
- Google-style docstrings, no types in Args
- Tests use pytest, not in classes
- Uses match/case, walrus operator, modern Python 3.13 syntax
- Transport configs are dataclasses in `src/acp/transports.py`
- `serve()` normalizes string literals to config objects then dispatches
- `AgentSideConnection` wraps `Connection` with agent-specific methods
- `_agent_handler()` is a standalone function handling all JSON-RPC methods
- `BaseServer.stop()` cancels the server task without setting `_shutdown_event`
- uvicorn is already a core dependency; starlette needs to be added
- `websockets` library is already a dependency

## Key Files
- `src/acp/transports.py` - transport configs, serve(), stream adapters
- `src/acp/agent/connection.py` - AgentSideConnection, _agent_handler
- `src/acp/__init__.py` - public exports
- `src/agentpool_server/acp_server/server.py` - ACPServer
- `src/agentpool_server/base.py` - BaseServer with start_background/stop
- `src/agentpool_config/pool_server.py` - ACPPoolServerConfig
- `src/agentpool_cli/serve_acp.py` - CLI command
- `src/agentpool_cli/ui.py` - Toad helper uses `--transport websocket --ws-port`

## 2026-05-22 - Completion Summary

All phases of RFC-0030 implementation complete:

### Verification Results
- New tests: 41/41 passed (22 transport + 10 integration + 9 CLI)
- Existing ACP RPC tests: 9/9 passed (no regressions)
- ruff: All checks passed on all changed files
- lsp_diagnostics: Clean on all modified files

### Key Gotchas
- `uv run pytest` fails due to pre-existing `mistralai` package registry issue; use `.venv/bin/pytest` instead
- Pre-existing snapshot test `test_execute_command_simple` in `test_acp_via_acp_snapshots.py` fails on original code too
- Integration tests with mocked WebSocket objects produce expected `AttributeError`/`TypeError` in receive loop when mocks return coroutines/MagicMock instead of bytes - these are expected because the test kills the connection immediately; the tests still pass

### Files Modified
- src/acp/transports.py (+153/-1): ACPWebSocketTransport, _serve_streamable_http(), Starlette adapters
- src/acp/agent/connection.py (+24/-3): Initialize guard with -32002 rejection
- src/acp/__init__.py (+2/-0): Re-export ACPWebSocketTransport
- src/agentpool_server/acp_server/server.py (+29/-2): stop() override, from_config() transport resolution
- src/agentpool_config/pool_server.py (+24/-0): transport/host/port fields
- src/agentpool_cli/serve_acp.py (+43/-11): --transport streamable-http, --host, --port, deprecation warning
- src/agentpool_cli/ui.py (+2/-2): Migrated to --transport streamable-http --port
- pyproject.toml (+1/-0): Added starlette>=0.40 dependency
- tests/servers/acp_server/test_rpc.py (+17/-0): Added initialize() calls before guarded methods

### Files Created
- tests/acp/test_streamable_http_transport.py (470 lines)
- tests/servers/acp_server/test_streamable_http_integration.py (461 lines)
- tests/cli/test_serve_acp_streamable_http.py (290 lines)
- tests/acp/__init__.py
- tests/cli/__init__.py
