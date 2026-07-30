---
title: run
description: Run a node with prompts
icon: material/play
---

# run

Run a node with prompts using the `agentwolf run` command.

```bash
agentwolf run <agent_name> "prompt text"
```

The `run` command executes a single prompt against a configured agent.

## Basic Usage

```bash
# Simple run
agentwolf run assistant "Hello!"

# With streaming output
agentwolf run assistant "Tell me a story" --stream

# With explicit config file
agentwolf run assistant "Hello!" --config my-agents.yml
```

For a full list of options, run:

```bash
agentwolf run --help
```