"""Prepare and validate this project's local Codex MCP plugin.

This script touches only plugin files in the project. It does not register a
marketplace, install a plugin, read Codex account configuration, or start a model
session. M0 intentionally uses the project's virtual environment; portable
runtime packaging belongs to M7.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def expected_mcp(project_root: Path) -> dict[str, Any]:
    python_path = project_root / ".venv" / "Scripts" / "python.exe"
    if not python_path.exists():
        posix_python = project_root / ".venv" / "bin" / "python"
        if posix_python.exists():
            python_path = posix_python
    return {
        "mcpServers": {
            "renpy_bridge": {
                "command": str(python_path),
                "args": ["-m", "renpy_bridge.server"],
                "env": {
                    "RENPY_BRIDGE_HOME": str(project_root),
                    "PYTHONPATH": str(project_root / "src"),
                    "PYTHONUTF8": "1",
                    "PYTHONUNBUFFERED": "1",
                },
            }
        }
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Project root; defaults to the parent of scripts/.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Refresh local plugin .mcp.json paths. Never modifies Codex config.",
    )
    parser.add_argument(
        "--installed-root",
        type=Path,
        help="Optional installed plugin copy to compare; no auto-discovery.",
    )
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    marketplace_root = project_root / "plugin"
    plugin_root = marketplace_root / "renpy-bridge"
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    marketplace_path = marketplace_root / ".agents" / "plugins" / "marketplace.json"
    mcp_path = plugin_root / ".mcp.json"
    problems: list[str] = []
    details: dict[str, Any] = {
        "schema_version": "1.0",
        "stage": "M0",
        "project_root": str(project_root),
        "marketplace_root": str(marketplace_root),
        "plugin_root": str(plugin_root),
        "configuration_written": False,
        "global_configuration_modified": False,
    }
    try:
        manifest = read_json(manifest_path)
        marketplace = read_json(marketplace_path)
        if manifest.get("name") != "renpy-bridge":
            problems.append("Plugin manifest name must be renpy-bridge.")
        if manifest.get("mcpServers") != "./.mcp.json":
            problems.append("Plugin manifest must declare ./.mcp.json.")
        if marketplace.get("name") != "renpy-bridge-local":
            problems.append("Marketplace name must be renpy-bridge-local.")
        entries = [
            entry
            for entry in marketplace.get("plugins", [])
            if entry.get("name") == "renpy-bridge"
        ]
        if len(entries) != 1 or entries[0].get("source") != {
            "source": "local",
            "path": "./renpy-bridge",
        }:
            problems.append("Marketplace must point once to ./renpy-bridge.")
        if problems:
            details["problems"] = problems
            print(json.dumps(details, ensure_ascii=False, indent=2))
            return 1

        expected = expected_mcp(project_root)
        if args.write:
            temporary = mcp_path.with_name(".mcp.json.tmp")
            temporary.write_text(
                json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(mcp_path)
            details["configuration_written"] = True
        actual = read_json(mcp_path)
        details["configuration_matches"] = actual == expected
        if actual != expected:
            problems.append("Local MCP configuration differs; rerun with --write.")

        runtime_python = Path(expected["mcpServers"]["renpy_bridge"]["command"])
        details["runtime_python"] = str(runtime_python)
        details["runtime_python_exists"] = runtime_python.is_file()
        details["server_module_exists"] = (
            project_root / "src" / "renpy_bridge" / "server.py"
        ).is_file()
        if not details["runtime_python_exists"]:
            problems.append("Project virtual environment Python is not ready.")
        if not details["server_module_exists"]:
            problems.append("renpy_bridge.server source module is not ready.")
        details["files"] = [
            {"path": str(path), "sha256": digest(path)}
            for path in (manifest_path, marketplace_path, mcp_path)
        ]
        if args.installed_root:
            installed_root = args.installed_root.resolve()
            comparisons = []
            for relative in (Path(".codex-plugin/plugin.json"), Path(".mcp.json")):
                installed_path = installed_root / relative
                equal = (
                    installed_path.is_file()
                    and installed_path.read_bytes() == (plugin_root / relative).read_bytes()
                )
                comparisons.append({"file": str(relative), "matches": equal})
                if not equal:
                    problems.append(f"Installed copy differs: {relative}")
            details["installed_root"] = str(installed_root)
            details["installed_comparisons"] = comparisons
    except (OSError, ValueError, TypeError, KeyError) as error:
        problems.append(f"{type(error).__name__}: {error}")
    details["problems"] = problems
    details["ready_for_host_test"] = not problems
    print(json.dumps(details, ensure_ascii=False, indent=2))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())

