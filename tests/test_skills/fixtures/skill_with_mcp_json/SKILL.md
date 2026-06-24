---
# ============================================================
# SKILL.md Frontmatter — mcp.json companion file
# ============================================================
# This skill has NO frontmatter mcp-servers — all MCP configuration
# comes from the companion mcp.json file in the same directory.
#
# mcp.json follows Claude Desktop's format:
#   { "mcpServers": { "name": { "command": "...", "args": [...] } } }
#
# The companion file supports BOTH stdio and HTTP server types,
# plus environment variable expansion (${VAR} syntax).
# See mcp.json in this directory for the actual config.
# ============================================================

name: skill-with-mcp-json
description: A test skill with MCP servers configured via companion mcp.json file
---

# Skill with mcp.json

This skill demonstrates MCP server configuration via a companion `mcp.json` file rather than
frontmatter `mcp-servers`.

## Precedence

When both `mcp-servers` in frontmatter AND a companion `mcp.json` exist:

1. `mcp.json` values **override** frontmatter values for the same keys
2. Frontmatter keys NOT present in `mcp.json` are **preserved**

See `skill-with-mcp-json-conflict` for a test of this precedence behavior.

## Usage

Use this skill when testing mcp.json companion file loading via `_load_mcp_json()`.
