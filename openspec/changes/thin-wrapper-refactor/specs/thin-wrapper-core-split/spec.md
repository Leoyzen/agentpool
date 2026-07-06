## ADDED Requirements

### Requirement: orchestrator/core.py split into three focused modules
The system SHALL split `orchestrator/core.py` into three dedicated modules: `event_bus.py` (containing `EventBus`, `EventEnvelope`, and related helpers), `session_controller.py` (containing `SessionController`, `SessionState`, `RunHandle`, and related types), and `session_pool.py` (containing `SessionPool`, `SessionPoolConfig`, and `SessionPoolMetrics`).

#### Scenario: EventBus class accessible from event_bus module
- **WHEN** any code imports `EventBus` from `agentpool.orchestrator.event_bus`
- **THEN** the import SHALL succeed and return the `EventBus` class

#### Scenario: SessionController class accessible from session_controller module
- **WHEN** any code imports `SessionController` from `agentpool.orchestrator.session_controller`
- **THEN** the import SHALL succeed and return the `SessionController` class

#### Scenario: SessionPool class accessible from session_pool module
- **WHEN** any code imports `SessionPool` from `agentpool.orchestrator.session_pool`
- **THEN** the import SHALL succeed and return the `SessionPool` class

### Requirement: orchestrator/__init__.py re-exports all moved symbols
The `orchestrator/__init__.py` module SHALL re-export `EventBus`, `SessionController`, `SessionPool`, `RunHandle`, `SessionState`, `EventEnvelope`, and all other public symbols that were previously importable from `orchestrator.core`. Existing imports from `agentpool.orchestrator` or `agentpool.orchestrator.core` SHALL continue to work without modification.

#### Scenario: Existing import from orchestrator.core still works
- **WHEN** code imports `EventBus` from `agentpool.orchestrator.core`
- **THEN** the import SHALL succeed via re-export from `event_bus.py`

#### Scenario: No circular imports after split
- **WHEN** the three new modules are loaded
- **THEN** no circular import errors SHALL occur, with `event_bus.py` having no dependencies on `session_controller.py` or `session_pool.py`

### Requirement: orchestrator/core.py file removed or reduced to re-exports
After the split, `orchestrator/core.py` SHALL either be removed entirely or reduced to a thin re-export module that imports from the three new files. No class or function definitions SHALL remain in `core.py`.

#### Scenario: core.py contains no class definitions
- **WHEN** `orchestrator/core.py` is inspected after the split
- **THEN** it SHALL contain zero `class` statements and zero `def` statements (only import re-exports)

### Requirement: Each module has clear single responsibility
The `event_bus.py` module SHALL contain only event bus infrastructure (publishing, subscribing, replay buffers, overflow handling). The `session_controller.py` module SHALL contain only per-session lifecycle management (session creation, run tracking, agent resolution). The `session_pool.py` module SHALL contain only pool-level session management (session pooling, metrics, cleanup scheduling).

#### Scenario: event_bus.py has no SessionController references
- **WHEN** `event_bus.py` is inspected
- **THEN** it SHALL NOT import or reference `SessionController`, `SessionPool`, or `RunHandle` except via an optional callback interface

#### Scenario: session_controller.py has no SessionPool references
- **WHEN** `session_controller.py` is inspected
- **THEN** it SHALL NOT import or reference `SessionPool` or `SessionPoolConfig`
