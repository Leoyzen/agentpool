# Design: MCP Session Warmup

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Protocol Server                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ ACP Server   │  │ OpenCode     │  │ AG-UI        │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
└─────────┼────────────────┼────────────────┼──────────────┘
          │                │                │
          └────────────────┴────────────────┘
                           │
                    ┌──────▼──────┐
                    │  Warmup     │
                    │  Hook       │
                    └──────┬──────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
   ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
   │ MCP Provider│  │ MCP Provider│  │ MCP Provider│
   │  (lazy)     │  │  (lazy)     │  │  (eager)    │
   └─────────────┘  └─────────────┘  └─────────────┘
```

## Key Design Decisions

### 1. Hook Location

在每个 protocol server 的 session 建立回调中插入 warmup 逻辑：

- **ACP**: `acp_server.py` 的 `on_connect` 或 `handle_initialize`
- **OpenCode**: `opencode_server.py` 的 `get_or_create_agent` 之后
- **AG-UI**: `agui_server.py` 的 session 建立时

### 2. Warmup Implementation

在 `MCPResourceProvider` 或 `MCPManager` 上添加 `warmup()` 方法：

```python
async def warmup(self) -> None:
    """Ensure the provider is connected.
    
    Idempotent: safe to call multiple times.
    For eager providers, this is a no-op.
    For lazy providers, this triggers the deferred connection.
    """
    await self._ensure_client_connected()
```

### 3. Batch Warmup

在 `MCPManager` 上添加批量 warmup：

```python
async def warmup_all(self) -> None:
    """Warm up all lazy providers concurrently."""
    tasks = [
        provider.warmup()
        for provider in self.providers
        if not provider._client_connected
    ]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
```

### 4. Error Handling

- 单个 provider warmup 失败 → 记录日志，继续其他 provider
- 不阻塞 session 建立
- 失败的 provider 仍然可以在后续按需重试

## Integration Points

### ACP Server

```python
async def on_session_connect(session):
    # ... existing logic ...
    
    # Warmup MCP providers
    if mcp_manager := session.mcp_manager:
        await mcp_manager.warmup_all()
```

### OpenCode Server

```python
async def get_or_create_agent(session_id):
    agent = await _create_agent(session_id)
    
    # Warmup MCP providers
    if mcp_manager := agent.mcp_manager:
        await mcp_manager.warmup_all()
    
    return agent
```

## Testing Strategy

1. Unit test: `warmup()` triggers connection for lazy provider
2. Unit test: `warmup()` is no-op for eager provider
3. Unit test: `warmup_all()` handles partial failures
4. Integration test: session warmup makes tools available before first run

## Files to Modify

- `src/agentpool/resource_providers/mcp_provider.py` — add `warmup()`
- `src/agentpool/mcp_server/manager.py` — add `warmup_all()`
- `src/agentpool_server/acp_server/` — session warmup hook
- `src/agentpool_server/opencode_server/` — session warmup hook
- `src/agentpool_server/agui_server/` — session warmup hook
- `tests/` — warmup tests
