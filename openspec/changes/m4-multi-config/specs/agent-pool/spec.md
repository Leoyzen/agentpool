## MODIFIED Requirements

### Requirement: AgentPool can be constructed from ConfigRegistry reference

AgentPool SHALL support construction from a `ConfigRegistry` reference in addition to the existing file-path constructor. When constructed from a ConfigRegistry, AgentPool SHALL use a specified `config_id` to look up its configuration. The existing `async with AgentPool("config.yml")` constructor SHALL continue to work by internally creating a ConfigRegistry and registering the file.

#### Scenario: Construct AgentPool from ConfigRegistry

- **WHEN** `AgentPool.from_registry(registry, config_id="config-a")` is called
- **THEN** AgentPool SHALL retrieve the config for "config-a" from the registry
- **AND** SHALL initialize infrastructure (MCP, storage, skills) from the HostConfig section
- **AND** SHALL construct a HostContext with `config_id="config-a"`

#### Scenario: Existing file-path constructor still works

- **WHEN** `async with AgentPool("config.yml") as pool:` is used
- **THEN** AgentPool SHALL internally create a ConfigRegistry, register the file, and use `config_id="default"`
- **AND** all existing behavior SHALL be preserved

#### Scenario: AgentPool config_id is populated from RunScope

- **WHEN** AgentPool is accessed through HostRegistry with a RunScope containing `config_id="config-a"`
- **THEN** the HostContext's `config_id` field SHALL be `"config-a"`, not `"default"`
- **AND** the config_id SHALL propagate to all agents created by the AgentFactory

### Requirement: AgentPool supports multi-config CLI

The `agentpool` CLI SHALL accept multiple config file paths for serve commands. Each config SHALL be registered in a ConfigRegistry with a derived `config_id` (from filename or explicit `--name` flag). The CLI SHALL create a HostRegistry and start the protocol server with multi-config routing enabled.

#### Scenario: Serve multiple configs simultaneously

- **WHEN** `agentpool serve-acp config-a.yml config-b.yml` is run
- **THEN** both configs SHALL be registered in a ConfigRegistry
- **AND** a HostRegistry SHALL be created to manage Hosts for both configs
- **AND** the ACP server SHALL route requests to the correct Host based on RunScope

#### Scenario: Named configs via CLI flag

- **WHEN** `agentpool serve-acp --name prod config-a.yml --name staging config-b.yml` is run
- **THEN** config-a SHALL be registered with `config_id="prod"`
- **AND** config-b SHALL be registered with `config_id="staging"`
- **AND** RunScope extraction SHALL use these config IDs for routing
