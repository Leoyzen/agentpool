## Why

Current test suite has 348 failures and 9 errors. After fixing 20 slowest tests with `@pytest.mark.slow`, the remaining failures fall into ~12 categories. This change removes tests for a deprecated behavior (skill prefix), ignores standalone/SessionPool tests (intentional architecture change), and fixes the remaining broken tests to bring the suite back to green.

## What Changes

- **Remove** skill-prefix mismatch tests (Category 5: ~7 tests) — behavior no longer required
- **Ignore** standalone/SessionPool tests (Category 1: ~120 tests) — `_execute_direct()` fallback intentionally removed
- **Fix** ProviderCurrentConfig.headers missing (Category 2: ~6 tests)
- **Fix** FakeManifest missing `acp` attribute (Category 3: ~8 tests)
- **Fix** apprise `ModuleNotFoundError` in notifications tests (Category 4: ~11 tests)
- **Fix** `get_effective_paths()` deprecated test assertions (Category 6: ~6 tests)
- **Fix** MCP `register_pending_request` attribute rename (Category 7: ~4 tests)
- **Fix** MCP provider `UPath` import (Category 8: ~5 tests)
- **Fix** SSE / GlobalEvent serialization (Category 9: ~15 tests)
- **Fix** RFC0011 lineage `parent_session_id` propagation (Category 10: ~2 tests)
- **Fix** EventProcessor `_child_contexts` attribute rename (Category 11: ~2 tests)
- **Fix** Regex pattern mismatch for exception messages (Category 12: ~5 tests)
- **Fix** Various minor failures — missing `croniter`/`fasta2a` deps, benchmark flakiness, etc.

## Capabilities

### New Capabilities
- `test-failure-remediation`: Fixes for 9 categories of test failures across the agentpool test suite

### Modified Capabilities
<!-- No spec-level requirement changes — all fixes are implementation/test-only -->

## Impact

- **`src/agentpool_server/acp_server/provider_router.py`**: Fix `headers` attribute access on `ProviderCurrentConfig`
- **`tests/`**: Multiple test file fixes (remove, update assertions, add skipif guards)
- **Dependencies**: Optional `apprise` dependency needs skipif guard in notification tests
