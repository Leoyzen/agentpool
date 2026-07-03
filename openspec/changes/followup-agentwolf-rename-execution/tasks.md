## 1. Execute rename

- [ ] 1.1 Verify all Phase 4-7 follow-up changes are merged
- [ ] 1.2 Verify clean working tree
- [ ] 1.3 Run `python scripts/rename_to_agentwolf.py` (no --dry-run)
- [ ] 1.4 Commit as single atomic commit: `refactor: rename agentpool to agentwolf`

## 2. Verify

- [ ] 2.1 Run `uv sync` — all dependencies resolve with new package names
- [ ] 2.2 Run `uv run pytest` — full test suite passes with new package names
- [ ] 2.3 Run `uv run mypy src/` — type checking passes
- [ ] 2.4 Run `uv run ruff check src/` — linting passes
- [ ] 2.5 Verify `agentwolf --version` CLI command works
- [ ] 2.6 Verify `agentwolf serve-acp config.yml` works with a sample config
- [ ] 2.7 Verify no `agentpool` references remain (except openspec/changes/ historical artifacts)
