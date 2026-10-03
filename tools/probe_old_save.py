"""Real same-SDK old-save probe using private state and two generated revisions.

This is an SDK-icon compatibility fixture, not the user-facing Codex demo.
It creates a native save in revision 2, loads that exact save in revision 4,
then advances into updated future content. No synthetic save or jump is used.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import time
import traceback

from renpy_bridge.compatibility import prepare_previous_release
from renpy_bridge.derived_integrity import record_derived_sources
from renpy_bridge.generator import generate_project
from renpy_bridge.native_qa import NATIVE_QA_SOURCE
from renpy_bridge.sdk import discover_sdk, run_sdk
from renpy_bridge.storage import Store, atomic_json, file_hash


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/m6-save"
SAVED_TEXT = "存档句：读取应恢复旧画面，再推进到新版内容。"
NEW_TEXT = "新版后继：使用新的素材与位置，继续原有帧身份。"


def q(value):
    return json.dumps(value, ensure_ascii=False)


def tag(value):
    return "rb_component_" + hashlib.sha256(value.encode()).hexdigest()[:24]


def helper_source(record_path: Path):
    return NATIVE_QA_SOURCE + f'''init -1 python:
    import json as rb_old_json

    def _rb_old_state():
        transforms = []
        media = []
        seen = set()
        def walk(displayable):
            if id(displayable) in seen:
                return
            seen.add(id(displayable))
            if isinstance(displayable, renpy.display.transform.Transform):
                xpos = displayable.state.xpos
                if xpos is not None:
                    transforms.append(float(xpos))
            filename = getattr(displayable, "filename", None)
            if isinstance(filename, str):
                media.append(filename)
            for child in displayable.visit():
                if child is not None:
                    walk(child)
        for entry in renpy.game.context().scene_lists.layers["rb_characters"]:
            walk(entry.displayable)
        return {{"project_path": renpy.config.basedir, "frame_id": rb_current_frame_id, "text": _rb_qa_visible_text(),
                 "layers": {{layer: sorted(renpy.get_showing_tags(layer=layer)) for layer in ("rb_background", "rb_characters", "rb_decorations")}}, "x_positions": transforms,
                 "media_paths": media, "decorative_text": _rb_qa_layer_text(),
                 "active_save_locations": list(renpy.loadsave.location.locations)}}

    def _rb_old_check(frame_id, text, xpos, image_path, decoration):
        state = _rb_old_state()
        print("OLD_SAVE_NATIVE_STATE=" + repr(state))
        if state["frame_id"] != frame_id or state["text"] != text:
            raise AssertionError("Old save did not resume the expected actual frame/text")
        if state["layers"]["rb_characters"] != [{q(tag("hero_sprite"))}]:
            raise AssertionError("Saved complete character layer differs")
        if not any(abs(value - xpos) < 0.000001 for value in state["x_positions"]):
            raise AssertionError("Actual transform does not have the expected restored/current x position")
        if image_path not in state["media_paths"]:
            raise AssertionError("Actual engine displayable does not use the expected immutable image version")
        if state["decorative_text"] != [decoration]:
            raise AssertionError("Actual decorative layer did not restore or update correctly")
        # Save location objects are converted to paths for evidence serialization.
        state["active_save_locations"] = [getattr(location, "directory", repr(location)) for location in state["active_save_locations"]]
        path = {q(str(record_path))}
        try:
            with open(path, "r", encoding="utf-8") as stream:
                records = rb_old_json.load(stream)
        except IOError:
            records = []
        # The test runner may evaluate assert-eval more than once while it
        # waits. Record the observed state once per project/interaction.
        if not records or records[-1] != state:
            records.append(state)
        with open(path, "w", encoding="utf-8") as stream:
            rb_old_json.dump(records, stream, ensure_ascii=False, indent=2)
        return True
'''


def fixture_frames():
    return [
        {"frame_id": "frame_entry", "act_id": "act_fixture", "order": 0,
         "dialogue": {"text": "从入口开始，推进后保存真实原生存档。"},
         "flow": {"kind": "next", "target_frame_id": "frame_saved"}},
        {"frame_id": "frame_saved", "act_id": "act_fixture", "order": 1,
         "characters": [{"component_id": "hero_sprite", "kind": "character", "character_id": "hero", "asset_id": "asset_saved_sprite",
                         "layout": {"x": 0.2, "y": 0.65, "height": 0.35}}],
         "decorations": [{"component_id": "title", "kind": "text", "text": "旧版：保存画面"}],
         "dialogue": {"speaker_id": "hero", "text": SAVED_TEXT},
         "flow": {"kind": "next", "target_frame_id": "frame_future"}},
        {"frame_id": "frame_future", "act_id": "act_fixture", "order": 2,
         "characters": [{"component_id": "hero_sprite", "kind": "character", "character_id": "hero", "asset_id": "asset_sprite",
                         "layout": {"x": 0.6, "y": 0.65, "height": 0.35}}],
         "decorations": [{"component_id": "title", "kind": "text", "text": "旧版：后继画面"}],
         "dialogue": {"text": "旧版后继台词。"}, "flow": {"kind": "end"}},
    ]


def main():
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"probe": "real-native-cross-revision-save", "run_directory": str(run_dir),
              "commands": {}, "complete": False, "save_compatibility_verified": False,
              "scope": "Ren'Py 8.5.3; stable project/frame/component identities and namespace; saved complete frame unchanged; subsequent dialogue/layout/image updated",
              "unsupported_without_new_probes": ["different SDK", "deleted/renamed saved statements", "arbitrary flow restructuring", "expecting a frozen historical scene after editing the saved frame itself"],
              "formal_demo": False, "audio_output_verified": False}

    def write_report():
        atomic_json(run_dir / "report.json", report)
        atomic_json(EVIDENCE / "report.json", report)

    try:
        sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
        report["sdk_version"] = sdk.version
        store = Store(run_dir / "bridge-home")
        project_id = store.create("跨版本原生存档夹具", "create-save-fixture")["project_id"]
        root = store.directory(project_id)
        icon = sdk.root / "launcher/game/gui7/icon.png"
        saved_icon = sdk.root / "launcher/game/images/window-icon.png"
        new_icon = sdk.root / "launcher/game/images/logo.png"
        store.register_assets(project_id, 0, "old-icon", [
            {"asset_id": "asset_saved_sprite", "kind": "image", "source_path": str(saved_icon)},
            {"asset_id": "asset_sprite", "kind": "image", "source_path": str(icon)},
        ])
        store.apply_batch(project_id, 1, "old-story", fixture_frames(), [{"act_id": "act_fixture", "name": "验收幕"}], [{"character_id": "hero", "name": "测试角色"}])
        old_model = store.load(project_id)
        old_asset = old_model.assets["asset_sprite"]
        saved_asset = old_model.assets["asset_saved_sprite"]
        old_project = root / "generated/old-version"
        old_generation = generate_project(old_model, root, old_project, sdk)
        report["old_generation"] = old_generation
        old_manifest = json.loads(Path(old_generation["manifest_path"]).read_text(encoding="utf-8"))
        old_image = old_manifest["assets"]["asset_saved_sprite"]["game_path"]
        retired_game_path = old_manifest["assets"]["asset_sprite"]["game_path"]
        record_path = run_dir / "native-states.json"
        old_tests = helper_source(record_path) + f'''
testsuite global:
    teardown:
        exit

testcase create_old_save:
    $ _test.timeout = 20.0
    $ _preferences.text_cps = 0
    run Start()
    pause 0.1 until eval (rb_current_frame_id == "frame_entry" and _rb_qa_visible_text() is not None)
    keysym "dismiss" until eval (rb_current_frame_id == "frame_saved" and _rb_qa_visible_text() == {q(SAVED_TEXT)})
    pause 0.3
    assert eval _rb_old_check("frame_saved", {q(SAVED_TEXT)}, 0.2, {q(old_image)}, "旧版：保存画面")
    screenshot "old_saved_frame"
    run ShowMenu("save")
    pause 0.3
    assert screen "save"
    run FileSave(1, page="1", confirm=False)
    pause 0.3
    assert eval FileLoadable(1, page="1")
    keysym "game_menu"
    pause 0.1 until not screen "save"
    pause 0.2
'''
        (old_project / "game/bridge_testcases.rpy").write_text(old_tests, encoding="utf-8")
        report["old_controlled_qa_sources"] = record_derived_sources(old_project, controlled_paths=["game/bridge_testcases.rpy"])
        saves = run_dir / "shared-private-saves"
        saves.mkdir()
        env = {"RENPY_MULTIPERSISTENT": str(run_dir / "private-multipersistent")}
        report["private_savedir"] = str(saves)
        report["save_namespace"] = old_model.save_namespace

        def execute(name, project, command, extra=()):
            result = run_sdk(sdk, project, command, [*extra, "--savedir", str(saves)], timeout_seconds=120, env=env)
            report["commands"][name] = asdict(result)
            (run_dir / f"{name}.stdout.txt").write_text(result.stdout, encoding="utf-8")
            (run_dir / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
            if (project / "log.txt").is_file():
                shutil.copyfile(project / "log.txt", run_dir / f"{name}.engine-log.txt")
            print(f"{name}: exit={result.exit_code}, timeout={result.timed_out}", flush=True)
            write_report()
            if result.exit_code != 0 or result.timed_out or (command == "test" and "[rpytest] Status: PASSED" not in result.stdout + result.stderr):
                raise RuntimeError(f"{name} failed: {result.stdout[-2500:]} {result.stderr[-1500:]}")
            return result

        execute("old-compile", old_project, "compile")
        execute("old-save", old_project, "test", ["--report-detailed"])
        actual_saves = sorted(saves.rglob("*.save"))
        if not actual_saves:
            raise RuntimeError("Native FileSave did not produce a real .save file in the private savedir")
        report["save_files"] = [{"path": str(path), "sha256": file_hash(path), "bytes": path.stat().st_size} for path in actual_saves]
        store.mark_runnable(project_id, old_model.revision, old_project)
        store.register_assets(project_id, 2, "new-icon", [{"asset_id": "asset_sprite", "kind": "image", "source_path": str(new_icon)}])
        frames = fixture_frames()
        frames[2]["characters"][0]["layout"]["x"] = 0.75
        frames[2]["dialogue"]["text"] = NEW_TEXT
        frames[2]["decorations"][0]["text"] = "新版：后继画面"
        store.apply_batch(project_id, 3, "new-story", frames)
        new_model = store.load(project_id)
        new_asset = new_model.assets["asset_sprite"]
        if new_asset.sha256 == old_asset.sha256:
            raise RuntimeError("The compatibility fixture must change the actual image bytes")
        new_project = root / "generated/new-version"
        report["new_generation"] = generate_project(new_model, root, new_project, sdk)
        new_manifest = json.loads(Path(report["new_generation"]["manifest_path"]).read_text(encoding="utf-8"))
        new_image = new_manifest["assets"]["asset_sprite"]["game_path"]
        report["compatibility_preparation"] = prepare_previous_release(old_project, new_project, project_id, sdk.version)
        retired_image = new_project / "game" / retired_game_path
        if not retired_image.is_file() or file_hash(retired_image) != old_asset.sha256:
            raise RuntimeError("Previous immutable image version was not retained in the new native project")
        report["retired_image_verified"] = {"path": str(retired_image), "sha256": file_hash(retired_image)}
        report["revisions"] = {"old": old_model.revision, "new": new_model.revision}
        report["old_asset"] = old_asset.model_dump(mode="json")
        report["new_asset"] = new_asset.model_dump(mode="json")
        new_tests = helper_source(record_path) + f'''
testsuite global:
    teardown:
        exit

testcase load_previous_version:
    $ _test.timeout = 20.0
    $ _preferences.text_cps = 0
    assert eval FileLoadable(1, page="1")
    run FileLoad(1, page="1", confirm=False)
    pause 0.1 until eval (rb_current_frame_id == "frame_saved" and _rb_qa_visible_text() == {q(SAVED_TEXT)})
    pause 0.3
    assert eval _rb_old_check("frame_saved", {q(SAVED_TEXT)}, 0.2, {q(old_image)}, "旧版：保存画面")
    screenshot "old_save_loaded_in_new_version"
    keysym "dismiss" until eval (rb_current_frame_id == "frame_future" and _rb_qa_visible_text() == {q(NEW_TEXT)})
    pause 0.3
    assert eval _rb_old_check("frame_future", {q(NEW_TEXT)}, 0.75, {q(new_image)}, "新版：后继画面")
    screenshot "new_future_after_old_save"
'''
        (new_project / "game/bridge_testcases.rpy").write_text(new_tests, encoding="utf-8")
        report["new_controlled_qa_sources"] = record_derived_sources(new_project, controlled_paths=["game/bridge_testcases.rpy"])
        execute("new-compile", new_project, "compile")
        execute("new-load", new_project, "test", ["--report-detailed"])
        states = json.loads(record_path.read_text(encoding="utf-8"))
        if len(states) != 3:
            raise RuntimeError("Three actual engine save/load/future state records are required")
        report["native_states"] = states
        report["screenshots"] = [{"path": str(path), "sha256": file_hash(path)} for project in [old_project, new_project] for path in sorted((project / "tests/screenshots").glob("*.png"))]
        if len(report["screenshots"]) != 3:
            raise RuntimeError("Old save, restored save and new future screenshots are required")
        report["complete"] = report["save_compatibility_verified"] = True
        report["code_sha256"] = {str(path.relative_to(ROOT)): file_hash(path) for path in [Path(__file__).resolve(), ROOT / "src/renpy_bridge/compatibility.py", ROOT / "src/renpy_bridge/generator.py", ROOT / "src/renpy_bridge/native_qa.py"]}
        write_report()
        print(f"Old-save compatibility verified. Report: {run_dir / 'report.json'}", flush=True)
        return 0
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        write_report()
        print(report["error"]["traceback"], flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
