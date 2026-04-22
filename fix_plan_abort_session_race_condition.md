# 修复计划：解决abort_session()返回太早导致的Race Condition

## 问题描述

**症状**: 用户报告 Ctrl+C/Esc 无法中断消息，新消息显示"QUEUED"无法执行

**根本原因**: `abort_session()` 在调用 `agent.interrupt()` 后立即返回（仅等待0.1秒），没有等待实际的stream停止。

**Race Condition 流程**:
```
1. User presses Ctrl+C/Esc
2. TUI sends POST /{session_id}/abort
3. abort_session() executes:
   - Calls agent.interrupt() → Sets cancelled flag, cancels tasks
   - Sleeps 0.1s → Arbitrary timeout
   - Sets session_status = idle
   - Broadcasts SessionIdleEvent
   - Returns True ← PROBLEM: Returns before stream stops
4. TUI shows session as idle
5. User sends new message
6. New message calls _process_message_locked()
7. Tries to acquire agent_lock → BLOCKS (still held by stopping stream)
8. UI shows "QUEUED" indefinitely
```

## 解决方案

**核心思路**: 在 `ServerState` 中跟踪活跃的stream任务，并在 `abort_session()` 中等待该任务完成后再返回。

### 实现步骤

#### 步骤1: 在ServerState中添加活跃任务跟踪字段

**文件**: `src/agentpool_server/opencode_server/state.py`

在 `ServerState` dataclass 中添加新字段（在 line 129 之后）:

```python
# Active stream task for cancellation synchronization
# Tracks the current message processing task so abort_session() can wait for it
active_stream_task: asyncio.Task[Any] | None = field(default=None, repr=False)
```

**位置**: 在 `command_store: CommandStore | None = field(default=None)` 之后

#### 步骤2: 在_process_message_locked()中设置和清除活跃任务

**文件**: `src/agentpool_server/opencode_server/routes/message_routes.py`

**修改1**: 在调用 `agent.run_stream()` 之前设置任务（约 line 470 之前）:

```python
# Track active stream task for abort_session() synchronization
# This allows abort_session() to wait for actual stream completion
current_task = asyncio.current_task()
state.active_stream_task = current_task

try:
    async for oc_event in adapter.process_stream(iterator):
        await state.broadcast_event(oc_event)
finally:
    # Clear active stream task after stream completes (or is cancelled)
    state.active_stream_task = None
    if original_model is not None:
        with contextlib.suppress(Exception):
            await agent.set_model(original_model)
```

**注意**: 这需要修改现有的finally块（lines 473-476），在 `active_stream_task = None` 之前添加。

#### 步骤3: 在abort_session()中等待活跃任务完成

**文件**: `src/agentpool_server/opencode_server/routes/session_routes.py`

**修改**: 在调用 `agent.interrupt()` 后添加等待逻辑（在 line 880 之后）:

```python
# Interrupt the agent to cancel any ongoing stream
try:
    await state.agent.interrupt()

    # Give a moment for cancellation to propagate
    await asyncio.sleep(0.1)

    # Wait for active stream task to complete
    # This ensures agent_lock is released before we mark session as idle
    if state.active_stream_task and not state.active_stream_task.done():
        logger.info(
            "Waiting for active stream task to complete",
            session_id=session_id,
            task_id=id(state.active_stream_task),
        )
        try:
            # Wait for task with timeout to prevent hanging
            await asyncio.wait_for(
                state.active_stream_task,
                timeout=5.0,  # 5s timeout for stream to stop
            )
            logger.info("Active stream task completed", session_id=session_id)
        except asyncio.TimeoutError:
            logger.warning(
                "Active stream task did not complete in time",
                session_id=session_id,
                timeout=5.0,
            )
        except Exception as exc:
            logger.warning(
                "Error waiting for active stream task",
                session_id=session_id,
                error=str(exc),
            )

except Exception:  # noqa: BLE001
    pass
```

**位置**: 替换现有的 lines 876-882

## 为什么这个方案有效

### 1. 任务跟踪确保同步
- `active_stream_task` 在stream开始时设置，结束时清除
- `abort_session()` 可以检查任务是否还在运行
- 只有任务完成后才标记session为idle

### 2. 消除Race Condition
- 新消息不会在stream还在停止时尝试获取 `agent_lock`
- 确保lock被释放后才允许新消息开始处理

### 3. 向后兼容
- 不改变agent层的行为（interrupt机制仍然正常工作）
- 只在server层添加同步机制
- 超时保护防止无限等待

### 4. 恢复Pre-1.4.4行为
- 类似于移除的route-level `asyncio.timeout()` 机制
- 但更精确：只等待stream任务，不强制中断

## 测试计划

### 1. 单元测试（现有测试应保持PASS）
- `test_interrupt.py` - Agent interrupt机制
- 这些测试不涉及ServerState，应该不受影响

### 2. 集成测试（新增）
创建 `tests/test_abort_session_fix.py`:

```python
async def test_abort_session_waits_for_stream_completion():
    """Test that abort_session() waits for stream to complete."""
    # 1. Start slow message (holds agent_lock for 5s)
    # 2. Call abort_session() after 0.05s
    # 3. Verify abort_session() waits for stream to complete
    # 4. Verify new message can acquire agent_lock after abort returns
    pass

async def test_abort_session_timeout():
    """Test that abort_session() has timeout protection."""
    # 1. Start slow message that never completes
    # 2. Call abort_session()
    # 3. Verify it times out after 5s and returns
    # 4. Verify no indefinite blocking
    pass

async def test_multiple_concurrent_aborts():
    """Test that multiple concurrent abort calls work correctly."""
    # 1. Start slow message
    # 2. Call abort_session() multiple times concurrently
    # 3. Verify all calls succeed without race conditions
    pass
```

### 3. 手动测试
1. 启动OpenCode server
2. 发送一个慢消息（使用BlockingTestModel）
3. 按下Ctrl+C/Esc
4. 验证消息被中断
5. 立即发送新消息
6. 验证新消息正常执行（不显示"QUEUED"）

## 回归计划

如果修复引入新问题：
1. 移除 `active_stream_task` 字段
2. 移除message_routes.py中的任务设置/清除代码
3. 移除session_routes.py中的等待逻辑
4. 恢复到当前状态

## 风险评估

### 低风险
- 只修改server层，不触及agent核心逻辑
- 向后兼容，不改变API
- 超时保护防止无限等待

### 中等风险
- 如果stream任务异常完成，`active_stream_task` 可能未被清除
- **缓解措施**: 使用try-finally确保清除

### 需要验证
- 多个并发abort调用是否会互相干扰
- 慢stream任务（>5s）的超时行为是否合理

## 实施顺序

1. ✅ 创建修复计划（本文档）
2. ⏭️ 修改ServerState添加字段
3. ⏭️ 修改message_routes.py设置/清除任务
4. ⏭️ 修改session_routes.py等待任务
5. ⏭️ 运行现有测试确保PASS
6. ⏭️ 创建新集成测试
7. ⏭️ 手动测试验证修复
8. ⏭️ 代码审查和优化
