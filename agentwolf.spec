import contextlib

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


datas = []
# Collect package metadata for packages that use importlib.metadata
metadata_packages = [
    "agentwolf",
    "llmling-models",
    "pydantic-ai-slim",
    "genai_prices",
    "schemez",
    "tokonomics",
    "pydantic",
    "fastmcp",
    "mcp",
    "typer",
    "rich",
    "httpx",
    "openai",
    "anthropic",
    "google-generativeai",
    "mistralai",
    "opentelemetry-sdk",
    "opentelemetry-api",
    "structlog",
    "sqlmodel",
    "sqlalchemy",
    "pydantic-settings",
    "platformdirs",
]

for pkg in metadata_packages:
    with contextlib.suppress(Exception):  # Package might not be installed or have metadata
        datas += copy_metadata(pkg, recursive=True)

# Collect data files for packages that need them
datas += collect_data_files("certifi")
with contextlib.suppress(Exception):
    datas += collect_data_files("tzdata")
with contextlib.suppress(Exception):
    datas += collect_data_files("zoneinfo")


a = Analysis(  # noqa: F821  # pyright: ignore[reportUndefinedVariable]  # ty:ignore[unresolved-reference]
    ["src/agentwolf/__main__.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "agentwolf",
        "agentwolf_cli",
        "agentwolf_config",
        "agentwolf_commands",
        "agentwolf_storage",
        "agentwolf_prompts",
        "agentwolf_server",
        "agentwolf_toolsets",
        "acp",
        "acp.bridge",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["runtime_hook.py"],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)  # noqa: F821  # pyright: ignore[reportUndefinedVariable]  # ty:ignore[unresolved-reference]

exe = EXE(  # noqa: F821  # pyright: ignore[reportUndefinedVariable]  # ty:ignore[unresolved-reference]
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agentwolf",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
