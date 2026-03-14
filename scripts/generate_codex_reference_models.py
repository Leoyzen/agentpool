"""Generate reference Pydantic models from the Codex app-server JSON Schema.

This script uses the ``codex`` CLI to generate the JSON Schema for the
app-server protocol, then converts it into Pydantic models using
``datamodel-codegen``.

Requirements:
    - ``codex`` CLI installed and on PATH
    - ``uv tool install datamodel-code-generator``

Usage:
    python scripts/generate_codex_reference_models.py
    # Or with uv:
    uv run python scripts/generate_codex_reference_models.py
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_ROOT / "src" / "codex_adapter" / "reference"
OUTPUT_FILE = OUTPUT_DIR / "v2_protocol.py"
SCHEMA_FILENAME = "codex_app_server_protocol.v2.schemas.json"


def check_codex_cli() -> str:
    """Check that the codex CLI is available and return its path."""
    codex = shutil.which("codex")
    if not codex:
        print("Error: 'codex' CLI not found on PATH.", file=sys.stderr)
        print("Install it from https://github.com/openai/codex", file=sys.stderr)
        sys.exit(1)
    print(f"Found codex CLI: {codex}")
    return codex


def generate_schema(codex: str, out_dir: Path) -> Path:
    """Run ``codex app-server generate-json-schema`` and return schema path."""
    print("Generating JSON Schema from codex app-server...")
    result = subprocess.run(
        [
            codex,
            "app-server",
            "generate-json-schema",
            "--out",
            str(out_dir),
            "--experimental",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"Error generating schema: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    schema_path = out_dir / SCHEMA_FILENAME
    if not schema_path.exists():
        print(f"Error: Expected schema file not found: {schema_path}", file=sys.stderr)
        sys.exit(1)

    lines = schema_path.read_text().count("\n")
    print(f"  Generated schema: {lines} lines")
    return schema_path


def generate_models(schema_path: Path) -> None:
    """Generate Pydantic models using datamodel-codegen."""
    print("Generating Pydantic models with datamodel-codegen...")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        "uv",
        "tool",
        "run",
        "--from",
        "datamodel-code-generator",
        "datamodel-codegen",
        "--input",
        str(schema_path),
        "--input-file-type",
        "jsonschema",
        "--output",
        str(OUTPUT_FILE),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--target-python-version",
        "3.13",
        "--target-pydantic-version",
        "2",
        "--use-standard-collections",
        "--use-standard-primitive-types",
        "--snake-case-field",
        "--field-constraints",
        "--use-schema-description",
        "--use-field-description",
        "--use-double-quotes",
        "--reuse-model",
        "--enum-field-as-literal",
        "all",
        "--use-one-literal-as-default",
        "--use-title-as-name",
        "--formatters",
        "ruff-check",
        "ruff-format",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        print(f"Error generating models: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    if result.stderr:
        print(f"  Warnings: {result.stderr}")

    print(f"  Generated models to {OUTPUT_FILE}")


def post_process() -> None:
    """Fix known datamodel-codegen issues in the generated file."""
    print("Post-processing generated models...")
    content = OUTPUT_FILE.read_text()
    original = content

    # Replace constr(pattern=...) with str in dict key positions.
    content = re.sub(r"constr\(pattern=r?['\"][^'\"]*['\"]\)", "str", content)

    # Remove unused constr import if no constr() usages remain outside imports
    non_import_lines = [
        line for line in content.splitlines() if not line.strip().startswith(("from ", "import "))
    ]
    if "constr" not in "\n".join(non_import_lines):
        content = re.sub(r",\s*constr", "", content)
        content = re.sub(r"constr,\s*", "", content)
        content = content.replace("from pydantic import constr\n", "")

    if content != original:
        OUTPUT_FILE.write_text(content)
        print("  Applied post-processing fixes")
    else:
        print("  No post-processing needed")


def write_init() -> None:
    """Write __init__.py for the reference package if it doesn't exist."""
    init_file = OUTPUT_DIR / "__init__.py"
    if not init_file.exists():
        init_file.write_text(
            '"""Reference models generated from the Codex app-server JSON Schema.\n'
            "\n"
            "These models are auto-generated and should not be edited manually.\n"
            "Re-generate with: python scripts/generate_codex_reference_models.py\n"
            '"""\n'
        )
        print(f"  Created {init_file}")


def verify_output() -> None:
    """Verify the generated file exists and has content."""
    if not OUTPUT_FILE.exists():
        print(f"Error: Output file {OUTPUT_FILE} not found!", file=sys.stderr)
        sys.exit(1)

    content = OUTPUT_FILE.read_text()
    lines = content.count("\n")
    model_count = content.count("class ")
    print(f"  Verified: {lines} lines, {model_count} model classes")


def main() -> None:
    """Main entry point."""
    print("=" * 60)
    print("Codex App-Server Reference Model Generation")
    print("=" * 60)

    try:
        codex = check_codex_cli()

        with tempfile.TemporaryDirectory(prefix="codex-schema-") as tmpdir:
            schema_path = generate_schema(codex, Path(tmpdir))
            generate_models(schema_path)

        post_process()
        write_init()
        verify_output()

        print("\n" + "=" * 60)
        print("Success! Reference models generated.")
        print("=" * 60)
        print(f"\nOutput: {OUTPUT_FILE}")

    except subprocess.CalledProcessError as e:
        print(f"\nError: Command failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
