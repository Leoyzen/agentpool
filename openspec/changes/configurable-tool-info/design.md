## Context

AgentPool's `derive_rich_tool_info()` function (`src/agentpool/agents/events/infer_info.py`) infers display metadata (title, kind, diff content, file locations) from tool names and input arguments. It uses a hardcoded if/elif chain matching tool names (`edit`, `write`, `read`, `bash`, etc.) and field names (`file_path`, `old_string`, `new_string`). Third-party MCP tools with different names or schemas cannot produce rich metadata without code changes.

Separately, the diff pipeline infrastructure is fully built but disconnected:
- `DiffContentItem` exists as the internal diff representation
- `EventConverter` (ACP) converts `DiffContentItem` → `FileEditToolCallContent`
- `EventEmitter.file_edit_progress()` exists for tools to emit diffs
- `derive_rich_tool_info()` already returns `DiffContentItem` in `RichToolInfo.content`
- BUT: `EventMapper` never calls `derive_rich_tool_info()`, and the only caller (`opencode_server/event_processor.py:501`) discards `content` and only uses `title`

## Goals / Non-Goals

**Goals:**
- Replace hardcoded if/elif chain with a configurable registry that supports YAML-declared mappings
- Support wildcard MCP tool name matching and multi-field fallback extraction
- Wire the registry into `EventMapper` so `ToolCallStartEvent` carries inferred title, kind, content (including `DiffContentItem`), and locations directly
- Update ACP `EventConverter` to process `content` items from `ToolCallStartEvent` and emit `ToolCallProgress` notification alongside `ToolCallStart` when content is present
- Preserve backward compatibility at the `derive_rich_tool_info()` function level (no config → identical return values). Note: at the system level, even without user config, built-in rules cause `EventMapper` to populate `content`/`locations` on `ToolCallStartEvent` — this is a new behavior (previously these fields were always empty)

**Non-Goals:**
- Adding new content item types (only supporting existing: diff, text, file, location)
- Auto-detecting diff content from tool return values (only tool input/args are inspected)
- Per-agent tool mapping overrides (mappings are pool-level, top-level config)
- Covering external ACP agents (e.g., Goose) that send events through `acp_converters.py`, bypassing `EventMapper`

## Decisions

### Decision 1: Registry pattern over function extension

**Choice:** Introduce `ToolInfoRegistry` class with compiled rules, replacing the if/elif chain.

**Rationale:** A registry allows runtime registration, YAML configuration loading, and ordered rule matching (user rules first, built-in fallback). A function extension (e.g., plugin callbacks) would lack structured configuration and declarative YAML support.

**Alternatives considered:**
- Plugin/callback system: More flexible but over-engineered for this use case. YAML config covers 95% of needs.
- Keeping if/elif and adding a config override layer: Fragile, two code paths to maintain.

### Decision 2: Populate content directly on ToolCallStartEvent

**Choice:** `EventMapper._emit_tool_call_start()` queries the `ToolInfoRegistry` and populates `title`, `kind`, `content`, and `locations` directly on the `ToolCallStartEvent`. The `ToolCallStartEvent` already has `content: list[ToolCallContentItem]` and `locations: list[LocationContentItem]` fields (events.py:253-256). The ACP `EventConverter` is updated to process `content` from `ToolCallStartEvent` and emit a `ToolCallProgress` notification alongside `ToolCallStart` when content is present.

**Rationale:** `ToolCallStartEvent` already carries `content` and `locations` fields — they're just not populated by `EventMapper` today. Populating them directly avoids the need for a cache mechanism, a separate `ToolCallProgressEvent`, or changes to `NativeTurn.execute()`'s event loop. The ACP `EventConverter` already has `DiffContentItem` → `FileEditToolCallContent` conversion logic in its `ToolCallProgressEvent` handler (event_converter.py:581-586) — extracting it as a shared helper and calling it from the `ToolCallStartEvent` handler is a small additive change (~10 lines).

