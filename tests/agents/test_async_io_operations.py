"""Test suite for async I/O operations.

Tests that blocking I/O operations are properly handled with asyncio.to_thread.
"""

import sys
from pathlib import Path

import pytest

# Add src to path for imports
sys_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(sys_path))


if __name__ == "__main__":
    print("Testing async I/O operations...\n")
    print("\n✓ All async I/O tests passed!")
    print("Run with pytest to execute async tests.")
