## ADDED Requirements

### Requirement: TaskManager replaced by anyio.TaskGroup in BaseServer
The system SHALL replace `TaskManager` usage in `BaseServer` and all 6 protocol server subclasses with `anyio.TaskGroup`. All server classes inheriting from `BaseServer` SHALL use `anyio.create_task_group()` for task creation and lifecycle management instead of `TaskManager.create_task()`.

#### Scenario: BaseServer uses TaskGroup for task creation
- **WHEN** `BaseServer` creates a background task
- **THEN** the task is spawned via `self._task_group.start_soon(coro, name=name)`
- **AND** the task is tracked by the `TaskGroup` for lifecycle management

#### Scenario: Server shutdown awaits all tasks
- **WHEN** `BaseServer.shutdown()` is called
- **THEN** `self._task_group.__aexit__` is invoked
- **AND** all tasks spawned in the group are guaranteed to complete or be cancelled
- **AND** no tasks leak beyond the server's lifetime

#### Scenario: All protocol servers migrate successfully
- **WHEN** any protocol server (ACP, OpenCode, AG-UI, OpenAI API, MCP, A2A) starts and stops
- **THEN** task creation uses `TaskGroup.start_soon()` instead of `TaskManager.create_task()`
- **AND** existing server tests pass without modification

### Requirement: TaskManager class deprecated, not removed
The system SHALL mark `TaskManager` as deprecated by adding a `DeprecationWarning` in its `__init__()` method. The class SHALL NOT be removed — non-BaseServer instances (MessageNode, EventManager, StorageManager, PromptManager, StorageProvider, ACPConnectionManager, test harness) continue to use it until a follow-up change.

#### Scenario: TaskManager constructor emits deprecation warning
- **WHEN** `TaskManager()` is instantiated
- **THEN** a `DeprecationWarning` is emitted: "TaskManager is deprecated. Use anyio.TaskGroup for new code."
- **AND** the instance functions normally

#### Scenario: BaseServer and protocol servers no longer import TaskManager
- **WHEN** `from agentpool.utils.tasks import TaskManager` is searched in `BaseServer` and 6 protocol server files
- **THEN** no import is found

### Requirement: Delayed execution via anyio.sleep
The system SHALL replace delayed task execution (formerly `TaskManager.create_task(coro, delay=...)`) with `anyio.sleep(delay)` followed by `TaskGroup.start_soon(coro)`.

#### Scenario: Delayed task execution
- **WHEN** a task needs to be executed after a delay
- **THEN** the code spawns a wrapper in the `TaskGroup` that first calls `anyio.sleep(delay)` then runs the original coroutine
