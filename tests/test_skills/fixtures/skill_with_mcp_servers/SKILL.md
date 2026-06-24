---
# ============================================================
# SKILL.md Frontmatter -- MCP Servers
# ============================================================
# This fixture demonstrates mcp-servers frontmatter configuration.
#
# mcp-servers supports TWO connection types:
#   1) stdio  -- command + args (local subprocess, like npx/uvx)
#   2) HTTP   -- url + headers (remote server)
#
# You can mix both types in the same skill.
# ============================================================

name: skill-with-mcp-servers
description: A test skill with both stdio and HTTP MCP server examples in frontmatter

# MCP server configurations for skill-provided tools.
mcp-servers:
  # Stdio-based server: starts an MCP server via local command + args.
  filesystem-server:
    command: npx                                          # Executable to run
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]  # CLI arguments
    # Optional: env vars for the subprocess
    # env:
    #   NODE_ENV: "production"

  # HTTP-based server: connects to a remote MCP server over HTTP SSE.
  # (commented out -- no actual server running at this URL)
  # remote-api-server:
  #   url: "http://localhost:8080/mcp"
  #   headers:
  #     Authorization: "Bearer ${TOKEN}"
---

# Skill with MCP Servers

This skill demonstrates `mcp-servers` frontmatter configuration with both stdio-based (local)
and HTTP-based (remote) MCP server examples.

## Connection Types

| Type   | Fields                  | Use Case                         |
|--------|-------------------------|----------------------------------|
| stdio  | `command` + `args`      | Local subprocess (npx, uvx, etc) |
| HTTP   | `url` + `headers`       | Remote server, shared deployment |

## Usage

Use this skill when you need filesystem or remote API access via MCP.

## Comments on Fields

- **command**: Executable path or name (e.g., "npx", "uvx", "python"). Set to `null` for HTTP.
- **args**: List of CLI arguments passed to the command.
- **url**: Remote server URL for HTTP connections. Set to `null` for stdio.
- **headers**: HTTP headers sent with URL requests.
- **env**: Environment variables injected into the subprocess (stdio only).
