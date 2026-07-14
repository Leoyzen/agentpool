## 1. AggregatingResourceProvider: add/remove provider support

- [x] 1.1 Add `add_provider(provider: ResourceProvider) -> None` method that appends to `self.providers` and emits `skills_changed` signal
- [x] 1.2 Add `remove_provider(provider: ResourceProvider) -> None` method that removes from `self.providers` and emits `skills_changed` signal

## 2. SkillURIResolver: unregister support

- [x] 2.1 Add `unregister_provider(name: str) -> None` method to remove a provider from `self._providers` dict (pre-existing at uri_resolver.py:317)

## 3. AgentPool: register/unregister skill providers

- [x] 3.1 Add `register_skill_provider(provider: ResourceProvider) -> None` that calls `self._skill_provider.add_provider(provider)` and `self._skill_resolver.register_provider(provider.name, provider)`
- [x] 3.2 Add `unregister_skill_provider(provider: ResourceProvider) -> None` that calls `self._skill_provider.remove_provider(provider)` and `self._skill_resolver.unregister_provider(provider.name)`
- [x] 3.3 Handle edge case: `register_skill_provider` called before `_setup_skills_provider()` — providers should be buffered and added when aggregator is created

## 4. ACPSession: wire session MCP providers into pool

- [x] 4.1 In `initialize_mcp_servers()`, after each `MCPResourceProvider` is created and stored in `self.session_mcp_providers`, call `pool.register_skill_provider(provider)`
- [x] 4.2 On session teardown/close, iterate `self.session_mcp_providers` and call `pool.unregister_skill_provider(provider)` for each
- [x] 4.3 Ensure session teardown happens reliably (use `try/finally` or context manager) so providers are always unregistered (existing `close()` method with try/finally pattern already handles this)

## 5. Tests

- [x] 5.1 Unit test: `AggregatingResourceProvider.add_provider()` and `remove_provider()` emit `skills_changed` and affect `get_skills()` output
- [x] 5.2 Unit test: `SkillURIResolver.unregister_provider()` removes provider from search
- [x] 5.3 Unit test: `AgentPool.register_skill_provider()` makes skills visible to `_skill_provider.get_skills()`
- [x] 5.4 Unit test: `AgentPool.unregister_skill_provider()` removes skills
- [ ] 5.5 Integration test: ACP session with MCP-over-ACP skills → skills appear in `<available-skills>` XML via `SkillsInstructionProvider` (requires full ACP server setup — deferred)
- [ ] 5.6 Integration test: Session close → skills removed from aggregator (requires full ACP server setup — deferred)
- [x] 5.7 Run existing test suite: `uv run pytest` — ensure no regressions

## 6. Verification

- [x] 6.1 Run `uv run ruff check src/` and `uv run ruff format --check src/` — pre-existing issues only, no new issues
- [x] 6.2 Run `uv run --no-group docs mypy src/` — pre-existing errors only, no new issues
- [x] 6.3 Run `uv run pytest` — 815 passed, 2 pre-existing failures (unrelated), 2 skipped
