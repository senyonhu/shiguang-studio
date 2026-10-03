"""Generation identities and complete immutable installation, not HTML gameplay.

Packaging-only tests use explicitly inert host bytes and a minimal runtime
module. Actual SDK/browser coverage is recorded by the independent probes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

from renpy_bridge import generator
from renpy_bridge.derived_integrity import record_derived_sources, verify_derived_sources
from renpy_bridge.errors import BridgeError
from renpy_bridge.minigame_packages import validate_directory
from renpy_bridge.models import Project
from renpy_bridge.sdk import ProcessResult, SdkInfo


def manifest(directory):
    files = [{"path": p.relative_to(directory).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
             for p in directory.rglob("*") if p.is_file() and p.name != "manifest.json"]
    value = {"format_version": "1.0", "game_id": "puzzle", "version": "1.0", "entry": "index.html", "protocol": "1.0",
        "inputs": {"difficulty": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2}},
        "results": {"outcomes": ["win", "lose"], "fields": {"score": {"type": "integer", "minimum": 0, "maximum": 100}}},
        "save_capabilities": {"entry_restart": True, "checkpoint": False, "snapshot_version": None}, "files": files}
    (directory / "manifest.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


@pytest.fixture
def inputs(tmp_path):
    root = tmp_path / "authoring"
    temporary = tmp_path / "source"
    temporary.mkdir()
    (temporary / "index.html").write_text("<html>packaging fixture</html>", encoding="utf-8")
    (temporary / "LICENSE.txt").write_text("License must accompany the complete resource package.", encoding="utf-8")
    (temporary / "data").mkdir()
    (temporary / "data/level.txt").write_text("关卡文本不能被发行排除规则丢弃。", encoding="utf-8")
    definition = manifest(temporary)
    info = validate_directory(temporary)
    source = root / "minigames" / info.digest
    shutil.copytree(temporary, source)
    project = Project(schema_version="1.1", project_id="generator_mg", name="生成验收", save_namespace="generator_mg",
        entry_frame_id="entry", acts={"act": {"act_id": "act", "frame_ids": ["entry", "win", "lose", "cancel", "error", "fallback"]}},
        minigames={"puzzle": {"game_id": "puzzle", "package_digest": info.digest, "package_version": "1.0",
                              "path": "minigames/" + info.digest, "manifest": definition}},
        variables={"affection": {"variable_id": "affection", "type": "int", "default": 0, "minimum": 0, "maximum": 100}},
        frames={"entry": {"frame_id": "entry", "act_id": "act", "dialogue": {"text": "真实剧情入口"},
            "flow": {"kind": "minigame", "game_id": "puzzle", "instance_id": "puzzle_at_door",
                "inputs": {"difficulty": {"kind": "literal", "value": 2}}, "save_mode": "entry_restart",
                "result_branches": [{"branch_id": "won", "outcome": "win", "effects": [{"effect_id": "reward", "op": "add", "variable_id": "affection", "value": {"kind": "literal", "value": 2}}], "target_frame_id": "win"},
                                    {"branch_id": "lost", "outcome": "lose", "target_frame_id": "lose"}],
                "cancel_target_frame_id": "cancel", "error_target_frame_id": "error", "fallback_target_frame_id": "fallback"}},
            **{name: {"frame_id": name, "act_id": "act", "dialogue": {"text": name}, "flow": {"kind": "end"}} for name in ("win", "lose", "cancel", "error", "fallback")}})
    host = tmp_path / "host"
    host.mkdir()
    (host / "HtmlMinigameEmbeddedHost.exe").write_bytes(b"inert packaging fixture; never execute")
    (host / "WebView2Loader.dll").write_bytes(b"inert native dependency")
    (host / "THIRD_PARTY_NOTICES.txt").write_text("host dependency license", encoding="utf-8")
    return root, source, project, host


def test_minigame_call_identity_survives_dialogue_order_and_layout_edit(inputs):
    root, source, project, host = inputs
    original, mapping = generator._build_story(project, {})
    edited = project.model_dump(mode="json")
    edited["frames"]["entry"]["dialogue"]["text"] = "修改后的入口对白"
    edited["frames"]["entry"]["order"] = 8
    changed, changed_map = generator._build_story(Project.model_validate(edited), {})
    assert mapping["entry"]["minigame"] == changed_map["entry"]["minigame"]
    info = mapping["entry"]["minigame"]
    assert f'call rb_minigame_run("puzzle_at_door") from {info["return_label"]}' in original
    assert original.count("jump expression _return") == 1
    assert original != changed


def test_silent_minigame_calls_immediately_without_new_dismiss_requirement(inputs):
    root, source, project, host = inputs
    data = project.model_dump(mode="json")
    data["frames"]["entry"]["dialogue"] = None
    story, mapping = generator._build_story(Project.model_validate(data), {})
    entry = mapping["entry"]
    block = "\n".join(story.splitlines()[entry["start_line"] - 1:entry["end_line"]])
    assert "    pause" not in block
    assert "    call rb_minigame_run" in block
    assert entry["dialogue_line"] is None


def test_registered_package_modification_is_rejected_before_gui_creation(inputs, tmp_path, monkeypatch):
    root, source, project, host = inputs
    (source / "data/level.txt").write_text("changed", encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("damaged input must be rejected before running GUI generation")
    monkeypatch.setattr(generator, "run_sdk", forbidden)
    sdk = SdkInfo(tmp_path / "renpy-8.5.3", "8.5.3.26051504", tmp_path / "python", tmp_path / "renpy.py")
    target = tmp_path / "output"
    with pytest.raises(generator.GenerationError, match="MINIGAME_PACKAGE_HASH_MISMATCH"):
        generator.generate_project(project, root, target, sdk, minigame_host_root=host)
    assert not target.exists()


def test_missing_host_fails_before_creating_output(inputs, tmp_path):
    root, source, project, host = inputs
    sdk = SdkInfo(tmp_path / "renpy-8.5.3", "8.5.3.26051504", tmp_path / "python", tmp_path / "renpy.py")
    target = tmp_path / "output"
    with pytest.raises(generator.GenerationError, match="MINIGAME_RUNTIME_MISSING"):
        generator.generate_project(project, root, target, sdk, minigame_host_root=tmp_path / "absent")
    assert not target.exists()


def install_fixture(inputs, tmp_path, monkeypatch):
    root, source, project, host = inputs
    # This module is only an inert packaging stand-in, not runtime coverage.
    stub = SimpleNamespace(RPY_RUNTIME_SOURCE="default rb_minigame_state = None\nlabel rb_minigame_run(instance_id):\n    return\n",
        runtime_source_files=lambda: {"game/python-packages/renpy_bridge_minigames/__init__.py": "# inert packaging fixture\n"})
    monkeypatch.setitem(sys.modules, "renpy_bridge.minigame_runtime", stub)
    target = tmp_path / "generated"
    (target / "game").mkdir(parents=True)
    games = generator._minigame_inputs(project, root, allow_prototype=False)
    host_files = generator._host_inventory(host)
    installed = generator._install_minigames(project, target, games, host, host_files, allow_prototype=False)
    return target, installed


def test_complete_install_keeps_licenses_text_data_and_full_host_dependencies(inputs, tmp_path, monkeypatch):
    target, installed = install_fixture(inputs, tmp_path, monkeypatch)
    root, source, project, host = inputs
    package_root = target / installed["minigames"]["puzzle"]["package_root"]
    assert {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob("*") if p.is_file()} == {
        p.relative_to(package_root).as_posix(): p.read_bytes() for p in package_root.rglob("*") if p.is_file()}
    assert (target / "html-runtime/win-x64/THIRD_PARTY_NOTICES.txt").read_bytes() == (host / "THIRD_PARTY_NOTICES.txt").read_bytes()
    config = json.loads((target / "game/bridge-minigames.json").read_text(encoding="utf-8"))
    assert config["instances"]["puzzle_at_door"]["frame_id"] == "entry"
    assert config["frame_labels"]["win"] == generator.stable_label("win")
    assert config["host"]["relative_executable"] == "html-runtime/win-x64/HtmlMinigameEmbeddedHost.exe"
    assert config["host"]["window_mode"] == "embedded_same_top_level_window"
    assert config["host"]["footer_fraction"] == 0.1
    assert "default rb_story_variables = {'affection': 0}" in (target / "game/bridge_minigame_runtime.rpy").read_text(encoding="utf-8")


@pytest.mark.parametrize("damage", ["package-file", "host-file", "python-file", "extra-package-file", "configuration"])
def test_installed_runtime_and_packages_are_integrity_protected(inputs, tmp_path, monkeypatch, damage):
    target, installed = install_fixture(inputs, tmp_path, monkeypatch)
    record_derived_sources(target, managed_roots=installed["managed_roots"])
    package_root = target / installed["minigames"]["puzzle"]["package_root"]
    path = {"package-file": package_root / "LICENSE.txt", "host-file": target / "html-runtime/win-x64/HtmlMinigameEmbeddedHost.exe",
            "python-file": target / "game/python-packages/renpy_bridge_minigames/__init__.py",
            "extra-package-file": package_root / "extra.txt", "configuration": target / "game/bridge-minigames.json"}[damage]
    path.write_bytes(b"external change")
    with pytest.raises(BridgeError, match="派生|生成|改动"):
        verify_derived_sources(target, tmp_path / "integrity-evidence")


def test_fixed_browser_complete_tree_and_license_are_installed_and_protected(inputs, tmp_path, monkeypatch):
    root, source, project, host = inputs
    target, installed = install_fixture(inputs, tmp_path, monkeypatch)
    # A second fresh tree keeps this packaging stand-in explicitly inert.
    destination = tmp_path / "with-fixed-browser"
    (destination / "game").mkdir(parents=True)
    browser = tmp_path / "fixed-browser"
    (browser / "Locales").mkdir(parents=True)
    (browser / "msedgewebview2.exe").write_bytes(b"inert fixed browser; never execute")
    (browser / "LICENSE.txt").write_text("browser license", encoding="utf-8")
    (browser / "Locales/zh-CN.pak").write_bytes(b"inert locale resource")
    browser_files = generator._host_inventory(browser, required_file="msedgewebview2.exe")
    result = generator._install_minigames(project, destination, generator._minigame_inputs(project, root, allow_prototype=False),
        host, generator._host_inventory(host), allow_prototype=False, fixed_runtime_root=browser,
        fixed_runtime_files=browser_files, fixed_runtime_version="154.0.4258.53")
    configuration = json.loads((destination / "game/bridge-minigames.json").read_text(encoding="utf-8"))
    assert configuration["fixed_runtime"] == {"relative_root": "html-runtime/webview2-fixed", "version": "154.0.4258.53", "files": browser_files}
    assert (destination / "html-runtime/webview2-fixed/LICENSE.txt").read_bytes() == (browser / "LICENSE.txt").read_bytes()
    record_derived_sources(destination, managed_roots=result["managed_roots"])
    (destination / "html-runtime/webview2-fixed/Locales/zh-CN.pak").write_bytes(b"changed locale")
    with pytest.raises(BridgeError):
        verify_derived_sources(destination, tmp_path / "browser-integrity-evidence")


def test_pure_story_branch_output_keeps_original_script_contract():
    project = Project(project_id="pure", name="纯剧情", save_namespace="pure", entry_frame_id="end",
        acts={"act": {"act_id": "act", "frame_ids": ["end"]}},
        frames={"end": {"frame_id": "end", "act_id": "act", "dialogue": {"text": "原对白"}, "flow": {"kind": "end"}}})
    story, mapping = generator._build_story(project, {})
    label = generator.stable_label("end")
    assert story == f'''# Generated by renpy-bridge. Edit the authoring project, not this file.

default rb_current_frame_id = None

label start:
    jump {label}

label {label}:
    $ rb_current_frame_id = "end"
    $ quick_menu = True
    scene onlayer rb_background
    scene onlayer rb_characters
    scene onlayer rb_decorations
    stop music
    voice None
    window auto
    narrator "原对白"
    return
'''
    assert "minigame" not in story and "minigame" not in mapping["end"]


def test_compatibility_can_install_retained_configuration_before_first_registry(inputs, tmp_path, monkeypatch):
    root, source, project, host = inputs
    sdk = SdkInfo(tmp_path / "sdk", "8.5.3.26051504", tmp_path / "python", tmp_path / "renpy.py")
    browser = tmp_path / "Microsoft.WebView2.FixedVersionRuntime.154.0.4258.53.x64"
    browser.mkdir()
    (browser / "msedgewebview2.exe").write_bytes(b"inert browser; never execute")
    stub = SimpleNamespace(RPY_RUNTIME_SOURCE="default rb_minigame_state = None\n",
        runtime_source_files=lambda: {"python-packages/renpy_bridge_minigames/__init__.py": "# inert fixture\n"})
    monkeypatch.setitem(sys.modules, "renpy_bridge.minigame_runtime", stub)
    target = tmp_path / "pending-generated"

    def inert_sdk(sdk, project_path, command, args, **kwargs):
        if command == "generate_gui":
            game = target / "game"
            game.mkdir(parents=True)
            (game / "SourceHanSansLite.ttf").write_bytes(b"inert font")
            (game / "options.rpy").write_text('define config.name = "fixture"\ndefine config.save_directory = "fixture"\ndefine build.name = "fixture"\n', encoding="utf-8")
            (game / "gui.rpy").write_text('define gui.text_size = 1\ndefine gui.textbox_height = 1\ndefine gui.dialogue_xpos = 1\ndefine gui.dialogue_width = 1\n', encoding="utf-8")
        return ProcessResult([], str(tmp_path), 0, "", "", 0.0, False, 0, None)

    monkeypatch.setattr(generator, "run_sdk", inert_sdk)
    monkeypatch.setattr(generator, "complete_native_gui", lambda sdk, game: [])
    result = generator.generate_project(project, root, target, sdk, minigame_host_root=host,
        minigame_fixed_runtime_root=browser, defer_minigame_integrity=True)
    assert result["derived_sources"]["pending"] is True
    assert not (target / ".bridge-derived-integrity/manifest.json").exists()
    config_path = target / "game/bridge-minigames.json"
    configuration = json.loads(config_path.read_text(encoding="utf-8"))
    configuration["retained_packages"] = {}
    config_path.write_text(json.dumps(configuration), encoding="utf-8")
    record_derived_sources(target, managed_roots=result["derived_sources"]["managed_roots"])
    assert verify_derived_sources(target, tmp_path / "before-damage")["status"] == "passed"
    config_path.write_text("changed after authority was recorded", encoding="utf-8")
    with pytest.raises(BridgeError):
        verify_derived_sources(target, tmp_path / "after-damage")
