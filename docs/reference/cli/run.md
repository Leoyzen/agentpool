---
title: run
description: Run a node with prompts
icon: material/play
---

# run

Run a node with prompts using the `agentpool run` command.

```bash
agentpool run <agent_name> "prompt text"
```

The `run` command executes a single prompt against a configured agent.

## Basic Usage

```bash
# Simple run
agentpool run assistant "Hello!"

# With streaming output
agentpool run assistant "Tell me a story" --stream

# With explicit config file
agentpool run assistant "Hello!" --config my-agents.yml
```

For a full list of options, run:

```bash
agentpool run --help
```