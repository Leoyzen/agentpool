## Why

AgentPool is the only framework in the ecosystem that embeds a `provider` segment in `skill://` URIs (`skill://{provider}/{name}`). The upstream standard (FastMCP) and the Agent Skills Spec both use `skill://{name}` — a flat namespace without provider. The provider segment conflates two concerns (identity vs. routing) and forces LLMs to understand internal infrastructure details that should be transparent. Removing it aligns AgentPool with the ecosystem standard and simplifies the URI scheme for both developers and models.

## What Changes

- **BREAKING**: `skill://{provider}/{name}` URI format is removed. Skills are identified by `skill://{name}` or bare `name`.
- `Skill.safe_uri` returns `skill://{name}` instead of `skill://local/{name}`.
- `ResolvedSkillURI.parse()` no longer extracts a `provider` field from the URI netloc.
- `SkillURIResolver.resolve()` always searches all registered providers (priority-ordered: local > MCP) instead of routing by provider name.
- `SkillCommand.resolved_skill_uri` returns `skill://{name}` without a provider fallback.
- `command_registry` no longer reads `metadata["provider"]` to construct display URIs.
- MCP resource skills get `skill_path=PurePosixPath(f"skill://{skill_name}")` without the MCP server name prefix.
- The `"provider"` metadata key on MCP skills is removed.
- Provider name validation infrastructure (`_is_valid_provider_name`, `_validate_provider_name`, `PROVIDER_NAME_PATTERN`, `MAX_PROVIDER_NAME_LENGTH`) is retained for `register_provider()` dict key safety — no changes needed.
- Skill name disambiguation follows priority order: local skills first, then MCP skills. First match wins.

## Capabilities

### New Capabilities

- `flat-skill-uris`: Skills are identified by flat `skill://{name}` URIs without a provider segment. The resolver handles disambiguation internally via provider priority ordering (local > MCP, first-wins).

### Modified Capabilities

<!-- None — no existing specs cover skill URI format. -->

## Impact

- **Source files**: `skill.py`, `command.py`, `command_registry.py`, `uri_resolver.py`, `mcp_provider.py`, `skills_instruction.py`, `skills.py` (toolsets)
- **Tests**: ~15 test files with hardcoded `skill://provider/...` URI assertions
- **No API changes** to `ResourceProvider`, `AgentPool`, or protocol servers
- **No dependency changes**
- **No storage migration needed** (URIs are ephemeral, not persisted)
