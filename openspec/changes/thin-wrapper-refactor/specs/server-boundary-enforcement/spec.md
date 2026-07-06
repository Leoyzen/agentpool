## ADDED Requirements

### Requirement: No core-to-app import violations
The `agentpool` core package SHALL NOT import from `agentpool_server`, `agentpool_cli`, `agentpool_commands`, or any other application-layer package. The dependency direction SHALL be: application packages depend on core, never the reverse.

#### Scenario: Core does not import from server
- **WHEN** `src/agentpool/` source files are scanned for imports from `agentpool_server`
- **THEN** zero import statements referencing `agentpool_server` SHALL be found

#### Scenario: Core does not import from CLI
- **WHEN** `src/agentpool/` source files are scanned for imports from `agentpool_cli`
- **THEN** zero import statements referencing `agentpool_cli` SHALL be found

### Requirement: import-linter enforces architectural boundaries
The project SHALL include an `import-linter` configuration (`.importlinter` or `pyproject.toml` `[tool.importlinter]` section) that defines forbidden import contracts between packages. The configuration SHALL be enforced in CI.

#### Scenario: import-linter detects core-to-app import
- **WHEN** a developer adds an import from `agentpool_server` in `src/agentpool/`
- **THEN** `lint-imports` (import-linter CLI) SHALL fail with a contract violation error

#### Scenario: import-linter passes on clean codebase
- **WHEN** `lint-imports` is run after all violations are fixed
- **THEN** it SHALL exit with code 0 and no contract violations

### Requirement: Four known import violations fixed
The four known core→app import violations SHALL be fixed by either moving the imported code to the core package, inverting the dependency (passing the dependency as a parameter), or restructuring the code to eliminate the import.

#### Scenario: All four violations resolved
- **WHEN** `lint-imports` is run with the new configuration
- **THEN** all four previously-known violations SHALL be resolved and no new violations SHALL be introduced
