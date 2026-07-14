"""Diagnostic: Check AgentPool skill_resolver initialization without full agent startup."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys


sys.path.insert(0, "/Users/yuchen.liu/src/yilab/iroot-llm/packages/agentpool/src")

from agentpool import AgentPool


async def main():
    print("=" * 60)
    print("AgentPool Skill Resolver Diagnostic")
    print("=" * 60)
    print()

    config_path = (
        "/Users/yuchen.liu/src/yilab/iroot-llm/packages/xeno-agent/config/diag-agent-ng.yaml"
    )
    print(f"Config: {config_path}")
    print()

    # Check if config exists
    if not Path(config_path).exists():
        print(f"ERROR: Config file not found: {config_path}")
        return

    try:
        # Create pool but don't enter it - just check initialization
        pool = AgentPool(config_path)
        print("Pool created (not entered)")
        print(f"  Pool skills_dirs: {pool.skills.registry.skills_dirs if pool.skills else 'N/A'}")
        print()

        # Now enter the pool to initialize skill_resolver
        async with pool:
            print("Pool entered successfully")
            print(f"  skill_resolver: {pool.skill_resolver is not None}")
            print(f"  skill_provider: {pool.skill_provider is not None}")
            print()

            if pool.skill_resolver:
                print("Registered providers:")
                for name in pool.skill_resolver.list_providers():
                    print(f"  - {name}")
                print()

                # Test bare skill name resolution
                print("Test: resolver.resolve('systematic-troubleshooting')")
                try:
                    skill = await pool.skill_resolver.resolve("systematic-troubleshooting")
                    print(f"  OK: {skill.name}")
                except Exception as e:  # noqa: BLE001
                    print(f"  FAIL: {e}")
                print()

                # Test URI resolution
                print("Test: resolver.resolve('skill://systematic-troubleshooting/references/...')")
                uri = "skill://systematic-troubleshooting/references/expert_knowledge/excavator/excavator-hard-starting.md"
                try:
                    skill = await pool.skill_resolver.resolve(uri)
                    print(f"  OK: {skill.name}")
                    ref = getattr(skill, "_resolved_reference_path", None)
                    print(f"  ref_path: {ref}")
                except Exception as e:  # noqa: BLE001
                    print(f"  FAIL: {e}")
                print()

    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()

    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
