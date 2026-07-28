#!/usr/bin/env python
"""Check that every VCR cassette has a corresponding test function.

Cassette convention:
    tests/cassettes/vcr/<test_module>/<test_function>.yaml

This script walks ``tests/cassettes/`` recursively, extracts the test module
and function names from each cassette path, and verifies that:

1. A test file ``tests/vcr/<test_module>.py`` (or ``tests/<test_module>.py``)
   exists.
2. The test file defines a function named ``<test_function>``.

Exit codes:
    0 — all cassettes have a corresponding test function
    1 — some cassettes are orphaned (no matching test function found)

Usage:
    python tests/check_cassettes.py
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
CASSETTES_DIR = TESTS_DIR / "cassettes"
VCR_TESTS_DIR = TESTS_DIR / "vcr"


def _find_test_file(test_module: str) -> Path | None:
    """Find a test file matching ``test_module`` in tests/vcr/ or tests/.

    Args:
        test_module: e.g. ``test_native_basic`` (without ``.py``).

    Returns:
        Path to the test file, or ``None`` if not found.
    """
    candidates: list[Path] = [
        VCR_TESTS_DIR / f"{test_module}.py",
        TESTS_DIR / f"{test_module}.py",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _test_file_has_function(test_file: Path, function_name: str) -> bool:
    """Check if ``test_file`` defines a top-level function named ``function_name``."""
    try:
        tree: ast.Module = ast.parse(test_file.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            return True
    return False


def _collect_cassettes(cassettes_dir: Path) -> list[Path]:
    """Collect all ``.yaml`` cassette files under ``cassettes_dir``."""
    if not cassettes_dir.is_dir():
        return []
    return sorted(cassettes_dir.rglob("*.yaml"))


def main() -> int:
    """Run the cassette hygiene check. Returns process exit code."""
    cassettes = _collect_cassettes(CASSETTES_DIR)
    if not cassettes:
        print(f"No cassettes found under {CASSETTES_DIR.relative_to(REPO_ROOT)}")
        return 0

    orphaned: list[tuple[Path, str]] = []
    for cassette in cassettes:
        rel = cassette.relative_to(CASSETTES_DIR)
        parts = rel.parts
        # Expected layout: [vcr,] <test_module>, <test_function>.yaml
        if len(parts) < 2:
            print(f"WARNING: cassette {rel} has unexpected path layout — skipping")
            continue
        test_function = parts[-1].removesuffix(".yaml")
        test_module = parts[-2]
        # Handle parametrized test names: test_func[param_id] → test_func
        if "[" in test_function:
            test_function = test_function.split("[")[0]
        test_file = _find_test_file(test_module)
        if test_file is None:
            orphaned.append((cassette, f"no test file tests/vcr/{test_module}.py"))
            continue
        if not _test_file_has_function(test_file, test_function):
            orphaned.append(
                (cassette, f"no function {test_function}() in {test_file.relative_to(REPO_ROOT)}"),
            )

    if orphaned:
        print(f"\nERROR: {len(orphaned)} orphaned cassette(s) found:\n")
        for cassette, reason in orphaned:
            print(f"  {cassette.relative_to(REPO_ROOT)} — {reason}")
        print(
            "\nTo fix: either add the missing test function or delete the cassette.\n"
            "Cassettes live at tests/cassettes/vcr/<test_module>/<test_function>.yaml",
        )
        return 1

    print(f"OK: {len(cassettes)} cassette(s) all have matching test functions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
