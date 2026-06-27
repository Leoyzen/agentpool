## ADDED Requirements

### Requirement: EventBus provides pub/sub event routing with bounded queues
The EventBus SHALL decouple event producers from consumers using asyncio queues with configurable max size.

#### Scenario: Event published to subscribers
- **WHEN** an event is published to a session with active subscribers
- **THEN** each subscriber receives a shallow copy of the event

#### Scenario: Queue overflow drops oldest event
- **WHEN** a subscriber's queue is full and a new event is published
- **THEN** the oldest event is dropped to make room for the new event

#### Scenario: Session close sends sentinel
- **WHEN** a session is closed via EventBus.close_session()
- **THEN** all subscribers for that session receive a sentinel (None) to unblock consumers

### Requirement: SessionController manages per-session agent lifecycle
The SessionController SHALL create, track, and clean up per-session agent instances with proper locking.

#### Scenario: Session creation
- **WHEN** get_or_create_session() is called with a new session_id
- **THEN** a new SessionState is created with a turn_lock and metadata

#### Scenario: Per-session agent creation
- **WHEN** get_or_create_session_agent() is called for a native agent config
- **THEN** a new agent instance is created, entered, and cached for the session

#### Scenario: Shared agent fallback for non-native types
- **WHEN** get_or_create_session_agent() is called for an ACP/Claude/AG-UI agent
- **THEN** the shared pool agent is used with a warning log

#### Scenario: Session cleanup on close
- **WHEN** close_session() is called
- **THEN** the session is marked closing, active turn completes, agent is exited, and resources are freed

#### Scenario: Session TTL expiration
- **WHEN** a session exceeds session_ttl_seconds without activity
- **THEN** the background cleanup task closes the expired session

### Requirement: TurnRunner enforces turn serialization and auto-resume
The TurnRunner SHALL execute at most one turn per session at a time and automatically resume for post-turn work.

#### Scenario: Single turn execution
- **WHEN** run_turn() is called for a session
- **THEN** the turn_lock is acquired, one turn runs, and events are published to EventBus

#### Scenario: Turn loop with auto-resume
- **WHEN** run_loop() is called with initial prompts
- **THEN** the initial turn runs followed by auto-resume turns for queued injections/prompts

#### Scenario: Concurrent turn rejection
- **WHEN** run_turn() or run_loop() is called while another turn is active on the same session
- **THEN** the second call blocks until the first turn completes

#### Scenario: Max auto-resume limit
- **WHEN** auto-resume iterations exceed max_auto_resume
- **THEN** a warning is logged and no further auto-resume turns start

#### Scenario: Cancellation preserves queued work
- **WHEN** run_loop() is cancelled via asyncio.CancelledError
- **THEN** post-turn injections remain queued for the next prompt

### Requirement: SessionPool provides high-level facade
The SessionPool SHALL combine SessionController and TurnRunner into a unified interface for protocol handlers.

#### Scenario: Prompt processing
- **WHEN** process_prompt() is called with session_id and prompts
- **THEN** run_loop() or run_turn() is executed based on auto_resume setting

#### Scenario: Prompt injection during active turn
- **WHEN** inject_prompt() is called during an active turn
- **THEN** the message is injected into the active run context

#### Scenario: Prompt injection after turn completion
- **WHEN** inject_prompt() is called after a turn completes
- **THEN** the message is queued and auto-resume is triggered

#### Scenario: Session close cleanup
- **WHEN** close_session() is called
- **THEN** session resources are released, EventBus subscriptions closed, and turn state cleaned up
