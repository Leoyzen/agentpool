#!/usr/bin/env python3
"""Debug script to check session messages via HTTP API and trace the execution chain."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import httpx


async def check_session_api(session_id: str, base_url: str = "http://localhost:8000"):
    """Check session messages via HTTP API."""
    async with httpx.AsyncClient() as client:
        # 1. List all sessions
        print("=" * 60)
        print("1. LIST ALL SESSIONS")
        print("=" * 60)
        try:
            resp = await client.get(f"{base_url}/session")
            sessions = resp.json()
            print(f"Found {len(sessions)} sessions")
            for s in sessions[:5]:  # Show first 5
                print(f"  - {s.get('id', 'N/A')}: {s.get('title', 'N/A')}")
        except Exception as e:
            print(f"ERROR: {e}")

        # 2. Get specific session
        print("\n" + "=" * 60)
        print(f"2. GET SESSION: {session_id}")
        print("=" * 60)
        try:
            resp = await client.get(f"{base_url}/session/{session_id}")
            if resp.status_code == 200:
                session = resp.json()
                print(f"Session found:")
                print(f"  ID: {session.get('id')}")
                print(f"  Title: {session.get('title')}")
                print(f"  Agent: {session.get('agent')}")
            else:
                print(f"ERROR: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"ERROR: {e}")

        # 3. Get session messages
        print("\n" + "=" * 60)
        print(f"3. GET SESSION MESSAGES: {session_id}")
        print("=" * 60)
        try:
            resp = await client.get(f"{base_url}/session/{session_id}/message")
            if resp.status_code == 200:
                messages = resp.json()
                print(f"Found {len(messages)} messages\n")

                role_counts: dict[str, int] = {}
                for msg in messages:
                    role = msg.get("role", "unknown")
                    role_counts[role] = role_counts.get(role, 0) + 1

                    print(f"[{role.upper()}] ID: {msg.get('id', 'N/A')}")
                    print(f"  Model: {msg.get('model_id', 'N/A')}")
                    print(f"  Timestamp: {msg.get('time', {}).get('created', 'N/A')}")
                    parts = msg.get("parts", [])
                    print(f"  Parts count: {len(parts)}")
                    for i, part in enumerate(parts[:3]):  # Show first 3 parts
                        part_type = part.get("type", "unknown")
                        content_preview = ""
                        if part_type == "text":
                            content_preview = part.get("text", "")[:100]
                        elif part_type == "reasoning":
                            content_preview = part.get("text", "")[:100]
                        print(f"    [{i + 1}] {part_type}: {content_preview}...")
                    if len(parts) > 3:
                        print(f"    ... and {len(parts) - 3} more parts")
                    print()

                print("-" * 60)
                print("SUMMARY BY ROLE:")
                print("-" * 60)
                for role, count in role_counts.items():
                    print(f"  {role}: {count}")
            else:
                print(f"ERROR: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"ERROR: {e}")


async def trace_execution_chain(session_id: str):
    """Trace the execution chain from storage to API response."""
    from agentpool.storage.manager import StorageManager
    from agentpool_config.storage import StorageConfig
    from agentpool_storage.sql_provider.sql_provider import SQLModelProvider
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    print("\n" + "=" * 60)
    print("4. TRACE EXECUTION CHAIN")
    print("=" * 60)

    config = StorageConfig()
    print(f"Storage config: {config}")
    print(f"Effective providers: {len(config.effective_providers)}")

    async with StorageManager(config) as storage:
        provider = storage.providers[0]
        print(f"Provider type: {type(provider).__name__}")
        print(f"Can load history: {provider.can_load_history}")

        # Step 1: Get messages directly from provider
        print("\n--- Step 1: Provider.get_session_messages() ---")
        messages = await provider.get_session_messages(session_id)
        print(f"Messages returned: {len(messages)}")

        role_counts: dict[str, int] = {}
        for msg in messages:
            role_counts[msg.role] = role_counts.get(msg.role, 0) + 1
        print(f"Role distribution: {role_counts}")

        # Step 2: Check raw database records
        print("\n--- Step 2: Raw database query ---")
        if isinstance(provider, SQLModelProvider):
            from agentpool_storage.sql_provider.models import Message

            async with AsyncSession(provider.engine) as session:
                result = await session.execute(
                    select(Message).where(Message.session_id == session_id)  # type: ignore
                )
                db_messages = result.scalars().all()
                print(f"DB records: {len(db_messages)}")

                db_role_counts: dict[str, int] = {}
                for msg in db_messages:
                    db_role_counts[msg.role] = db_role_counts.get(msg.role, 0) + 1
                print(f"DB role distribution: {db_role_counts}")

        # Step 3: Check conversion to OpenCode format
        print("\n--- Step 3: Conversion to OpenCode format ---")
        from agentpool_server.opencode_server.converters import chat_message_to_opencode

        opencode_messages = []
        for msg in messages:
            try:
                oc_msg = chat_message_to_opencode(
                    msg,
                    session_id=session_id,
                    working_dir=str(Path.cwd()),
                    agent_name="debug",
                    model_id=msg.model_name or "unknown",
                    provider_id="debug",
                )
                opencode_messages.append(oc_msg)
            except Exception as e:
                print(f"  ERROR converting message {msg.message_id}: {e}")

        print(f"Successfully converted: {len(opencode_messages)}")

        oc_role_counts: dict[str, int] = {}
        for msg in opencode_messages:
            role = getattr(msg, "role", "unknown")
            oc_role_counts[role] = oc_role_counts.get(role, 0) + 1
        print(f"OpenCode role distribution: {oc_role_counts}")


if __name__ == "__main__":
    session_id = sys.argv[1] if len(sys.argv) > 1 else "ses_d4cd77a64001kwbPDCOlpvAo9d"
    base_url = sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8000"

    print(f"Debugging session: {session_id}")
    print(f"Base URL: {base_url}\n")

    asyncio.run(check_session_api(session_id, base_url))
    asyncio.run(trace_execution_chain(session_id))
