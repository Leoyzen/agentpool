## 1. Fix server→cli/commands violations (8)

- [ ] 1.1 Audit 8 `agentpool_server` → `agentpool_cli`/`agentpool_commands` import violations
- [ ] 1.2 Fix each violation — move shared code to core or invert dependency
- [ ] 1.3 Remove corresponding `ignore_imports` entries

## 2. Fix config→core violations (72)

- [ ] 2.1 Audit 72 `agentpool_config` → `agentpool` import violations — categorize by type (type refs, runtime imports, util calls)
- [ ] 2.2 Fix type-reference violations — use `TYPE_CHECKING` imports or move types to a neutral package
- [ ] 2.3 Fix runtime-import violations — move code or invert dependency
- [ ] 2.4 Remove corresponding `ignore_imports` entries

## 3. Tighten import-linter config

- [ ] 3.1 Remove `allow_indirect_imports = true` from all contracts
- [ ] 3.2 Verify `lint-imports` passes with zero violations
- [ ] 3.3 Add `lint-imports` to `.github/workflows/` CI pipeline

## 4. Verify

- [ ] 4.1 Run `uv run lint-imports` — zero violations
- [ ] 4.2 Run `uv run pytest` — full test suite passes after import fixes
