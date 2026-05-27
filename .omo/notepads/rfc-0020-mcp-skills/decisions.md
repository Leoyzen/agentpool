# RFC-0020 Architectural Decisions

## Decision Log

## Decisions to Make

### Decision 1: Cache Implementation Strategy
- Options: functools.lru_cache, cachetools.TTLCache, custom implementation
- Factors: Async support, TTL requirement, invalidation needs

### Decision 2: Skill Name Collision Resolution
- Current spec: Provider priority (local > MCP)
- Alternative: Namespace prefixing (provider/skill-name)
- Decision needed: Keep simple priority or add namespacing

### Decision 3: MCP Resource Skill Detection
- Options: 
  1. Detect skill://skill-name/SKILL.md pattern only
  2. Also check _manifest resource
  3. Both with fallback
- Decision needed: Implementation approach

### Decision 4: Argument Substitution Syntax
- Options: $1, $2, $@ vs {arg1}, {arg2}, {args}
- RFC specifies: $1, $2, $@, $ARGUMENTS
- Decision: Follow RFC specification

### Decision 5: AggregatingResourceProvider Deduplication
- Options:
  1. Keep all skills (even with same name from different providers)
  2. Deduplicate by name (first provider wins)
  3. Deduplicate by name+provider
- Decision: Keep all skills, let resolver handle priority

### Decision 6: Performance Testing Approach
- **URI Resolution**: <10ms target using time.perf_counter() measurements
- **Skill Discovery**: <50ms target (<100ms acceptable for CI environments)
- **Caching**: Verified 2x+ speedup requirement
- **Test Structure**: Module-level constants for thresholds, time-based assertions
- **RFC-0020 Compliance**: Separate thresholds for target vs acceptable performance

### Decision 7: Performance Benchmark Organization
- **Location**: `tests/performance/test_skill_performance.py`
- **Coverage**: URI parsing, resolution, discovery (10/50/100 skills), caching, multi-provider
- **Thresholds**: Constants defined at module level (RFC0020_*_THRESHOLD_MS)
- **Evidence**: Saved to `.sisyphus/evidence/task-15-perf.txt`
