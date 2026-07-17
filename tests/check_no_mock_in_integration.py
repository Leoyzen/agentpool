#!/usr/bin/env python
"""Check that @pytest.mark.integration test files don't use MagicMock or Mock.

Integration tests should avoid mocking in favor of real component wiring.
Exceptions: MockTransport (a legitimate test infrastructure helper) is ignored.

Usage:
    python tests/check_no_mock_in_integration.py

Exit codes:
    0 — no integration test file uses MagicMock or Mock
    1 — some integration test files use MagicMock or Mock
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

# Markers that constitute "integration test" designation
INTEGRATION_MARKER = "integration"

# Regex patterns for mock usage we want to detect
MOCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"MagicMock\("),
    re.compile(r"(?<!MockTransport)\bMock\("),
]


def is_integration_test(filepath: Path) -> bool:
    """Check if a test file is marked as integration.

    Recognises:
    - pytestmark = pytest.mark.integration  (module-level assignment)
    - pytestmark = [pytest.mark.integration, ...]  (list assignment)
    - @pytest.mark.integration  (function/class decorator)
    - @pytest.mark.integration(...)  (decorator with args)
    """
    try:
        tree = ast.parse(filepath.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return False

    # Module-level pytestmark assignments
    for node in ast.iter_child_nodes(tree):
        match node:
            case ast.Assign():
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "pytestmark":
                        if _is_integration_attr(node.value):
                            return True
                        if isinstance(node.value, ast.List):
                            for elt in node.value.elts:
                                if isinstance(elt, ast.Attribute) and _is_integration_attr(elt):
                                    return True
            case ast.AnnAssign():
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "pytestmark"
                    and node.value is not None
                ):
                    if _is_integration_attr(node.value):
                        return True
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Attribute) and _is_integration_attr(elt):
                                return True

    # Decorators on functions/classes
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Attribute) and _is_integration_attr(decorator):
                    return True
                if isinstance(decorator, ast.Call):
                    func = decorator.func
                    if isinstance(func, ast.Attribute) and _is_integration_attr(func):
                        return True

    return False


def _is_integration_attr(node: ast.expr) -> bool:
    """Check if an AST node represents pytest.mark.integration."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr != INTEGRATION_MARKER:
        return False
    if isinstance(node.value, ast.Attribute) and node.value.attr == "mark":
        if isinstance(node.value.value, ast.Name) and node.value.value.id == "pytest":
            return True
    return False


def find_mock_lines(filepath: Path) -> list[tuple[int, str]]:
    """Find lines in a file that use MagicMock or Mock.

    Returns list of (line_number, line_text) for offending lines.
    Skips MockTransport usage.
    """
    results: list[tuple[int, str]] = []
    lines = filepath.read_text().splitlines()

    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("#"):
            continue
        # Check for MockTransport usage — if a line has MockTransport(Mock(
        # we still flag it since the inner Mock( is the issue.
        # But "MockTransport(" alone (not followed by Mock) is fine.
        for pattern in MOCK_PATTERNS:
            if pattern.search(line):
                results.append((lineno, line))
                break

    return results


def main() -> int:
    tests_dir = Path(__file__).parent
    violations: list[tuple[Path, list[tuple[int, str]]]] = []

    for py_file in sorted(tests_dir.rglob("test_*.py")):
        if "__pycache__" in str(py_file):
            continue
        # Short-circuit: skip fast if not an integration test
        if not is_integration_test(py_file):
            continue
        mock_lines = find_mock_lines(py_file)
        if mock_lines:
            violations.append((py_file, mock_lines))

    if violations:
        total = sum(len(lines) for _, lines in violations)
        print(f"❌ {len(violations)} integration test file(s) use MagicMock/Mock ({total} occurrence(s)):")
        print()
        for filepath, lines in violations:
            rel = filepath.relative_to(tests_dir.parent)
            print(f"  📄 {rel}")
            for lineno, line in lines:
                print(f"     L{lineno}: {line.strip()}")
            print()
        print("Integration tests should use real components, not mocks.")
        print("Refactor to use TestModel, FunctionModel, or real AgentPool wiring instead.")
        return 1

    print("✅ No integration test files use MagicMock or Mock.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
