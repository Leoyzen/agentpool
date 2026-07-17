#!/usr/bin/env python
"""Check that every test file has at least one layer marker (unit, integration, vcr, or e2e).

Usage:
    python tests/check_markers.py

Exit codes:
    0 — all test files have at least one layer marker
    1 — some test files are missing layer markers
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

LAYER_MARKERS = {"unit", "integration", "vcr", "e2e"}

# Markers that imply a layer marker is present via pytestmark
PYTESTMARK_ATTRS = {"pytestmark", "pytest"}


def has_layer_marker(filepath: Path) -> bool:
    """Check if a test file has at least one layer marker.

    Looks for:
    - @pytest.mark.<layer> decorators on test functions
    - pytestmark = pytest.mark.<layer> module-level assignments
    - pytestmark = [pytest.mark.<layer>, ...] list assignments
    """
    try:
        content = filepath.read_text()
        tree = ast.parse(content)
    except (SyntaxError, UnicodeDecodeError):
        return True  # Skip files that can't be parsed

    # Check module-level pytestmark
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    # Check if it's pytest.mark.<layer> or a list containing it
                    if isinstance(node.value, ast.Attribute):
                        if _is_layer_mark(node.value):
                            return True
                    elif isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Attribute) and _is_layer_mark(elt):
                                return True
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "pytestmark"
                and node.value is not None
            ):
                if isinstance(node.value, ast.Attribute):
                    if _is_layer_mark(node.value):
                        return True
                elif isinstance(node.value, ast.List):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Attribute) and _is_layer_mark(elt):
                            return True

    # Check @pytest.mark.<layer> decorators on functions
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Attribute) and _is_layer_mark(decorator):
                    return True
                # Handle pytest.mark.<layer>(...) calls
                if isinstance(decorator, ast.Call):
                    func = decorator.func
                    if isinstance(func, ast.Attribute) and _is_layer_mark(func):
                        return True

    return False


def _is_layer_mark(node: ast.Attribute) -> bool:
    """Check if an AST node represents pytest.mark.<layer>."""
    if not isinstance(node, ast.Attribute):
        return False
    if node.attr not in LAYER_MARKERS:
        return False
    # Check that the value is pytest.mark
    if isinstance(node.value, ast.Attribute) and node.value.attr == "mark":
        if isinstance(node.value.value, ast.Name) and node.value.value.id == "pytest":
            return True
    return False


def main() -> int:
    tests_dir = Path(__file__).parent
    missing: list[Path] = []

    for py_file in sorted(tests_dir.rglob("test_*.py")):
        # Skip __pycache__ and similar
        if "__pycache__" in str(py_file):
            continue
        if not has_layer_marker(py_file):
            missing.append(py_file)

    if missing:
        print(f"❌ {len(missing)} test files missing layer markers (unit/integration/vcr/e2e):")
        for f in missing:
            rel = f.relative_to(tests_dir.parent)
            print(f"  {rel}")
        print(f"\nAdd @pytest.mark.unit, @pytest.mark.integration, @pytest.mark.vcr, or @pytest.mark.e2e to each file.")
        return 1
    print(f"✅ All test files have at least one layer marker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
