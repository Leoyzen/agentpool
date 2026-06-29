## ADDED Requirements

### Requirement: EventMapper uses ToolInfoRegistry for tool call metadata
The `EventMapper._emit_tool_call_start()` SHALL query the `ToolInfoRegistry` (when available) to derive `RichToolInfo` from the tool name and input arguments. The derived `title`, `kind`, `content`, and `locations` SHALL be included directly in the `ToolCallStartEvent`. The `ToolCallStartEvent` already has `content: list[ToolCallContentItem]` and `locations: list[LocationContentItem]` fields — these SHALL be populated from the registry result.

#### Scenario: EventMapper infers rich info from registry
- **WHEN** a `FunctionToolCallEvent` arrives for tool `"edit"` with input `{"file_path": "/a.py", "old_string": "x", "new_string": "y"}`
- **AND** the `EventMapper` has a `ToolInfoRegistry` configured
- **THEN** the emitted `ToolCallStartEvent` SHALL have `title="Edit /a.py"` (or equivalent from registry)
- **AND** the `ToolCallStartEvent` SHALL have `kind="edit"`
- **AND** the `ToolCallStartEvent` SHALL have `content` containing a `DiffContentItem` with `path="/a.py"`, `old_text="x"`, `new_text="y"`
- **AND** the `ToolCallStartEvent` SHALL have `locations` containing a `LocationContentItem` with `path="/a.py"`

#### Scenario: EventMapper without registry falls back to defaults
- **WHEN** a `FunctionToolCallEvent` arrives and the `EventMapper` has no `ToolInfoRegistry`
- **THEN** the emitted `ToolCallStartEvent` SHALL use `title="Executing: {tool_name}"` and `kind="other"`
- **AND** `content` and `locations` SHALL be empty lists

#### Scenario: Tool with no diff content has empty content list
- **WHEN** a `FunctionToolCallEvent` arrives for tool `"bash"` (no content items in `RichToolInfo`)
- **THEN** the emitted `ToolCallStartEvent` SHALL have `content=[]`
- **AND** the `ToolCallStartEvent` SHALL still carry the inferred `title` and `kind`

### Requirement: EventMapper receives registry via constructor
The `EventMapper.__init__()` SHALL accept an optional `registry: ToolInfoRegistry | None = None` parameter. When provided, the registry SHALL be used for all tool info derivation. When `None`, the EventMapper SHALL fall back to default behavior (`title="Executing: {tool_name}"`, `kind` from `tool_kind_map`). The registry SHALL be passed from `NativeTurn.execute()` which receives it via `AgentRunContext`.

#### Scenario: Registry passed to EventMapper
- **WHEN** `NativeTurn.execute()` creates an `EventMapper`
- **AND** the `AgentRunContext` has a `tool_info_registry` attribute
- **THEN** the registry SHALL be passed to `EventMapper.__init__(registry=...)`

#### Scenario: No registry in context
- **WHEN** `NativeTurn.execute()` creates an `EventMapper`
- **AND** the `AgentRunContext` has no `tool_info_registry` (or it is `None`)
- **THEN** `EventMapper` SHALL be constructed with `registry=None`
- **AND** default behavior SHALL be used

### Requirement: ACP EventConverter processes content from ToolCallStartEvent
The ACP `EventConverter` SHALL extract `content` items from `ToolCallStartEvent` (in addition to the existing `locations` extraction). When `content` is non-empty, the converter SHALL emit a `ToolCallProgress` notification (in addition to the `ToolCallStart` notification) with the converted content items. The content conversion logic SHALL be the same as the existing `ToolCallProgressEvent` handler: `DiffContentItem` → `FileEditToolCallContent`, `TextContentItem` → `ContentToolCallContent.text`, etc.

#### Scenario: ToolCallStartEvent with diff content
- **WHEN** the ACP `EventConverter` receives a `ToolCallStartEvent` with `content=[DiffContentItem(path="/a.py", old_text="x", new_text="y")]`
- **THEN** the converter SHALL emit a `ToolCallStart` notification (as before)
- **AND** SHALL also emit a `ToolCallProgress` notification with `content=[FileEditToolCallContent(path="/a.py", old_text="x", new_text="y")]`
- **AND** SHALL set `state.has_content = True`

#### Scenario: ToolCallStartEvent with empty content
- **WHEN** the ACP `EventConverter` receives a `ToolCallStartEvent` with `content=[]`
- **THEN** the converter SHALL emit only the `ToolCallStart` notification (as before)
- **AND** SHALL NOT emit an additional `ToolCallProgress` notification

#### Scenario: Content conversion reuses existing logic
- **WHEN** the ACP `EventConverter` processes `content` items from `ToolCallStartEvent`
- **THEN** the conversion logic SHALL be identical to the `ToolCallProgressEvent` handler
- **AND** `DiffContentItem` SHALL become `FileEditToolCallContent`
- **AND** `TextContentItem` SHALL become `ContentToolCallContent.text`
- **AND** `LocationContentItem` SHALL become `ToolCallLocation`
