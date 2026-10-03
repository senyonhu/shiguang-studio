"""Release identities are part of the public provenance contract."""
import json
from pathlib import Path
import tomllib
from renpy_bridge import __version__
from renpy_bridge.environment import get_environment_info
from renpy_bridge.generator import GENERATOR_VERSION


def test_public_environment_matches_manifest_package_and_generator():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "plugin/renpy-bridge/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    package = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert get_environment_info(False)["version"] == manifest["version"] == package["project"]["version"] == __version__ == GENERATOR_VERSION
