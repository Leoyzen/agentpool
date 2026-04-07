#!/usr/bin/env python3
"""Debug script to inspect session messages in storage."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from agentpool.storage.manager import StorageManager
from agentpool_config.storage import StorageConfig


async def inspect_session(session_id: str | None = None):
    """Inspect session messages in storage."""
    # Use default SQL storage
    config = StorageConfig()
    print(f"Config: {config}")
    print(f"Effective providers: {config.effective_providers}")
    print()

    async with StorageManager(config) as storage:
        # List all sessions
        print("=" * 60)
        print("ALL SESSIONS:")
        print("=" * 60)

        from agentpool_storage.sql_provider.sql_provider import SQLModelProvider
        from agentpool_storage.sql_provider.models import Conversation
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession

        provider = storage.providers[0]
        if not isinstance(provider, SQLModelProvider):
            print(f"ERROR: First provider is not SQLModelProvider: {type(provider)}")
            return

        async with AsyncSession(provider.engine) as session:
            result = await session.execute(
                select(Conversation).order_by(Conversation.start_time.desc())
            )
            conversations = result.scalars().all()

            for conv in conversations:
                print(f"\nSession ID: {conv.id}")
                print(f"  Agent: {conv.agent_name}")
                print(f"  Title: {conv.title}")
                print(f"  Start Time: {conv.start_time}")
                print(f"  Model: {conv.model}")

        # If session_id provided, show messages
        if session_id:
            print("\n" + "=" * 60)
            print(f"MESSAGES FOR SESSION: {session_id}")
            print("=" * 60)

            from agentpool_storage.sql_provider.models import Message

            async with AsyncSession(provider.engine) as session:
                result = await session.execute(
                    select(Message)
                    .where(Message.session_id == session_id)
                    .order_by(Message.timestamp)
                )
                messages = result.scalars().all()

                print(f"\nTotal messages: {len(messages)}\n")

                for i, msg in enumerate(messages):
                    print(f"[{i + 1}] ID: {msg.id}")
                    print(f"    Role: {msg.role}")
                    print(f"    Name: {msg.name}")
                    print(f"    Model: {msg.model}")
                    print(f"    Timestamp: {msg.timestamp}")
                    print(f"    Content Length: {len(msg.content) if msg.content else 0}")
                    print(f"    Content Preview: {msg.content[:200] if msg.content else 'N/A'}...")
                    print(f"    Total Tokens: {msg.total_tokens}")
                    print(
                        f"    Messages Field (JSON): {msg.messages[:100] if msg.messages else 'N/A'}..."
                    )
                    print()

                # Summary by role
                print("\n" + "-" * 60)
                print("SUMMARY BY ROLE:")
                print("-" * 60)
                role_counts: dict[str, int] = {}
                for msg in messages:
                    role_counts[msg.role] = role_counts.get(msg.role, 0) + 1
                for role, count in role_counts.items():
                    print(f"  {role}: {count}")


if __name__ == "__main__":
    session_id = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(inspect_session(session_id))
