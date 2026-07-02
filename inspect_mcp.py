"""Inspect MCP server resources at http://127.0.0.1:8890/mcp."""

import asyncio
import sys


sys.path.insert(0, "/Users/yuchen.liu/src/yilab/iroot-llm/packages/agentpool/src")

from agentpool.resource_providers.mcp_provider import MCPResourceProvider


async def main():
    provider = MCPResourceProvider(
        server="http://127.0.0.1:8890/mcp",
        name="scratchpad",
    )

    try:
        await provider.__aenter__()
        print("Connected to MCP server")
        print(f"Server config: {provider.server}")

        # Get all resources
        resources = await provider.get_resources()
        print(f"\nResources ({len(resources)}):")
        for r in resources:
            print(f"  - uri={r.uri} name={r.name}")

        # Get all prompts
        prompts = await provider.get_prompts()
        print(f"\nPrompts ({len(prompts)}):")
        for p in prompts:
            print(f"  - name={p.name}")

        # Get all tools
        tools = await provider.get_tools()
        print(f"\nTools ({len(tools)}):")
        for t in tools:
            print(f"  - name={t.name}")

        # List skill resources specifically
        skill_resources = [r for r in resources if str(r.uri).startswith("skill://")]
        print(f"\nSkill resources ({len(skill_resources)}):")
        for r in skill_resources:
            print(f"  - {r.uri}")

    finally:
        await provider.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())
