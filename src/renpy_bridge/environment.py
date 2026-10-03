import importlib.metadata
import os
from pathlib import Path
import json
from . import __version__


def bridge_home() -> Path:
    return Path(os.environ.get("RENPY_BRIDGE_HOME", Path(__file__).resolve().parents[2])).resolve()


def get_environment_info(include_runtime: bool = True) -> dict:
    root = bridge_home()
    result = {"ok": True, "plugin": "renpy-bridge", "version": __version__, "schema_version": "1.0",
              "home": str(root), "mcp_sdk": importlib.metadata.version("mcp"),
              "protocol_ready": True, "engine_verified": False}
    lock = root / "runtimes/sdk-lock.json"
    if include_runtime and lock.exists():
        result["runtime"] = json.loads(lock.read_text(encoding="utf-8"))
    return result


def minigame_capabilities() -> dict:
    root = bridge_home()
    return {
        'ok': True, 'project_schemas': ['1.0', '1.1'], 'package_format': '1.0', 'protocol': '1.0',
        'window_mode': 'embedded_same_top_level_window', 'same_window_verified': False,
        'platform_target': 'Windows 11 x64',
        'other_platforms_verified': False, 'network_policy': 'offline_local_content_only',
        'save_modes': ['entry_restart', 'checkpoint'],
        'variable_types': ['int', 'float', 'bool', 'str'],
        'effect_operations': ['set', 'add'],
        'limits': {'package_bytes': 268435456, 'package_files': 4096, 'snapshot_bytes': 65536, 'json_depth': 32,
                   'qa_scenarios_per_job': 1024, 'qa_scenarios_per_batch': 64},
        'prototype_packages_publishable': False,
        'host_present': (root / 'runtimes/html-minigame-embedded-host/win-x64/HtmlMinigameEmbeddedHost.exe').is_file(),
        'gameplay_verified': False,
        'verification_note': '具体revision的真实玩法、存档、窗口和发行证据须单独查询，不能由能力发现推断。',
    }
