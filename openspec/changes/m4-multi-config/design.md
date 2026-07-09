## Context

M1 delivered the foundation: `HostContext` (frozen dataclass carrying infrastructure handles) and `AgentFactory` (standalone compilation service). M2 delivered `RunLoop` (session orchestration, event bus, turn execution). Together they enable clean layer separation — but the system still operates on a single `AgentsManifest` per process. One YAML file, one `AgentPool`, one set of infrastructure.

AgentPool now has 6 protocol servers (~12,500 LOC across ACP, OpenCode, AG-UI, MCP, OpenAI API, and CLI). None are multi-tenant aware. Each assumes a single global `AgentPool` instance. The config model (`AgentsManifest`) mixes infrastructure config (mcp_servers, storage, observability) with agent definitions (agents, teams, graph) in a flat structure. The config split was explicitly deferred from M1 per Oracle analysis — M1's `AgentFactory.compile()` accepts the full `AgentsManifest` and only reads agent/team/graph sections, so narrowing to `AgentManifest` is transparent.

RFC-0050 defines the multi-config architecture: `ConfigRegistry` (versioned storage + watch), `HostRegistry` (lazy create/cache/evict by `(config_id, tenant_id)`), `RunScope` (routing context extracted at protocol entry). This milestone implements all three plus the config split and model config three-layer system.

## Goals / Non-Goals

**Goals:**
- Implement `ConfigRegistry` for versioned config storage with file watching and hot-reload notifications
- Implement `HostRegistry` for lazy create/cache/evict of `AgentHost` instances keyed by `(config_id, tenant_id)`
- Implement `RunScope` as a frozen routing dataclass extracted at protocol `initialize`
- Split `AgentsManifest` into `HostConfig` (infrastructure) and `AgentManifest` (agent definitions) with flat YAML auto-migration
- Implement three-layer model configuration: providers (infrastructure), aliases (shared definitions), per-agent selection
- Implement `ModelCache` for shared pydantic-ai `Model` instances across agents
- Hot reload: `AgentManifest` change → `Factory.recompile()` (diff-based); `HostConfig` change → `Host.reload()`
- Multi-config CLI: `agentpool serve-acp config-a.yml config-b.yml` serves both simultaneously
- `AgentPool` can be constructed from a `ConfigRegistry` reference instead of a single file path

**Non-Goals:**
- Tenant isolation (security boundaries, resource quotas) — M5
- Polyglot agent support (non-Python agents) — M6
- New protocol server implementations — existing servers are adapted, no new protocols added
- Full `ResourceProvider` cleanup — M3
- Backward-incompatible API changes — `async with AgentPool("config.yml")` must still work

## Decisions

### Decision 1: Config split — HostConfig vs AgentManifest

**Choice**: `AgentsManifest` splits into `host:` section (`HostConfig`: mcp_servers, storage, observability, skills, protocol, models) and agent sections (`AgentManifest`: agents, teams, graph, responses). Flat YAML (no `host:` key) auto-migrates via a Pydantic model validator that partitions fields.

**Rationale**: Infrastructure config is expensive to change (MCP process restart, storage reconnection, skill reload). Agent definitions are cheap to recompile (in-memory object recreation). Splitting them enables targeted hot reload — an agent definition change doesn't restart MCP servers. The flat YAML auto-migration ensures zero breaking changes for existing users.

**Alternative considered**: Keep flat `AgentsManifest` and diff at field level — rejected because the infrastructure/agent boundary is architecturally significant and must be explicit for `HostRegistry` to know when to evict vs recompile.

### Decision 2: HostRegistry lazy-create/cache/evict by (config_id, tenant_id)

**Choice**: `HostRegistry` maintains a `dict[tuple[str, str], AgentHost]` keyed by `(config_id, tenant_id)`. `get_or_create(config_id, tenant_id)` lazily creates and caches. Eviction drains active sessions before destroying the Host.

**Rationale**: Different configs get different Hosts (different infrastructure). Same config + different tenants get isolated Hosts (same config, different runtime state). Lazy creation avoids upfront cost for unused configs. Eviction with drain prevents losing in-flight requests.

**Alternative considered**: Eager creation of all Hosts at startup — rejected for memory and startup latency. LRU eviction without drain — rejected because it would kill active sessions.

