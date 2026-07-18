#!/usr/bin/env python
"""Check that every ``@pytest.mark.vcr`` test has a corresponding cassette file.

This is the inverse hygiene check of ``check_cassettes.py``. It walks
``tests/vcr/`` for test files, parses the AST to find VCR-marked test
functions (either via ``@pytest.mark.vcr`` decorator or
``pytestmark = pytest.mark.vcr`` module-level assignment), and checks that
each has a corresponding cassette file at:
    tests/cassettes/vcr/<test_module>/<test_function>.yaml

!!! note
    This script does NOT fail — it only WARNS. VCR cassettes are
    ``[HUMAN-REQUIRED]`` (recorded manually with ``--record-mode=once`` and
    a real API key). Tests may be merged before cassettes are recorded; the
    warning surfaces the recording backlog without blocking CI.

Exit codes:
    0 — always (warnings only)

Usage:
    python tests/check_vcr_tests.py
"""

from __future__ import annotations

import ast
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
VCR_TESTS_DIR = TESTS_DIR / "vcr"
CASSETTES_DIR = TESTS_DIR / "cassettes" / "vcr"


def _is_vcr_mark(node: ast.expr) -> bool:
    """Return True if ``node`` is an attribute access on ``pytest.mark.vcr``."""
    # Match: pytest.mark.vcr
    if isinstance(node, ast.Attribute) and node.attr == "vcr":
        inner = node.value
        if isinstance(inner, ast.Attribute) and inner.attr == "mark":
            inner2 = inner.value
            if isinstance(inner2, ast.Name) and inner2.id == "pytest":
                return True
    return False


def _has_vcr_decorator(decorator_list: list[ast.expr]) -> bool:
    """Check if any decorator in the list is ``@pytest.mark.vcr`` or ``@pytest.mark.vcr(...)``."""
    for dec in decorator_list:
        # @pytest.mark.vcr (no call)
        if _is_vcr_mark(dec):
            return True
        # @pytest.mark.vcr(...) (call form)
        if isinstance(dec, ast.Call) and _is_vcr_mark(dec.func):
            return True
    return False


def _module_has_vcr_pytestmark(tree: ast.Module) -> bool:
    """Check if module has ``pytestmark = pytest.mark.vcr`` (or list form)."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    if _is_vcr_mark(node.value):
                        return True
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Attribute) and _is_vcr_mark(elt):
                                return True
        elif isinstance(node, ast.AnnAssign) and (
            isinstance(node.target, ast.Name)
            and node.target.id == "pytestmark"
            and node.value is not None
        ):
            if _is_vcr_mark(node.value):
                return True
            if isinstance(node.value, ast.List):
                for elt in node.value.elts:
                    if isinstance(elt, ast.Attribute) and _is_vcr_mark(elt):
                        return True
    return False


def _collect_vcr_test_functions(test_file: Path) -> list[str] | None:
    """Collect VCR-marked test function names from ``test_file``.

    Returns:
        List of test function names, or ``None`` if the file is not VCR-marked
        (neither module-level ``pytestmark`` nor per-function ``@pytest.mark.vcr``).
    """
    try:
        tree: ast.Module = ast.parse(test_file.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return None

    module_marked = _module_has_vcr_pytestmark(tree)
    vcr_functions: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("test_"):
                continue
            if module_marked or _has_vcr_decorator(node.decorator_list):
                vcr_functions.append(node.name)

    if not vcr_functions:
        return None
    return vcr_functions


def _cassette_exists(test_module: str, test_function: str) -> bool:
    """Check if a cassette file exists for the given test module/function."""
    return (CASSETTES_DIR / test_module / f"{test_function}.yaml").is_file()


def main() -> int:
    """Run the VCR test hygiene check. Always returns 0 (warnings only)."""
    if not VCR_TESTS_DIR.is_dir():
        print(f"No VCR test directory found at {VCR_TESTS_DIR.relative_to(REPO_ROOT)}")
        return 0

    test_files = sorted(VCR_TESTS_DIR.rglob("test_*.py"))
    missing: list[tuple[str, str]] = []
    total_vcr_tests = 0

    for test_file in test_files:
        test_module = test_file.stem
        vcr_functions = _collect_vcr_test_functions(test_file)
        if vcr_functions is None:
            continue
        for func_name in vcr_functions:
            total_vcr_tests += 1
            if not _cassette_exists(test_module, func_name):
                missing.append((test_module, func_name))

    if missing:
        print(
            f"WARNING: {len(missing)} of {total_vcr_tests} VCR test(s) missing cassettes "
            "(expected — record with --record-mode=once):",
        )
        for test_module, func_name in missing:
            rel_cassette = (CASSETTES_DIR / test_module / f"{func_name}.yaml").relative_to(
                REPO_ROOT
            )
            print(f"  {test_module}.py::{func_name} → {rel_cassette}")
        print(
            "\nTo record: export OPENAI_API_KEY=sk-... && "
            "uv run pytest tests/vcr/<test_module>.py --record-mode=once",
        )
        return 0

    if total_vcr_tests == 0:
        print(f"No VCR-marked tests found under {VCR_TESTS_DIR.relative_to(REPO_ROOT)}")
    else:
        print(f"OK: all {total_vcr_tests} VCR test(s) have matching cassettes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
