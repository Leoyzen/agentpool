## 1. ResolvedSkillURI: Remove provider field

- [ ] 1.1 Remove `provider` field from `ResolvedSkillURI` dataclass in `src/agentpool/skills/uri_resolver.py`
- [ ] 1.2 Restructure `ResolvedSkillURI.parse()` to handle flat URIs: for ALL `skill://` URIs, `urlparse()` puts the skill name in `netloc` and the path in `path`. Remove the netloc → provider extraction. Always treat `parsed.netloc` as the skill name and `parsed.path` (stripped of leading `/`) as the reference path. Examples: `skill://code-review` → netloc=`code-review`, path=`""` → skill_name=`code-review`, reference_path=`None`. `skill://code-review/references/guide.md` → netloc=`code-review`, path=`/references/guide.md` → skill_name=`code-review`, reference_path=`references/guide.md`.
- [ ] 1.3 Keep all provider name validation infrastructure as-is (`PROVIDER_NAME_PATTERN`, `MAX_PROVIDER_NAME_LENGTH`, `_is_valid_provider_name()`, `_validate_provider_name()`) — they are still used by `register_provider()` for dict key safety. No code changes needed for validation functions.
- [ ] 1.4 Update `ResolvedSkillURI.parse()` docstring and examples to reflect flat URI format (remove `provider` from all examples)

## 2. SkillURIResolver: Simplify resolve()

- [ ] 2.1 Remove provider-based routing branch in `resolve()` (lines 364-426) — keep only the provider-less search (lines 347-362) as the unified resolution path
- [ ] 2.2 Ensure fuzzy match (name alternatives with -/_ swapping) still works across all providers in priority order
- [ ] 2.3 Remove `get_provider()` and `list_providers()` methods from `SkillURIResolver` (no longer needed after task 7.2 replaces the provider-iteration loop in `list_skills()`)
- [ ] 2.4 Add `logger.debug()` warning when a skill name collision is detected (lower-priority provider's skill is shadowed)
- [ ] 2.5 Update `resolve()` docstring to reflect flat URI-only resolution

## 3. Skill.safe_uri: Remove hardcoded "local"

- [ ] 3.1 Change `Skill.safe_uri` in `src/agentpool/skills/skill.py` to return `f"skill://{self.name}"` instead of `f"skill://local/{self.name}"`
- [ ] 3.2 Verify virtual (MCP) skills still return their existing `skill_path` as-is

## 4. SkillCommand: Remove "local" fallback

- [ ] 4.1 Change `SkillCommand.resolved_skill_uri` in `src/agentpool/skills/command.py` to return `f"skill://{self.name}"` instead of `f"skill://local/{self.name}"`

## 5. CommandRegistry: Remove provider from URI construction

- [ ] 5.1 In `CommandRegistry._sync_from_skill_provider()` at `src/agentpool/skills/command_registry.py`, remove `provider_name` extraction from metadata (line 214)
- [ ] 5.2 Change skill URI construction to use skill name only: `f"skill://{skill.name}"` instead of `f"skill://{provider_name}/{skill.name}"`

## 6. MCPResourceProvider: Remove provider from skill paths and metadata

- [ ] 6.1 In `_get_resource_skills()` at `src/agentpool/resource_providers/mcp_provider.py`, change `skill_path` construction from `PurePosixPath(f"skill://{self.name}/{skill_name}")` to `PurePosixPath(f"skill://{skill_name}")` (line 564)
- [ ] 6.2 Remove `"provider": self.name` from skill metadata dict (line 569)
- [ ] 6.3 Remove `"provider": self.name` from prompt-based skill metadata (line 487)

## 7. Skills toolset: Remove provider from display and dead code

- [ ] 7.1 **Remove** the conditional blocks at `skills.py` lines 333-336 and 355-356 that access `resolved.provider`. After task 1.1 removes the field, these will raise `AttributeError`. The display URI is already covered by `skill.safe_uri` on lines 332 and 352 — no replacement needed.
- [ ] 7.2 Replace the provider-iteration loop in `list_skills()` (lines 426-437) with direct flat URI display. Replace the entire `for provider_name in resolver.list_providers():` block with `lines.append(f"  - URI: skill://{skill.name}")`.
- [ ] 7.3 Remove dead code: `_resolved_reference_path` getattr checks in `_load_skill()` (lines 284, 321) — these become no-ops after the provider fallback branch is removed in task 2.1.
- [ ] 7.4 Update `SKILL_USAGE_GUIDANCE` constant (lines 27-28) to use `skill://skill-name` format instead of `skill://provider/skill-name`
- [ ] 7.5 Update usage guidance docstring examples (lines 36, 454, 459) to use flat URI format (e.g., `skill://python-expert` instead of `skill://local/python-expert`)

## 8. SkillsInstructionProvider: Verify safe_uri changes propagate

- [ ] 8.1 Verify that `_format_skill_metadata()` and `_format_skill_full()` in `src/agentpool/resource_providers/skills_instruction.py` automatically reflect the new `safe_uri` format (no code changes expected — just verification)
- [ ] 8.2 Verify that `to_prompt()` in `skill.py` (line 269) automatically reflects the new `safe_uri` format

## 9. Tests

- [ ] 9.1 Update `tests/skills/test_uri_resolver.py` — remove provider-related test cases (14 `skill://local/` occurrences + 5 provider validation test functions), add flat URI tests including `urlparse("skill://name")` decomposition
- [ ] 9.2 Update `tests/toolsets/test_load_skill_uri.py` — replace `skill://pool_mcp_scratchpad/...`, `skill://local/...` with `skill://...`
- [ ] 9.3 Update `tests/skills/test_mcp_skills_integration.py` — remove `skill://mcp_provider/...` patterns, remove `mock_resolver.get_provider` mock, remove `metadata["provider"]` assertions
- [ ] 9.4 Update `tests/resource_providers/test_mcp_provider_skills.py` — verify flat `skill://{name}` in skill paths, remove `metadata.get("provider")` assertions (line 203)
- [ ] 9.5 Update `tests/resource_providers/test_mcp_skill_uri_normalization.py` — replace `skill://mcp/...`, `skill://test-mcp/...` patterns
- [ ] 9.6 Update `tests/skills/test_scratchpad_skill_reference_redflag.py` — replace `skill://pool_mcp_scratchpad/...`
- [ ] 9.7 Update `tests/performance/test_skill_performance.py` — replace `skill://local/...`, `skill://provider-name/...`
- [ ] 9.8 Update `tests/delegation/test_pool_skills.py` — replace `skill://mcp/some-skill`
- [ ] 9.9 Update `tests/resource_providers/test_skills_instruction.py` — update expected URI in XML output to `skill://skill1/`
- [ ] 9.10 Update `tests/server/opencode/test_skill_bridge.py` — replace `skill://local/...`
- [ ] 9.11 Update `tests/integration/test_skill_resolution.py` — replace `skill://local/...`
- [ ] 9.12 Update `tests/skills/test_exceptions.py` — replace `skill://local/skill/...` pattern (line 153)
- [ ] 9.13 Update `tests/toolsets/test_package_scoped_skills.py` — replace `skill://scratchpad/...` (line 110)
- [ ] 9.14 Run full test suite: `uv run pytest` — all tests pass

## 10. Verification

- [ ] 10.1 Run `uv run ruff check src/` — no new lint issues
- [ ] 10.2 Run `uv run --no-group docs mypy src/` — no new type errors
- [ ] 10.3 Run `uv run pytest` — all tests pass, no regressions