### Decision 3: RunScope extracted at protocol initialize

**Choice**: `RunScope` is a frozen dataclass `(config_id, tenant_id, user_id, session_id)`. Protocol servers extract it during `initialize` (from headers, URL params, or config). It is threaded through every layer boundary for routing.

**Rationale**: Protocol entry points are the natural extraction location — they have request context. Threading through layer boundaries (not global state) ensures testability and isolation. Frozen dataclass prevents mutation during a request lifecycle.

**Alternative considered**: Thread-local context — rejected because it's invisible, untestable, and breaks async context propagation. Passing individual params — rejected because the 4-tuple is always used together.

### Decision 4: Model config three-layer with ModelCache

**Choice**: Three layers: (1) providers (API keys, base URLs — infrastructure), (2) aliases (`smart` → `openai:gpt-4o`, fallback chains — shared definitions), (3) per-agent selection (`agent.model`, `agent.temperature` — individual config). `ModelCache` shares pydantic-ai `Model` instances across agents within a Host.

**Rationale**: Provider config is infrastructure (shared across all agents, expensive to recreate). Aliases are shared definitions (referenced by multiple agents, centrally managed). Per-agent selection is the leaf layer (model string or alias reference). `ModelCache` avoids redundant model client instantiation (each `openai:gpt-4o` Model object holds an HTTP client pool).

**Alternative considered**: Flat model config with inline provider settings — rejected because it duplicates API keys and prevents centralized provider management. Per-agent Model instances — rejected for resource waste.

### Decision 5: Hot reload — diff-based recompile vs full restart

**Choice**: `AgentManifest` change → `AgentFactory.recompile()` with diff-based agent recreation (only changed agents are rebuilt). `HostConfig` change → `AgentHost.reload()` (full infrastructure restart for that Host).

**Rationale**: Agent definition changes (system prompt tweak, tool add) are frequent and should be fast — diff-based recompile preserves unchanged agents. Infrastructure changes (MCP server added, storage reconfigured) are rare and require full restart — can't hot-swap a running MCP process.

**Alternative considered**: Full recompile on any config change — rejected for latency on agent-only changes. Partial infrastructure reload — rejected for complexity and race conditions.

### Decision 6: K8s CRD analogy for architectural mapping

**Choice**: The architecture maps to Kubernetes controller pattern: `ConfigRegistry` = etcd (versioned storage with watch), `AgentHost` = Controller (reconciles desired state), `AgentFactory` = Reconcile loop (transforms config to runtime).

**Rationale**: This analogy guides design decisions — watch-based notifications (not polling), desired-state reconciliation (not imperative commands), and versioned config (not mutable state). It also clarifies the hot reload model: config change is a "spec update," recompile is "reconciliation."

## Risks / Trade-offs

- **[Risk] Hot reload race conditions** — An agent definition change mid-turn could produce inconsistent behavior (old prompt for model call, new tools for tool call) → Mitigated by turn-level snapshot: `RunScope` captures the config version at turn start, and the turn runs against that version. MEDIUM risk.
- **[Risk] Model alias resolution may differ per tenant** — Tenant A's `smart` alias might point to a different model than Tenant B's → This is by design (per-tenant provider config), but must be documented. Aliases resolve within the Host's provider context, not globally. MEDIUM risk.
- **[Risk] Host eviction must drain active sessions** — Evicting a Host with active sessions would kill in-flight requests → `HostRegistry.evict()` SHALL wait for active sessions to complete (with timeout) before destroying infrastructure. If timeout expires, sessions are cancelled gracefully. HIGH risk, mitigated by drain + timeout.
- **[Trade-off] Config split adds a migration layer** — Flat YAML auto-migration adds complexity to config loading → Acceptable because it's a one-time validator that runs at load. Users who adopt the `host:` section get explicit control. MIGRATION risk, LOW complexity.
- **[Trade-off] ModelCache lifetime tied to Host** — Model instances are cached per-Host, not globally → If two Hosts use the same provider, they get separate Model instances (separate HTTP client pools). This is acceptable for isolation but suboptimal for resource sharing. Could be addressed in M5 with a global provider pool.
