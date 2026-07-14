## Why

AgentPool currently supports a single configuration per process — one YAML file, one AgentPool instance, one set of infrastructure. As deployment scenarios grow (serving multiple agent sets, hot-reloading configs, multi-team environments), the single-config limitation becomes a bottleneck. RFC-0050 defines a ConfigRegistry + HostRegistry + RunScope architecture that enables multiple configurations to coexist in one process, with runtime routing based on config_id. This also introduces the HostConfig/AgentManifest config split (deferred from M1 per Oracle analysis) and the model configuration three-layer system.

## What Changes

- **Config split**: `AgentsManifest` splits into `host:` section (HostConfig: mcp_servers, storage, observability, skills, protocol, models) and agent sections (AgentManifest: agents, teams, graph, responses). Flat YAML auto-migrates via model validator.
- **ConfigRegistry**: Versioned config storage with watch notifications. Supports hot-reload — file change triggers recompile.
- **HostRegistry**: By `(config_id, tenant_id)` key, lazily creates/caches/evicts AgentHost instances. Different configs get different Hosts; same config + different tenants get isolated Hosts.
- **RunScope routing**: ProtocolServer `initialize` extracts RunScope (config_id, tenant_id, user_id, session_id) and routes to correct Host + Factory.
- **Model config three-layer**: Provider config (API keys, base URLs), model aliases (`smart` → `openai:gpt-4o`, fallback chains), per-agent selection (agent.model, agent.temperature). ModelCache shares pydantic-ai Model instances across agents.
- **Hot reload**: AgentManifest change → `Factory.recompile()` (diff-based). HostConfig change → `Host.reload()` (restart infrastructure).
- **Multi-config CLI**: `agentpool serve-acp config-a.yml config-b.yml` serves both configs simultaneously.

## Capabilities

### New Capabilities

- `agent-host`: Tenant-scoped infrastructure bundle. One AgentHost per (config_id, tenant_id). Wraps HostContext, AgentFactory, AgentRegistry, and ModelCache. Provides get_agent, reload, cleanup, and validate_tenant methods.
- `config-registry`: Versioned storage and lifecycle management for multiple agent configurations. Supports file watching, hot-reload notifications, and named config lookup.
- `host-registry`: Lazy-create/cache/evict AgentHost instances keyed by (config_id, tenant_id). Enables multi-config and multi-tenant isolation.
- `run-scope`: Cross-cutting routing context (config_id, tenant_id, user_id, session_id) extracted at protocol entry, used at every layer boundary for routing and isolation.
- `model-config`: Three-layer model configuration: providers (infrastructure), aliases (shared definitions), per-agent selection. Includes ModelCache for shared Model instances.

### Modified Capabilities

- `agent-pool`: AgentPool can now be constructed from a ConfigRegistry reference instead of a single file path. Supports config_id-based lookup.
- `agent-factory`: AgentFactory.compile() now accepts AgentManifest (not full AgentsManifest). Model resolution goes through ModelCache + aliases.
- `host-context`: HostContext gains `config_id` field that is populated from RunScope, not hardcoded to "default".
