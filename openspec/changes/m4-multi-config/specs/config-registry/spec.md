## ADDED Requirements

### Requirement: ConfigRegistry provides versioned config storage

ConfigRegistry SHALL store agent configurations with version tracking. Each registered config SHALL have a unique `config_id` (string) and a monotonically increasing version number. ConfigRegistry SHALL support registering configs from file paths, YAML strings, or parsed `AgentsManifest` objects.

#### Scenario: Register a config from file path

- **WHEN** `registry.register("config-a", "/path/to/config.yml")` is called
- **THEN** the config SHALL be loaded, parsed, and stored under `config_id="config-a"`
- **AND** the initial version SHALL be `1`
- **AND** the config SHALL be retrievable via `registry.get("config-a")`

#### Scenario: Version increments on re-registration

- **WHEN** the same `config_id` is re-registered with a modified config
- **THEN** the version number SHALL increment by `1`
- **AND** `registry.get_version("config-a")` SHALL return the new version number

#### Scenario: Register multiple configs simultaneously

- **WHEN** `registry.register("config-a", path_a)` and `registry.register("config-b", path_b)` are both called
- **THEN** both configs SHALL be stored and independently retrievable
- **AND** modifying one config SHALL NOT affect the other

### Requirement: ConfigRegistry watches files for changes

ConfigRegistry SHALL monitor registered config files for modifications on disk. When a file changes, ConfigRegistry SHALL reload the config, increment the version, and notify registered listeners.

#### Scenario: File modification triggers reload

- **WHEN** a config file registered with `registry.register("config-a", "/path/to/config.yml")` is modified on disk
- **THEN** ConfigRegistry SHALL reload the file within a configurable debounce window (default: 500ms)
- **AND** the version SHALL increment
- **AND** all registered listeners SHALL be notified with the new config and version

#### Scenario: File watching is debounced

- **WHEN** a config file receives multiple rapid modifications (e.g., editor save + format)
- **THEN** ConfigRegistry SHALL debounce the notifications and only reload once after the debounce window elapses
- **AND** no intermediate versions SHALL be created for rapid successive changes

### Requirement: ConfigRegistry supports hot-reload notifications

ConfigRegistry SHALL allow components to subscribe to config change notifications. Subscribers register a callback that receives `(config_id, new_config, old_config, version)` when a config changes.

#### Scenario: Subscribe to config changes

- **WHEN** `registry.on_change("config-a", callback)` is registered and the config is reloaded
- **THEN** `callback` SHALL be invoked with `(config_id, new_config, old_config, new_version)`
- **AND** the callback SHALL receive the full parsed config objects, not raw YAML

#### Scenario: Multiple subscribers receive notifications

- **WHEN** two callbacks are registered for the same `config_id` and the config changes
- **THEN** both callbacks SHALL be invoked in registration order
- **AND** if one callback raises an exception, the second callback SHALL still be invoked

### Requirement: ConfigRegistry provides named config lookup

ConfigRegistry SHALL provide `get(config_id)` returning the current parsed config, `get_version(config_id)` returning the current version, and `list_configs()` returning all registered config IDs.

#### Scenario: Get config by ID

- **WHEN** `registry.get("config-a")` is called for a registered config
- **THEN** the current parsed `AgentsManifest` SHALL be returned
- **AND** the returned object SHALL reflect the latest version (including hot-reloaded changes)

#### Scenario: Get non-existent config

- **WHEN** `registry.get("nonexistent")` is called
- **THEN** a `KeyError` SHALL be raised
- **AND** `registry.list_configs()` SHALL NOT include "nonexistent"

### Requirement: ConfigRegistry supports unregister

ConfigRegistry SHALL support `unregister(config_id)` to remove a config and stop watching its file. Unregistering a config that has active listeners SHALL notify listeners that the config is being removed.

#### Scenario: Unregister a config

- **WHEN** `registry.unregister("config-a")` is called
- **THEN** the config SHALL be removed from storage
- **AND** file watching for that config SHALL stop
- **AND** registered listeners SHALL receive a final notification with `new_config=None`

#### Scenario: Unregister non-existent config

- **WHEN** `registry.unregister("nonexistent")` is called
- **THEN** a `KeyError` SHALL be raised
- **AND** no side effects SHALL occur
