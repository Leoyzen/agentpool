"""Title persistence verification script.

Run this to verify session title persistence is working correctly.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from agentpool.sessions.models import SessionData
from agentpool.storage.manager import SessionMetadata, StorageManager
from agentpool_server.opencode_server.converters import (
    opencode_to_session_data,
    session_data_to_opencode,
)
from agentpool_server.opencode_server.models import Session
from agentpool_server.opencode_server.models.common import TimeCreatedUpdated
from agentpool_config.storage import MemoryStorageConfig, OpenCodeStorageConfig, StorageConfig
from agentpool_storage.opencode_provider.provider import OpenCodeStorageProvider


async def verify_all_fixes():
    """Run all verification checks."""
    print("=" * 70)
    print("Session Title Persistence Verification")
    print("=" * 70)

    all_passed = True

    # 1. Test converters
    print("\n[1/5] Testing converters...")
    try:
        session = Session(
            id="test_conv_001",
            project_id="default",
            directory="/tmp",
            title="Test Title",
            version="1",
            time=TimeCreatedUpdated(created=1234567890, updated=1234567890),
        )
        session_data = opencode_to_session_data(session, agent_name="test")
        assert session_data.metadata.get("title") == "Test Title", "Converter failed"
        print("  ✓ Converters work correctly")
    except AssertionError as e:
        print(f"  ✗ Converter test failed: {e}")
        all_passed = False

    # 2. Test StorageManager title generation
    print("\n[2/5] Testing StorageManager title generation...")
    try:
        config = StorageConfig(
            providers=[MemoryStorageConfig()],
            title_generation_model="test",
        )
        async with StorageManager(config) as manager:
            session_id = "test_gen_001"
            await manager.log_session(session_id=session_id, node_name="test_agent")

            mock_metadata = SessionMetadata(
                title="Generated Test Title",
                emoji="🧪",
                icon="mdi:test-tube",
            )

            with patch.object(
                StorageManager,
                "_generate_title_core",
                return_value=mock_metadata,
            ):
                title = await manager._generate_title_from_prompt(
                    session_id=session_id,
                    prompt="Test prompt",
                    on_title_generated=None,
                )

                stored = await manager.get_session_title(session_id)
                assert title == "Generated Test Title", "Title generation failed"
                assert stored == "Generated Test Title", "Title storage failed"
                print("  ✓ StorageManager title generation works")
    except AssertionError as e:
        print(f"  ✗ StorageManager test failed: {e}")
        all_passed = False

    # 3. Test log_session triggers generation
    print("\n[3/5] Testing log_session triggers title generation...")
    try:
        config = StorageConfig(
            providers=[MemoryStorageConfig()],
            title_generation_model="test",
        )
        async with StorageManager(config) as manager:
            session_id = "test_trigger_001"

            mock_metadata = SessionMetadata(
                title="Auto Generated",
                emoji="🤖",
                icon="mdi:robot",
            )

            with patch.object(
                StorageManager,
                "_generate_title_core",
                return_value=mock_metadata,
            ):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("PYTEST_CURRENT_TEST", None)

                    await manager.log_session(
                        session_id=session_id,
                        node_name="test_agent",
                        initial_prompt="What is AI?",
                    )
                    await asyncio.sleep(0.1)

                stored = await manager.get_session_title(session_id)
                assert stored == "Auto Generated", "Auto generation failed"
                print("  ✓ log_session triggers generation correctly")
    except AssertionError as e:
        print(f"  ✗ Auto generation test failed: {e}")
        all_passed = False

    # 4. Test OpenCode provider creates session files
    print("\n[4/5] Testing OpenCode provider creates session files...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = OpenCodeStorageConfig(path=tmpdir)
            provider = OpenCodeStorageProvider(config)

            session_data = SessionData(
                session_id="test_oc_001",
                agent_name="test_agent",
                project_id="test_project",
                cwd="/tmp",
                metadata={"title": "OpenCode Test Title"},
            )
            await provider.save_session(session_data)

            # Check file was created
            session_file = Path(tmpdir) / "session" / "test_project" / "test_oc_001.json"
            assert session_file.exists(), f"Session file not created at {session_file}"

            # Verify content
            from agentpool_storage.opencode_provider import helpers

            oc_session = helpers.read_session(session_file)
            assert oc_session is not None, "Failed to read session"
            assert oc_session.title == "OpenCode Test Title", "Title not saved correctly"
            print(f"  ✓ Session file created: {session_file}")
            print(f"  ✓ Title persisted: {oc_session.title}")
    except AssertionError as e:
        print(f"  ✗ OpenCode provider test failed: {e}")
        all_passed = False

    # 5. Test roundtrip (title only - OpenCode format doesn't preserve arbitrary metadata)
    print("\n[5/5] Testing full roundtrip...")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = OpenCodeStorageConfig(path=tmpdir)
            provider = OpenCodeStorageProvider(config)

            # Save
            original = SessionData(
                session_id="test_round_001",
                agent_name="test_agent",
                project_id="test_project",
                metadata={"title": "Round Trip Title"},
            )
            await provider.save_session(original)

            # Load
            loaded = await provider.load_session("test_round_001")
            assert loaded is not None, "Failed to load session"
            assert loaded.title == "Round Trip Title", "Title lost in roundtrip"
            print("  ✓ Roundtrip preserves title correctly")
            print("  ℹ Note: OpenCode format only preserves title, not arbitrary metadata")
    except AssertionError as e:
        print(f"  ✗ Roundtrip test failed: {e}")
        all_passed = False

    # Summary
    print("\n" + "=" * 70)
    if all_passed:
        print("✅ All verification checks passed!")
        print("Session title persistence is working correctly.")
    else:
        print("❌ Some verification checks failed!")
        print("Please review the errors above.")
    print("=" * 70)

    return all_passed


async def test_real_opencode_storage():
    """Test with real OpenCode storage path if available."""
    print("\n" + "=" * 70)
    print("Real OpenCode Storage Test")
    print("=" * 70)

    opencode_path = Path.home() / ".local" / "share" / "opencode" / "storage"
    if not opencode_path.exists():
        print(f"OpenCode storage not found at {opencode_path}")
        print("Skipping real storage test.")
        return True

    print(f"Found OpenCode storage at: {opencode_path}")

    try:
        config = OpenCodeStorageConfig(path=str(opencode_path))
        provider = OpenCodeStorageProvider(config)

        # Create a test session
        test_session_id = "test_session_verify_title"
        test_title = "Verification Test Session"

        session_data = SessionData(
            session_id=test_session_id,
            agent_name="test_agent",
            project_id="test_project",
            metadata={"title": test_title},
        )

        await provider.save_session(session_data)

        # Verify file exists
        session_file = opencode_path / "session" / "test_project" / f"{test_session_id}.json"
        if session_file.exists():
            from agentpool_storage.opencode_provider import helpers

            oc_session = helpers.read_session(session_file)
            if oc_session and oc_session.title == test_title:
                print(f"✅ Test session created with title: {oc_session.title}")
                print(f"   File: {session_file}")

                # Clean up
                session_file.unlink()
                print("   (Test file cleaned up)")
                return True
            else:
                print(
                    f"❌ Title mismatch: expected '{test_title}', got '{oc_session.title if oc_session else None}'"
                )
                return False
        else:
            print(f"❌ Session file not found: {session_file}")
            return False

    except Exception as e:
        print(f"❌ Error testing real storage: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    """Run all verification tests."""
    basic_passed = await verify_all_fixes()

    # Only test real storage if basic tests pass
    if basic_passed:
        real_passed = await test_real_opencode_storage()
        return basic_passed and real_passed
    return False


if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
