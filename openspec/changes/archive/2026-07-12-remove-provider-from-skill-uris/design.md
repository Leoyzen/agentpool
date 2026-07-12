## Context

AgentPool currently uses `skill://{provider}/{name}` as its skill URI format. The `provider` segment serves as a routing hint for the `SkillURIResolver` to look up the correct `ResourceProvider` (local, MCP server A, MCP server B, etc.). This format is unique to AgentPool — the upstream standard (FastMCP) and the Agent Skills Spec both use `skill://{name}` with a flat namespace.

The `SkillURIResolver.resolve()` method already supports provider-less resolution: when a bare name or a `skill://name/references/...` URI is passed, it searches all registered providers. The MCP server communication layer already uses provider-less URIs internally (`skill://{skill_name}/SKILL.md`). The provider segment exists only for client-side routing within AgentPool.

The change is to remove the provider segment from all externally-visible URIs and make the resolver always search all providers in priority order. This is a simplification: the resolver's existing provider-less code path becomes the only code path.

## Goals / Non-Goals

**Goals:**
- Remove the `provider` segment from `skill://` URIs everywhere
- Simplify `ResolvedSkillURI` by removing the `provider` field
- Make `SkillURIResolver.resolve()` always search all providers (priority-ordered)
- Align AgentPool's URI format with FastMCP and the broader ecosystem
- Remove provider name validation from URI parsing (validation functions retained for `register_provider()` dict key safety)

**Non-Goals:**
- Changing how `MCPResourceProvider.get_skills()` discovers skills (already works correctly)
- Changing the ACP protocol or any server protocol
- Changing how skills are injected into agent prompts (only the URI value changes)
- Adding a `_manifest` resource pattern (separate concern)
- Removing the `mcp://` scheme for prompt-as-skill (separate concern)
- Changing `SkillURIResolver.register_provider()` / `unregister_provider()` (providers are still registered, just not reflected in URIs)

## Decisions

### Decision 1: Remove provider from URIs entirely (no optional fallback)

**Choice**: Remove the provider segment completely. `skill://{name}` is the only format. No backward compatibility for `skill://{provider}/{name}`.

**Rationale**: The user explicitly stated no backward compatibility is needed ("大模型拿到什么就会访问什么"). Adding an optional fallback would preserve complexity without value. The LLM receives `skill://{name}` in prompts and will call `load_skill("skill://{name}")` — there is no legacy URI to support.

**Alternatives considered**:
- *Keep provider as optional (e.g., `skill://{name}` default, `skill://{provider}/{name}` for explicit disambiguation)* — rejected: adds complexity for a use case that doesn't exist. If disambiguation is needed later, it can be added as a new feature.
- *Keep the provider in the resolver internally but hide it from URIs* — rejected: adds indirection without value. The resolver already searches all providers.

### Decision 2: Resolver searches all providers in priority order

**Choice**: `SkillURIResolver.resolve()` always iterates all registered providers, returning the first match. Provider registration order determines priority (local providers registered first → local wins).

**Rationale**: This is already the behavior for bare-name resolution (lines 347-362 of `uri_resolver.py`). We're making it the only behavior. The `register_provider()` method already preserves insertion order via a `dict`.

**Alternatives considered**:
- *Add explicit priority scores to providers* — rejected: YAGNI. Insertion order is sufficient. Priority scores can be added later if needed.

### Decision 3: Keep `SkillURIResolver.register_provider()` / `unregister_provider()`

**Choice**: Keep the provider registration API unchanged. Providers are still registered by name for lifecycle management (register on session start, unregister on teardown). The name is used as a dict key for `unregister_provider()`, not for URI routing.

**Rationale**: The `wire-session-mcp-skills-to-instruction-provider` change added dynamic provider registration for session-level MCP servers. This API is still needed for session lifecycle management. We only remove the provider from URIs, not from the internal registry.

**Alternatives considered**:
- *Remove `register_provider(name, ...)` in favor of a list-based API* — rejected: the name parameter is useful for `unregister_provider()` and debugging. Removing it would require a different lookup mechanism.

### Decision 4: `mcp://` scheme for prompt-as-skill remains unchanged

