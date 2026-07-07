## ADDED Requirements

### Requirement: Turn.execute() fires pre_turn and post_turn hooks via HookAwareTurn

`Turn.execute()` SHALL fire `pre_turn` hooks before the LLM/ACP prompt and `post_turn` hooks after the response completes (including on error or cancellation). This applies to native agents in both standalone and SessionPool modes (where `NativeTurn.execute()` is the convergence point), and to ACP agents in SessionPool mode (where `RunHandle.start()` → `ACPTurn.execute()`).

- `pre_turn` hooks SHALL fire after turn setup but before the LLM call / ACP prompt
- `post_turn` hooks SHALL fire in the `finally` block of `Turn.execute()`
- `post_turn` hooks SHALL fire even if the turn was cancelled or errored
- `RunHandle.start()` SHALL NOT fire any hooks — it only manages the turn loop
- A double-firing guard SHALL prevent duplicate firing when the old path (`BaseAgent._run_stream_once()`) still exists

**Known gap (future work)**: ACP agents in standalone mode use `ACPAgent._stream_events()` which does NOT call `ACPTurn.execute()`. For ACP standalone, `pre_turn`/`post_turn` hooks continue to fire in `_run_stream_once()` (retained, not removed in Phase 3). Refactoring `ACPAgent._stream_events()` to use `ACPTurn.execute()` is tracked as future work (requires `ACPAgentAPI` adapter implementing full `ACPClientProtocol`).

#### Scenario: pre_turn fires in SessionPool mode for native agent
- **WHEN** a native agent runs through SessionPool via RunHandle.start() → NativeTurn.execute()
- **THEN** pre_turn hooks fire before the LLM call in NativeTurn.execute()
- **AND** HookInput contains agent_name, session_id, and prompt

#### Scenario: post_turn fires on turn cancellation
- **WHEN** a turn is cancelled mid-execution
- **THEN** post_turn hooks fire in the finally block of Turn.execute()
- **AND** HookInput contains the cancellation context

#### Scenario: pre_turn fires for ACP agent in SessionPool
- **WHEN** an ACP agent runs through RunHandle.start() → ACPTurn.execute()
- **THEN** pre_turn hooks fire before the ACP prompt is sent

#### Scenario: RunHandle does not fire hooks
- **WHEN** a run executes through RunHandle.start()
- **THEN** RunHandle.start() SHALL NOT call any hook firing methods
- **AND** hooks are fired by Turn.execute() which RunHandle.start() calls

### Requirement: Dead pre_run/post_run code removed from _run_stream_once() for native agents

After the migration period, `BaseAgent._run_stream_once()` SHALL NOT contain `pre_run`/`post_run` (now `pre_turn`/`post_turn`) hook firing logic **for native agents**. This code is dead because `NativeTurn.execute()` handles firing (verified: `native_agent/agent.py:1154-1163` creates `NativeTurn` and calls `execute()`).

**For ACP agents**: `_run_stream_once()` hook firing SHALL be **retained** until `ACPAgent._stream_events()` is refactored to use `ACPTurn.execute()` (future work). The `hooks_fired` guard prevents double-firing during the transition period.

#### Scenario: _run_stream_once no longer fires hooks for native agents
- **WHEN** `BaseAgent._run_stream_once()` is called in standalone mode for a native agent
- **THEN** it SHALL NOT fire pre_turn or post_turn hooks directly
- **AND** hooks are fired by NativeTurn.execute() which _stream_events() calls

#### Scenario: _run_stream_once retains hooks for ACP agents
- **WHEN** `BaseAgent._run_stream_once()` is called in standalone mode for an ACP agent
- **THEN** it SHALL fire pre_turn/post_turn hooks (retained until ACP standalone refactored)
- **AND** the hooks_fired guard SHALL prevent double-firing if Turn.execute() also fires
