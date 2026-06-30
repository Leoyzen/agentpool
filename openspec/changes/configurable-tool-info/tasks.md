## 1. Config Models (`agentpool_config/`)

- [ ] 1.1 Create `agentpool_config/tool_mappings.py` with `FieldExtract`, `ContentMapping`, `LocationMapping`, and `ToolMappingConfig` Pydantic models
- [ ] 1.2 Add `tool_mappings: list[ToolMappingConfig] = []` field to `AgentsManifest` in `src/agentpool/models/manifest.py`
- [ ] 1.3 Write unit tests for config model validation (field defaults, required fields, invalid kind values)

## 2. ToolInfoRegistry (`agents/events/`)

- [ ] 2.1 Create `src/agentpool/agents/events/tool_info_registry.py` with `ToolInfoRegistry` class, `CompiledRule` dataclass, and field extraction logic
- [ ] 2.2 Implement tool name matching: exact match (case-insensitive), MCP suffix match (`match_mcp_suffix` per-rule boolean), wildcard `mcp__server__*` greedy match (matches everything after prefix including `__`)
- [ ] 2.3 Implement title template rendering with `{field_name}` placeholder substitution using `str.format_map()` with defaultdict
- [ ] 2.4 Implement content item construction: `ContentMapping(kind="diff")` → `DiffContentItem`, with `FieldExtract` for path/old_text/new_text
- [ ] 2.5 Implement location item construction: `LocationMapping` → `LocationContentItem`, with `FieldExtract` for path/line
- [ ] 2.6 Implement `ToolInfoRegistry.from_config()` — compile `list[ToolMappingConfig]` into ordered rules (user first, built-in fallback)
- [ ] 2.7 Implement `ToolInfoRegistry.builtin()` — migrate all hardcoded rules from `derive_rich_tool_info()` into `CompiledRule` objects
- [ ] 2.8 Implement `ToolInfoRegistry.derive(name, input_data) -> RichToolInfo` — main entry point with first-match-wins rule evaluation
- [ ] 2.9 Write unit tests for registry: builtin rules, user overrides, wildcard matching (including nested `__`), field fallback, title rendering, diff content, location extraction, unmatched tool fallback, `match_mcp_suffix` per-rule scope

## 3. Backward Compatibility (`agents/events/infer_info.py`)

- [ ] 3.1 Refactor `derive_rich_tool_info()` to delegate to a module-level default `ToolInfoRegistry.builtin()` instance
- [ ] 3.2 Verify existing callers of `derive_rich_tool_info()` produce identical results (no behavior change)
- [ ] 3.3 Update `agents/events/__init__.py` to export `ToolInfoRegistry`, `CompiledRule`, and config types

## 4. EventMapper Integration (`orchestrator/`)

- [ ] 4.1 Add optional `registry: ToolInfoRegistry | None = None` parameter to `EventMapper.__init__()`
- [ ] 4.2 Modify `EventMapper._emit_tool_call_start()` to call `registry.derive()` when registry is available, and populate `title`, `kind`, `content`, and `locations` directly on the `ToolCallStartEvent`
- [ ] 4.3 Handle the no-registry case: fall back to `title=f"Executing: {tool_name}"`, `kind=tool_kind_map.get(tool_name, "other")`, empty `content` and `locations` (current behavior)
- [ ] 4.4 Write unit tests for EventMapper with registry: title/kind/content/locations from registry, no-registry fallback, content is empty for non-edit tools

## 5. ACP EventConverter Update (`acp_server/`)

- [ ] 5.1 In `src/agentpool_server/acp_server/event_converter.py`, update the `ToolCallStartEvent` case to also bind `content` from the event
- [ ] 5.2 When `content` is non-empty, process items using the same conversion logic as the `ToolCallProgressEvent` handler (`DiffContentItem` → `FileEditToolCallContent`, etc.) and emit an additional `ToolCallProgress` notification after the `ToolCallStart` notification
- [ ] 5.3 Set `state.has_content = True` when diff content is processed from `ToolCallStartEvent`
- [ ] 5.4 Write unit tests: ToolCallStartEvent with diff content emits ToolCallStart + ToolCallProgress, empty content emits only ToolCallStart, conversion logic matches progress handler

## 6. Registry Wiring (`delegation/`, `agents/`)

- [ ] 6.1 In `AgentPool.__init__()`, build `ToolInfoRegistry.from_config(manifest.tool_mappings)` and store as `self._tool_info_registry`
- [ ] 6.2 Store the registry on `AgentRunContext` (e.g., `run_ctx.tool_info_registry`) so it's accessible during turn execution
- [ ] 6.3 In `NativeTurn.execute()` (turn.py:108), pass `registry=self._run_ctx.tool_info_registry` to `EventMapper.__init__()`
- [ ] 6.4 Write integration test: load a config with `tool_mappings`, verify registry is built, threaded through `AgentRunContext`, and passed to `EventMapper`

## 7. OpenCode Event Processor Update (`opencode_server/`)

- [ ] 7.1 Update `ToolCallStartEvent` handler (`_process_tool_call_start()` at event_processor.py:122) to also destructure `content`, `kind`, and `locations` from the event — currently only `title` is used. The fallback path at line 501 (`_process_pydantic_tool_call()`) can remain as-is since it calls `derive_rich_tool_info()` which delegates to the default registry and will benefit from built-in rules automatically
- [ ] 7.2 Determine how diff content items map to the OpenCode `ToolPart` model — `ToolStateRunning` currently has only `time`, `input`, `title` (no `content` field). Either: (a) add a `content` field to the OpenCode `ToolStateRunning` model, or (b) emit a separate tool state update event carrying diff content after the initial tool part is created. Investigate how OpenCode SDK represents file edits and follow that pattern
- [ ] 7.3 Write test verifying OpenCode event processor consumes `content`/`kind`/`locations` from `ToolCallStartEvent` handler and the fallback path still works via default registry

## 8. End-to-End Verification

- [ ] 8.1 Write integration test: native agent with `edit` tool → verify `ToolCallStartEvent` has correct title/kind/content/locations, ACP `EventConverter` produces `ToolCallStart` + `ToolCallProgress` with `FileEditToolCallContent`
- [ ] 8.2 Write integration test: agent with custom MCP tool mapping in YAML config → verify diff content flows through to ACP protocol
- [ ] 8.3 Run existing test suite to verify no regressions: `uv run pytest -m unit`
- [ ] 8.4 Run type checking: `uv run --no-group docs mypy src/agentpool/agents/events/tool_info_registry.py src/agentpool/orchestrator/event_mapper.py src/agentpool_server/acp_server/event_converter.py`
- [ ] 8.5 Run linter: `uv run ruff check src/agentpool/agents/events/tool_info_registry.py src/agentpool/orchestrator/event_mapper.py src/agentpool_server/acp_server/event_converter.py src/agentpool_config/tool_mappings.py`
