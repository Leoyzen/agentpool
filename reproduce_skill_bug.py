"""Quick red flag test: verify skill reference loading against local MCP scratchpad.

Run from packages/agentpool directory:
    uv run python reproduce_skill_bug.py
"""

from __future__ import annotations

import asyncio
import sys


sys.path.insert(0, "/Users/yuchen.liu/src/yilab/iroot-llm/packages/agentpool/src")

from agentpool.resource_providers.mcp_provider import MCPResourceProvider

from agentpool.skills.uri_resolver import SkillURIResolver


async def main():
    print("=" * 60)
    print("Skill Reference Loading - Red Flag Test")
    print("=" * 60)
    print()

    provider = MCPResourceProvider(
        server="http://127.0.0.1:8890/mcp",
        name="pool_mcp_scratchpad",
    )

    try:
        await provider.__aenter__()
        print(f"Provider connected: {provider.server}")

        # List skills from provider
        skills = await provider.get_skills()
        print(f"\nSkills from provider ({len(skills)} total):")
        for s in skills:
            print(f"  - {s.name}: {s.skill_path}")

        # Create resolver and register provider
        resolver = SkillURIResolver()
        resolver.register_provider("pool_mcp_scratchpad", provider)

        # Test 1: Bare skill name (should work)
        print("\n--- Test 1: Bare skill name ---")
        try:
            skill = await resolver.resolve("systematic-troubleshooting")
            print(f"OK: {skill.name}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL: {e}")

        # Test 2: skill:// URI with reference (the bug)
        print("\n--- Test 2: skill:// URI with reference ---")
        uri = "skill://systematic-troubleshooting/references/expert_knowledge/excavator/excavator-hard-starting.md"
        try:
            skill = await resolver.resolve(uri)
            print(f"OK: {skill.name}")
            ref_path = getattr(skill, "_resolved_reference_path", None)
            print(f"    _resolved_reference_path: {ref_path}")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL: {e}")

    finally:
        await provider.__aexit__(None, None, None)
        print("\nDisconnected")


if __name__ == "__main__":
    asyncio.run(main())
