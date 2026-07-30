---
title: serve-acp
description: Start ACP server
icon: material/server
---

# serve-acp

Start the Agent Communication Protocol (ACP) server to expose agents to ACP-compatible clients.

```bash
agentwolf serve-acp config.yml
```

For basic usage:

```bash
# Start with default config
agentwolf serve-acp agents.yml

# Listen on specific host and port
agentwolf serve-acp agents.yml --host 0.0.0.0 --port 8321
```

For a full list of options, run:

```bash
agentwolf serve-acp --help
```
