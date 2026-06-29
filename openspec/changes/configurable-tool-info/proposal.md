## Why

Tool rich info inference (title, kind, diff content, file locations) is hardcoded in `derive_rich_tool_info()` as an if/elif chain matching tool names and field names. Third-party MCP tools with different names or input schemas cannot produce diff content or rich UI metadata without code changes. Additionally, the diff pipeline infrastructure (`DiffContentItem` → `FileEditToolCallContent` via `EventConverter`) is fully built but never wired into the `EventMapper`, so no tool currently emits diff content through the event stream.

## What Changes

- Introduce a `ToolInfoRegistry` that replaces the hardcoded `derive_rich_tool_info()` if/elif chain with a configurable rule system
- Add `tool_mappings` top-level YAML configuration field in `AgentsManifest` for declaring tool name → rich info mappings (kind, title template, content/diff extraction, location extraction)
- Support wildcard MCP tool name matching (`mcp__server__*`) and multi-field fallback extraction (`fields: ["file_path", "path"]`)
- Wire `ToolInfoRegistry` into `EventMapper._emit_tool_call_start()` so that `ToolCallStartEvent` carries the inferred title, kind, locations, AND content (including `DiffContentItem`) directly — the `ToolCallStartEvent` already has `content` and `locations` fields
- Update ACP `EventConverter` to process `content` items from `ToolCallStartEvent` (same `DiffContentItem` → `FileEditToolCallContent` conversion already used for `ToolCallProgressEvent`) and emit a `ToolCallProgress` notification alongside `ToolCallStart` when content is present
- Refactor `derive_rich_tool_info()` to delegate to the registry while preserving backward compatibility
- Update OpenCode `event_processor.py` to consume `rich_info` from `ToolCallStartEvent` fields (title, kind, content) rather than independently calling `derive_rich_tool_info()`
- Initialize `ToolInfoRegistry` in `AgentPool` from manifest `tool_mappings` config, thread it through `Agent` → `NativeTurn` → `EventMapper`
- ACP-sourced tool calls (external ACP agents like Goose) are out of scope — they send their own tool call events through `acp_converters.py`

## Capabilities

### New Capabilities
- `tool-info-mapping`: Configurable registry that maps tool names and input schemas to rich display metadata (title, kind, content items including diffs, file locations) via YAML configuration

### Modified Capabilities
- `unified-event-routing`: `EventMapper` SHALL populate `content` and `locations` fields on `ToolCallStartEvent` from the `ToolInfoRegistry`. ACP `EventConverter` SHALL process `content` items from `ToolCallStartEvent` to emit `ToolCallProgress` notifications with `FileEditToolCallContent` when diff content is present.

## Impact

- **New files**: `agentpool_config/tool_mappings.py` (config models), `src/agentpool/agents/events/tool_info_registry.py` (registry)
- **Modified core files**: `src/agentpool/agents/events/infer_info.py` (delegate to registry), `src/agentpool/orchestrator/event_mapper.py` (use registry, populate content/locations on start event), `src/agentpool/agents/native_agent/turn.py` (pass registry to EventMapper)
- **Modified config**: `src/agentpool/models/manifest.py` (add `tool_mappings` field), `src/agentpool/delegation/pool.py` (initialize registry, thread to agents)
- **Modified protocol layer**: `src/agentpool_server/acp_server/event_converter.py` (process content from ToolCallStartEvent), `src/agentpool_server/opencode_server/event_processor.py` (consume rich info from start event instead of re-deriving)
- **No breaking changes**: `derive_rich_tool_info()` signature preserved, built-in rules loaded by default, `tool_mappings` config is optional
- **ACP agents out of scope**: External ACP agents (Goose, etc.) send their own tool call events through `acp_converters.py` and do not go through `EventMapper`. This change only affects native agents.
