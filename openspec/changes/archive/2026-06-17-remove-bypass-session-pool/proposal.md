## Why

`_bypass_session_pool` ContextVar 和 `_should_bypass_session_pool()` 函数是 SessionPool 架构中的一个症状疗法——它们解决的是 `asyncio.Lock`（turn_lock）不可重入导致的死锁问题，而非根因。这导致了两条并行的执行路径（SessionPool 路径 vs 直接执行路径），增加了认知负担、测试复杂度和潜在的 bug 面。AG-UI bypass 已在 thin-agentpool-core 中移除，现在是时候消除剩余的 TurnRunner 内部 bypass 了。

## What Changes

- **移除 `_bypass_session_pool` ContextVar**：删除 `base_agent.py` 中的 `_bypass_session_pool` 变量、`_should_bypass_session_pool()` 函数及其所有引用
- **引入 Turn-Owner Tracking 机制**：用 `SessionState._turn_owner_task` 替代锁的重入问题——通过检查当前 task 是否已持有 `turn_lock` 来决定是否路由到 SessionPool
- **消除 `_run_stream_direct()` 路径**：TurnRunner 内部的嵌套调用不再走直接执行路径，而是通过 Turn-Owner Tracking 安全地复用当前 turn 上下文
- **简化 `BaseAgent.run_stream()` / `run()` 路由逻辑**：从二元选择（bypass vs SessionPool）变为统一的路由决策
- **删除测试中的 `_bypass_session_pool.set(True)` 用法**：测试改用 TurnRunner 创建 session 的方式或 mock

## Capabilities

### New Capabilities
- `turn-owner-tracking`: 通过 `SessionState._turn_owner_task` 跟踪当前持有 `turn_lock` 的 asyncio.Task，使嵌套调用能安全识别自己是否已在 turn 中，从而避免死锁

### Modified Capabilities
- `sessionpool-only-execution`: 更新为不再需要 bypass 路径——所有 agent 执行都通过 SessionPool 路由

## Impact

- `src/agentpool/agents/base_agent.py`：删除 `_bypass_session_pool` ContextVar、`_should_bypass_session_pool()`、简化 `run_stream()`/`run()` 路由逻辑
- `src/agentpool/orchestrator/core.py`：`_run_turn_unlocked()` 不再设置/清除 ContextVar，改为管理 `SessionState._turn_owner_task`
- `src/agentpool_server/agui_server/base_agent_adapter.py`：AG-UI 适配器——已无 stack inspection，确认无需 ContextVar
- `tests/orchestrator/test_turn_runner.py`：更新 bypass 相关测试
- `tests/orchestrator/test_streaming_redflag_tool_calls.py`：删除 `_bypass_session_pool.set(True)` 用法