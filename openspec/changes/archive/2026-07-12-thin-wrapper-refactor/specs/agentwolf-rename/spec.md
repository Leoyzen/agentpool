## ADDED Requirements

### Requirement: All packages renamed from agentpool to agentwolf
The following 10 packages SHALL be renamed: `agentpool` → `agentwolf`, `agentpool_config` → `agentwolf_config`, `agentpool_server` → `agentwolf_server`, `agentpool_toolsets` → `agentwolf_toolsets`, `agentpool_storage` → `agentwolf_storage`, `agentpool_cli` → `agentwolf_cli`, `agentpool_commands` → `agentwolf_commands`, `agentpool_prompts` → `agentwolf_prompts`. The `acp` package SHALL retain its name.

#### Scenario: All package directories renamed
- **WHEN** the `src/` directory is inspected after rename
- **THEN** directories SHALL be named `agentwolf/`, `agentwolf_config/`, `agentwolf_server/`, etc., with no `agentpool*` directories remaining

#### Scenario: All imports updated
- **WHEN** the codebase is scanned for `from agentpool` or `import agentpool`
- **THEN** zero matches SHALL be found (all replaced with `agentwolf`)

### Requirement: CLI entry point renamed
The CLI entry point SHALL be renamed from `agentpool` to `agentwolf`. The `pyproject.toml` `[project.scripts]` section SHALL define `agentwolf` as the command name.

#### Scenario: agentpool command not found
- **WHEN** `agentpool --version` is executed after rename
- **THEN** the command SHALL not be found (or shall print a deprecation notice if alias is kept temporarily)

#### Scenario: agentwolf command works
- **WHEN** `agentwolf --version` is executed
- **THEN** it SHALL print the version and exit successfully

### Requirement: Configuration schema renamed
All YAML configuration references to `agentpool` SHALL be updated to `agentwolf`. This includes schema names, documentation references, and example configs.

#### Scenario: YAML config uses agentwolf
- **WHEN** example YAML configs in `site/examples/` are inspected
- **THEN** all references to `agentpool` SHALL be replaced with `agentwolf`

### Requirement: ACP package imports updated
The `acp` package SHALL retain its name but the 2 imports that reference `agentpool` SHALL be updated to reference `agentwolf`.

#### Scenario: acp package has no agentpool imports
- **WHEN** `src/acp/` is scanned for `agentpool` imports
- **THEN** zero matches SHALL be found (all replaced with `agentwolf`)

### Requirement: No backward-compatible aliases
No backward-compatible import aliases SHALL be provided. `import agentpool` SHALL fail after the rename. There SHALL be no `agentpool = agentwolf` shim.

#### Scenario: Import agentpool fails
- **WHEN** Python code executes `import agentpool`
- **THEN** `ModuleNotFoundError` SHALL be raised

### Requirement: Documentation and docstrings updated
All Markdown documentation, docstrings, and inline comments referencing `agentpool` SHALL be updated to reference `agentwolf`.

#### Scenario: No agentpool references in docs
- **WHEN** all `.md` files are scanned for `agentpool`
- **THEN** zero matches SHALL be found (excluding this openspec change document which is historical)