**Choice**: Keep the `mcp://{name}/prompts/{prompt.name}` scheme for MCP prompts that are surfaced as skills. This is a different URI scheme (`mcp://`, not `skill://`) and serves a different purpose (prompt identification, not skill identification).

**Rationale**: The `mcp://` scheme is an internal identifier for MCP resources, not a user-facing skill URI. Changing it is out of scope for this change.

### Decision 5: Handle `urlparse("skill://name")` decomposition

**Choice**: Restructure `ResolvedSkillURI.parse()` to handle the case where `urlparse("skill://name")` puts the skill name in `netloc` with `path=""`. When `path` is empty after removing the leading `/`, treat `netloc` as the skill name.

**Rationale**: `urlparse("skill://code-review")` returns `ParseResult(scheme='skill', netloc='code-review', path='', ...)`. The current parser raises `ValueError("URI path is empty")` on empty path. After removing provider, the name comes from either `netloc` (when path is empty) or `path[0]` (when path has segments). This is a mechanical parsing change.

**Alternatives considered**:
- *Use regex-based parsing instead of `urlparse`* — rejected: adds complexity, `urlparse` is standard library and handles URL encoding.
- *Always put name in path (`skill:///code-review` with triple slash)* — rejected: non-standard, ugly, breaks the common `skill://name` pattern.

### Decision 6: Keep all provider name validation infrastructure as-is

**Choice**: Keep `_validate_provider_name()`, `_is_valid_provider_name()`, `PROVIDER_NAME_PATTERN`, and `MAX_PROVIDER_NAME_LENGTH` unchanged. No validation code is removed.

**Rationale**: The `register_provider(name, provider)` and `unregister_provider(name)` APIs use the name as a dict key. The validation functions ensure dict keys are well-formed, which is still needed even though provider names are no longer in URIs. The implementation is identical to what it was before — it just serves a different purpose now (dict key safety vs. URI validation).

### Decision 7: Log warning on skill name collisions

**Choice**: When `SkillURIResolver.resolve()` finds a skill from a lower-priority provider that would have been shadowed by a higher-priority match, log a `logger.debug()` message.

**Rationale**: Silent shadowing is a debugging hazard. A debug-level log costs nothing and helps diagnose unexpected behavior when two providers expose same-named skills.

## Risks / Trade-offs

- **Risk**: Skill name collisions between providers become silently resolved (first-wins) rather than disambiguated by provider name
  - **Mitigation**: Skill name collisions are already rare in practice. The user explicitly chose first-wins behavior. A `logger.debug()` warning is emitted when a collision is detected. If disambiguation is needed later, it can be added as a new feature (e.g., `load_skill` with a `source` parameter).

- **Risk**: Resolver performance degrades from O(1) to O(n) provider lookups
  - **Mitigation**: The number of registered providers is small (typically 1-5). The search is over an in-memory list of skills that are already cached by each provider. No network calls are involved in the search.

- **Risk**: `resolved.provider` access in `skills.py` raises `AttributeError` after field removal
  - **Mitigation**: Remove the conditional blocks at `skills.py` lines 333-336 and 355-356 entirely. The display URI is already covered by `skill.safe_uri` on the preceding lines (332, 352). This must be done in the same commit as the `ResolvedSkillURI` field removal.

- **Risk**: Existing tests that assert `provider` in `ResolvedSkillURI` or in display output will fail
  - **Mitigation**: These tests need to be updated as part of implementation. This is expected and accounted for in the tasks. All files with `skill://provider/...` patterns have been identified.

- **Risk**: `urlparse("skill://name")` decomposition edge case — name lands in `netloc` with empty `path`
  - **Mitigation**: Restructure `parse()` to handle this case explicitly (Decision 5). Write dedicated tests for `skill://name` (single segment, no path) before any other changes.

- **Trade-off**: `ResolvedSkillURI.provider` is removed, which means downstream code that accessed this field (e.g., `skills.py` tool output formatting) must be updated
  - **Acceptable**: The `provider` field was only used for display purposes. Removing it simplifies the display and aligns with the new URI format.
