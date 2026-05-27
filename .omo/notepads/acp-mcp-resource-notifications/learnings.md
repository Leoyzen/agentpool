# Learnings for ACP MCP Resource Notification Bridge

## Codebase Conventions

- Python 3.13+, use modern syntax (match/case, walrus operator)
- Type hints required, mypy --strict
- Google-style docstrings, no types in Args section
- Tests use pytest, not in classes
- `from __future__ import annotations` for forward references
- Use `TYPE_CHECKING` to avoid circular imports
- Signal system from `anyenv.signals.Signal[T]`
- Resource providers use `create_change_event(resource_type)` helper
- MCP message handler uses match/case for notification dispatch
- ACP `ExtNotification` uses underscore-prefixed method names
- `client.ext_notification(method, params)` is the ACP client protocol method

## Key Files and Patterns

- `base.py`: `ResourceChangeEvent` (frozen dataclass, slots) for list changes
- `message_handler.py`: `MCPMessageHandler` dataclass with callback fields
- `client.py`: `MCPClient.__init__` stores callbacks, `_get_client()` passes to handler
- `mcp_provider.py`: `MCPResourceProvider` wires callbacks to provider methods
- `session.py`: `ACPSession` lifecycle, `initialize_mcp_servers()` loops over `mcp_servers`
- `manager.py`: `setup_server()` returns `MCPResourceProvider | None`

## Signal Patterns

```python
# Signal declaration
class ResourceProvider:
    tools_changed: Signal[ResourceChangeEvent] = Signal()

# Emitting
await self.tools_changed.emit(self.create_change_event("tools"))

# Connecting (in session)
provider.tools_changed.connect(self._handler)
# Disconnecting
provider.tools_changed.disconnect(self._handler)
```

## ACP Extension Notification Pattern

```python
# Sending
await self.client.ext_notification("_mcp/tools/listChanged", {"provider_name": ...})
```

## Must NOT Have Guardrails

- No ACP schema changes
- No `SessionNotification` for MCP changes
- No `getattr`/`hasattr` fallback logic
- No coupling of MCP/provider to ACP classes
- No client capability negotiation, debouncing, resume, reconnection
- No fixing pre-existing non-MCP signal cleanup unless required by tests

## Testing Patterns

- `AsyncMock` for ACP client
- `Agent.from_callback()` for test agents
- `AgentPool()` for test pools
- `ACPSession(session_id=..., agent=..., client=mock_client, ...)`
- Fixtures in `conftest.py` for reusable setup

## Task 2 Learnings (2026-05-22)

- `ResourceUpdatedNotification` does NOT have a direct `uri` attribute; the URI is at `message.params.uri` (type `AnyUrl`).
- The original code used `getattr(message, "uri", "unknown")` which was silently broken (always returned "unknown" at runtime).
- Using keyword arguments when constructing `MCPMessageHandler` in `_get_client()` prevents future positional ordering mistakes.
- `str(message.params.uri)` converts `AnyUrl` to `str` for the callback signature `Callable[[str], Awaitable[None]]`.
