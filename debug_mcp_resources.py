#!/usr/bin/env python3
"""
Debug script: Inspect what MCP scratchpad server exposes.

Usage (from packages/agentpool):
    uv run python debug_mcp_resources.py
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "/Users/yuchen.liu/src/yilab/iroot-llm/packages/agentpool/src")

from agentpool.resource_providers.mcp_provider import MCPResourceProvider


async def main():
    print("=" * 60)
    print("MCP Scratchpad Resource Inspection")
    print("=" * 60)
    print()

    provider = MCPResourceProvider(
        server="http://127.0.0.1:8890/mcp",
        name="scratchpad",
    )

    try:
        await provider.__aenter__()
        print("Connected to MCP server")

        # Get all resources
        resources = await provider.get_resources()
        print(f"\nTotal resources: {len(resources)}")
        for r in resources:
            print(f"  URI: {r.uri}")
            print(f"    Name: {r.name}")
            print(f"    Description: {getattr(r, 'description', 'N/A')}")
            print()

        # Get all prompts
        prompts = await provider.get_prompts()
        print(f"\nTotal prompts: {len(prompts)}")
        for p in prompts:
            print(f"  Name: {p.name}")

        # Get all tools
        tools = await provider.get_tools()
        print(f"\nTotal tools: {len(tools)}")
        for t in tools:
            print(f"  Name: {t.name}")

    finally:
        await provider.__aexit__(None, None, None)
        print("\nDone")


if __name__ == "__main__":
    asyncio.run(main())