**Alternatives considered:**
- Cache `RichToolInfo` in `EventMapper` and have `NativeTurn.execute()` emit a separate `ToolCallProgressEvent` after `ToolCallStartEvent`: Adds complexity (cache, pop method, event loop changes) and risks EventBus coalescing delays (`ToolCallProgressEvent` is batchable while `ToolCallStartEvent` is immediate).
- Change `map_event()` return type to `list[RichAgentStreamEvent]`: More flexible but higher blast radius — all consumers would need updating.
- Emit progress from inside `EventMapper` via a callback: Adds coupling between EventMapper and the event bus.

### Decision 3: Top-level `tool_mappings` config field

**Choice:** `tool_mappings` is a top-level field in `AgentsManifest`, not per-agent.

**Rationale:** Tool names are generally consistent across agents (an MCP tool has the same name regardless of which agent uses it). Per-agent config would cause duplication. Pool-level initialization is simpler.

**Alternatives considered:**
- Per-agent `tools` config: More granular but redundant for the common case.
- Separate `tool_mappings.yml` file: Extra file to manage; inline YAML is simpler.

### Decision 4: Field extraction with fallback lists

**Choice:** `FieldExtract` uses `fields: list[str]` — the registry tries each field name in order, returning the first non-None value.

**Rationale:** Different tools use different field names for the same concept (`file_path` vs `path`, `old_string` vs `old_text`). Fallback lists handle this declaratively without code.

### Decision 5: Title template with simple placeholder syntax

**Choice:** Title templates use `{field_name}` placeholder syntax (Python `str.format()`-style), not Jinja2.

**Rationale:** Titles are simple strings with 1-2 field substitutions. Jinja2 adds a dependency and complexity for minimal benefit. `str.format_map()` with a default-dict handles missing fields gracefully.

### Decision 6: Built-in rules as compiled ToolRules

**Choice:** The current `derive_rich_tool_info()` logic is migrated to `BUILTIN_RULES` — a list of `CompiledRule` objects loaded into the registry by default.

**Rationale:** Ensures zero-config backward compatibility. User-defined rules take priority (first match wins), built-in rules serve as fallback.

### Decision 7: Registry wiring via AgentRunContext

**Choice:** The `ToolInfoRegistry` is built in `AgentPool.__init__()` from `manifest.tool_mappings` and stored as `self._tool_info_registry`. It is passed through the wiring chain: `AgentPool` → `AgentRunContext.tool_info_registry` → `NativeTurn.execute()` (reads from `self._run_ctx.tool_info_registry` at turn.py:108) → `EventMapper.__init__(registry=...)`.

**Rationale:** `EventMapper` is constructed inside `NativeTurn.execute()` (turn.py:108), not in `AgentPool`. The registry must be available on `AgentRunContext` so `NativeTurn` can pass it to `EventMapper`. `AgentRunContext` is a dataclass (context.py:67) — adding a `tool_info_registry: ToolInfoRegistry | None = None` field is a minimal, standard change.

**Alternatives considered:**
- Global singleton registry: Simpler but prevents per-pool customization and complicates testing.
- Pass registry directly to `EventMapper` from `AgentPool`: Not feasible — `AgentPool` doesn't construct `EventMapper` instances; `NativeTurn` does.

## Risks / Trade-offs

- **[Performance: registry lookup per tool call]** → Mitigation: Rule matching is O(n) where n = number of rules (typically <20). Negligible compared to tool execution time. Can add name → rule cache if needed.
- **[Config complexity for users]** → Mitigation: Built-in rules cover common tools. Config is only needed for custom MCP tools. Provide clear examples in docs.
- **[Event ordering: content available before tool execution]** → Mitigation: Content is populated directly on `ToolCallStartEvent` by `EventMapper._emit_tool_call_start()`, which runs before tool execution begins. The ACP `EventConverter` yields `ToolCallStart` and `ToolCallProgress` notifications synchronously from the same event handler — no async gap between them.
- **[Wildcard matching ambiguity]** → Mitigation: First-match-wins ordering. User rules before built-in rules. Document that specific rules should come before wildcards.
