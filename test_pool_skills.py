"""Test skill resolution with actual AgentPool setup mimicking serve-acp.

Usage (from packages/agentpool):
    uv run python test_pool_skills.py
"""

from __future__ import annotations

import asyncio
import sys


sys.path.insert(0, "/Users/yuchen.liu/src/yilab/iroot-llm/packages/agentpool/src")

from agentpool import AgentPool


async def main():  # noqa: PLR0915
    print("=" * 60)
    print("AgentPool Skill Resolution Test")
    print("=" * 60)
    print()

    config_path = (
        "/Users/yuchen.liu/src/yilab/iroot-llm/packages/xeno-agent/config/diag-agent-ng.yaml"
    )
    print(f"Config: {config_path}")
    print()

    try:
        pool = AgentPool(config_path)
        async with pool:
            print("Pool initialized")
            print(f"  skill_resolver: {pool.skill_resolver is not None}")
            print(f"  skill_provider: {pool.skill_provider is not None}")
            print(f"  skills_dirs: {pool.skills.registry.skills_dirs if pool.skills else 'N/A'}")
            print()

            # List local skills
            local_skills = pool.skills.list_skills()
            print(f"Local skills ({len(local_skills)}):")
            for s in local_skills:
                print(f"  - {s.name}: {s.skill_path}")
            print()

            # List provider skills (including MCP)
            if pool.skill_provider:
                provider_skills = await pool.skill_provider.get_skills()
                print(f"Provider skills ({len(provider_skills)}):")
                for s in provider_skills:
                    print(f"  - {s.name}: {s.skill_path}")
                print()

            # Test resolver
            resolver = pool.skill_resolver
            if resolver:
                print("Registered providers:")
                for name in resolver.list_providers():
                    print(f"  - {name}")
                print()

                # Test 1: Bare skill name
                print("--- Test 1: Bare skill name ---")
                try:
                    skill = await resolver.resolve("systematic-troubleshooting")
                    print(f"OK: {skill.name}")
                except Exception as e:  # noqa: BLE001
                    print(f"FAIL: {e}")
                print()

                # Test 2: skill:// URI with reference
                print("--- Test 2: skill:// URI with reference ---")
                uri = "skill://systematic-troubleshooting/references/expert_knowledge/excavator/excavator-hard-starting.md"
                try:
                    skill = await resolver.resolve(uri)
                    print(f"OK: {skill.name}")
                    ref_path = getattr(skill, "_resolved_reference_path", None)
                    print(f"    ref_path: {ref_path}")
                except Exception as e:  # noqa: BLE001
                    print(f"FAIL: {e}")
                print()

    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
