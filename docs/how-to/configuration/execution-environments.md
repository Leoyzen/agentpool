---
title: Execution environments
description: Event handler setup and configuration
---

Execution environments allow you to configure the runtime environment for your agent. It's where code is run and where processes are managed / commands are executed.

Any Agent which can perform IO (regular Agents & ACP Agents) can get assigned an execution environment.

## Configuration

The execution environment can be configured with the following options:

- **type**: The type of execution environment (e.g., `local`)
- **cwd**: Working directory for command execution
- **env**: Environment variables
- **timeout**: Maximum execution time

There's one more execution environment, the ACP environment.
This one cannot get assigned manually, but it becomes the default execution environment for any agent participating in an ACP session (it's overridable though, an ACP agent can also work remotely!).