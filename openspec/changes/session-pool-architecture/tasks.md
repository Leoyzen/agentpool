## 1. BaseAgent Public API Extension

- [x] 1.1 Add `get_active_run_context()` public method to BaseAgent
- [x] 1.2 Add `is_turn_active()` helper method to BaseAgent
- [x] 1.3 Add unit tests for new BaseAgent APIs
- [x] 1.4 Verify no regression in existing BaseAgent tests

## 2. SessionPool Core Infrastructure

- [x] 2.1 Create `src/agentpool/orchestrator/` package with `__init__.py`
- [x] 2.2 Implement `SessionState` dataclass with turn_lock, is_closing, metadata
- [x] 2.3 Implement `EventBus` with subscribe, unsubscribe, publish, close_session
- [x] 2.4 Implement `SessionController` with get_or_create_session, get_or_create_session_agent, close_session
- [x] 2.5 Implement `TurnRunner` with run_turn, run_loop, inject_prompt, queue_prompt, auto-resume
- [x] 2.6 Implement `SessionPool` facade combining SessionController and TurnRunner
- [x] 2.7 Implement `SessionPoolMetrics` and `MetricsCollector`
- [x] 2.8 Add Session TTL cleanup background task to SessionController
- [x] 2.9 Add MCP process limit tracking to SessionController
- [x] 2.10 Write unit tests for EventBus (bounded queues, dropping, sentinel)
- [x] 2.11 Write unit tests for SessionController (lifecycle, cleanup, TTL)
- [x] 2.12 Write unit tests for TurnRunner (serialization, auto-resume, cancellation)
- [x] 2.13 Write unit tests for SessionPool (integration)

## 3. AgentPool Integration

- [x] 3.1 Add `enable_session_pool` and `session_pool_config` to AgentPool.__init__
- [x] 3.2 Add SessionPool lifecycle management to AgentPool.__aenter__/__aexit__
- [x] 3.3 Add `AgentPool.create_session()` convenience method
- [x] 3.4 Define `SessionPoolConfig` Pydantic model in agentpool_config
- [x] 3.5 Update `AgentsManifest` to accept session_pool configuration
- [x] 3.6 Add per-protocol feature flags (acp.use_session_pool, opencode.use_session_pool)
- [x] 3.7 Write integration tests for AgentPool + SessionPool
- [x] 3.8 Write mixed-mode tests (SessionPool enabled/disabled)
- [x] 3.9 Write rollback tests (feature flag off after being on)

## 4. ACP Protocol Handler Migration

- [x] 4.1 Create `ACPProtocolHandler` class skeleton in acp_server/handler.py
- [x] 4.2 Implement `_ensure_event_consumer()` with persistent EventBus subscription
- [x] 4.3 Implement `_event_consumer_loop()` for cross-turn event forwarding
- [x] 4.4 Implement `handle_prompt()` delegating to SessionPool.process_prompt()
- [x] 4.5 Implement `close_session()` with consumer cleanup
- [x] 4.6 Add `acp.use_session_pool` branch in server setup
- [x] 4.7 Ensure ACPEventConverter integration preserved
- [x] 4.8 Ensure subagent_display_mode support preserved
- [x] 4.9 Write ACP handler unit tests
- [x] 4.10 Write ACP end-to-end tests with SessionPool
- [x] 4.11 Canary deployment: validate 1% traffic
- [x] 4.12 Remove old ACP session management code (post-canary)

## 5. OpenCode Protocol Handler Migration

- [x] 5.1 Discovery: analyze state.py coupling depth
- [x] 5.2 Create `OpenCodeProtocolHandler` class skeleton in opencode_server/handler.py
- [x] 5.3 Implement `_ensure_event_consumer()` with persistent EventBus subscription
- [x] 5.4 Implement `_event_consumer_loop()` for SSE event forwarding
- [x] 5.5 Implement `handle_message()` delegating to SessionPool.process_prompt()
- [x] 5.6 Implement `close_session()` with consumer cleanup
- [x] 5.7 Add `opencode.use_session_pool` branch in server setup
- [x] 5.8 Preserve ServerState non-session functionality (skill bridge, todo callbacks)
- [x] 5.9 Preserve ensure_session() store-first behavior
- [x] 5.10 Write OpenCode handler unit tests
- [x] 5.11 Write OpenCode end-to-end tests with SessionPool
- [x] 5.12 Canary deployment: validate 1% traffic
- [x] 5.13 Remove old OpenCode session management code (post-canary)

## 6. Validation and Observability

- [x] 6.1 Implement stress test: 100 concurrent sessions
- [x] 6.2 Implement stress test: slow consumers + queue overflow
- [x] 6.3 Implement stress test: mid-turn cancellations
- [x] 6.4 Implement stress test: rapid subscribe/unsubscribe
- [x] 6.5 Implement EventBus latency benchmark (p50/p99 target < 10ms)
- [x] 6.6 Implement memory growth benchmark under load
- [x] 6.7 Implement long-running memory leak detection test
- [x] 6.8 Add monitoring metrics: active_sessions, active_turns, auto_resume_count
- [x] 6.9 Add monitoring metrics: event_bus_queue_depth, turn_latency_ms
- [x] 6.10 Create operational runbook for feature flags
- [x] 6.11 Create operational runbook for incident response
- [x] 6.12 Create operational runbook for rollback procedures
- [x] 6.13 Verify Issue #39 regression test passes
- [x] 6.14 Verify performance does not regress vs baseline
- [x] 6.15 Final integration test: all protocols + SessionPool enabled
