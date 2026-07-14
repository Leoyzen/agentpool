## ADDED Requirements

### Requirement: LoopDetectionCapability prevents infinite agent loops
A `LoopDetectionCapability` SHALL be implemented as a pdai `Capability` that tracks agent call depth and prevents infinite loops. It SHALL fire on the `wrap_node_run` hook to increment depth and on run completion to decrement. When depth exceeds a configurable `max_depth`, the capability SHALL raise a `LoopDetectionError`.

#### Scenario: Loop detected at max depth
- **WHEN** an agent with `LoopDetectionCapability(max_depth=5)` reaches depth 6
- **THEN** a `LoopDetectionError` SHALL be raised, aborting the run

#### Scenario: Depth resets between top-level runs
- **WHEN** a top-level agent run completes and a new run starts
- **THEN** the depth counter SHALL reset to 0

### Requirement: TokenBudgetCapability enforces per-run token budget
A `TokenBudgetCapability` SHALL be implemented as a pdai `Capability` that tracks token usage across all model requests within a single run. It SHALL fire on the `before_model_request` hook to check remaining budget. When the cumulative token usage exceeds the configured `max_tokens`, the capability SHALL raise a `TokenBudgetExceededError`.

#### Scenario: Token budget exceeded
- **WHEN** cumulative token usage exceeds `max_tokens=10000`
- **THEN** `TokenBudgetExceededError` SHALL be raised before the next model request

#### Scenario: Token budget tracked across multiple model requests
- **WHEN** an agent makes 3 model requests consuming 4000, 3000, 4000 tokens with `max_tokens=10000`
- **THEN** the third request SHALL raise `TokenBudgetExceededError` (cumulative 11000 > 10000)

### Requirement: ToolOutputBudgetCapability limits tool output size
A `ToolOutputBudgetCapability` SHALL be implemented as a pdai `Capability` that limits the size of individual tool outputs. It SHALL fire on the `after_tool_use` hook (or equivalent) to check output size. When a tool output exceeds `max_output_chars`, the capability SHALL truncate the output and append a truncation notice.

#### Scenario: Tool output truncated
- **WHEN** a tool returns 50000 characters and `max_output_chars=10000`
- **THEN** the output SHALL be truncated to 10000 characters with a truncation notice appended

### Requirement: DynamicContextCapability manages context window
A `DynamicContextCapability` SHALL be implemented as a pdai `Capability` that dynamically manages the context window. It SHALL fire on the `before_model_request` hook to apply context management strategies (compaction, summarization, truncation) when the message history approaches the model's context limit.

#### Scenario: Context compacted when near limit
- **WHEN** the message history exceeds 80% of the model's context window
- **THEN** the capability SHALL apply compaction to reduce the context size before the model request

### Requirement: SkillActivationCapability provides per-turn skill activation
A `SkillActivationCapability` SHALL be implemented as a pdai `Capability` that dynamically activates skills based on the current turn's context. It SHALL fire on the `before_model_request` hook to evaluate which skills are relevant and inject their instructions. This capability SHALL supersede `SkillBridgeCapability` from Phase 5.

#### Scenario: Relevant skills activated based on context
- **WHEN** an agent with `SkillActivationCapability` receives a prompt about "git commit"
- **THEN** skills matching "git" or "commit" keywords SHALL have their instructions injected into the context

#### Scenario: SkillBridgeCapability replaced
- **WHEN** `SkillActivationCapability` is attached to an agent
- **THEN** `SkillBridgeCapability` SHALL NOT be attached (they are mutually exclusive)

### Requirement: MemoryCapability provides persistent memory across turns
A `MemoryCapability` SHALL be implemented as a pdai `Capability` that persists and retrieves memory entries across turns within a session. It SHALL fire on `after_node_run` to save new memories and on `before_model_request` to inject relevant memories into the context.

#### Scenario: Memory persists across turns
- **WHEN** turn 1 saves a memory entry "user prefers Python" and turn 2 runs
- **THEN** the memory entry SHALL be available in turn 2's context

#### Scenario: Memory scoped to session
- **WHEN** session A saves a memory and session B runs
- **THEN** session B SHALL NOT have access to session A's memories (unless explicitly shared)
