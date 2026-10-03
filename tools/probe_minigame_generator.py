"""Run real SDK/HTML gameplay against a production-generated schema 1.1 fixture.

All authoring, saves, browser data, screenshots and reports use a new evidence
directory. Actual HTML input is performed separately; no result is fabricated.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import traceback
import zipfile

from renpy_bridge.derived_integrity import record_derived_sources, verify_derived_sources
from renpy_bridge.generator import generate_project, stable_label
from renpy_bridge.minigame_packages import import_package
from renpy_bridge.models import Project
from renpy_bridge.native_qa import NATIVE_QA_SOURCE
from renpy_bridge.sdk import discover_sdk, run_sdk


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/html-minigames/production-generator"
ENTRY_TEXT = "剧情入口：完成 HTML 小游戏后继续对应剧情。"
TEXT = {"win": "真实小游戏胜利，已返回生产生成器生成的剧情。",
        "lose": "真实小游戏失败，已返回失败剧情。",
        "cancel": "玩家已退出小游戏，已返回取消剧情。",
        "error": "小游戏运行异常，已返回恢复剧情。",
        "fallback": "小游戏结果进入声明的兜底剧情。"}

WINDOW_QA = r'''
init 20 python:
    import sys as _rb_prod_window_sys
    _rb_prod_window_previous_bytecode = _rb_prod_window_sys.dont_write_bytecode
    _rb_prod_window_sys.dont_write_bytecode = True
    try:
        import embedded_window as _rb_prod_window
        from renpy_bridge_minigames import minigame_windows as _rb_prod_geometry
    finally:
        _rb_prod_window_sys.dont_write_bytecode = _rb_prod_window_previous_bytecode

    def _rb_prod_observe_window(stage):
        manager = _rb_mg.manager()
        parent = _rb_prod_geometry.read_parent(renpy.get_sdl_dll(), renpy.get_sdl_window_pointer(), renpy.display.draw)
        value = {"stage": stage, **parent, **_rb_prod_window._owned_windows(parent, manager)}
        value["fullscreen"] = bool(_preferences.fullscreen)
        path = _rb_prod_window.Path(config.savedir) / "production-window.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(_rb_prod_window.json.dumps(value, ensure_ascii=False) + "\n")
        return True

    def _rb_prod_assert_geometry(stage, expect_visible):
        _rb_prod_observe_window(stage)
        path = _rb_prod_window.Path(config.savedir) / "production-window.jsonl"
        value = _rb_prod_window.json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        renderer = [child for child in value["child_windows"] if child["class_name"] == "Chrome_RenderWidgetHostHWND"]
        if value["visible_owned_top_level_count"] != 1 or not renderer:
            return False
        if expect_visible:
            return all(child["visible"] and child["root_hwnd"] == value["hwnd"]
                and child["parent_rectangle"] == value["geometry"]["viewport"] for child in renderer)
        return all(not child["visible"] for child in renderer)
'''


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


CHECKPOINT_QA = r'''
init 20 python:
    import json as _rb_prod_json
    import pathlib as _rb_prod_pathlib
    import hashlib as _rb_prod_hashlib
    import os as _rb_prod_os

    def _rb_prod_slot():
        configuration = _rb_prod_json.loads((_rb_prod_pathlib.Path(config.basedir) / "game/bridge-minigames.json").read_text(encoding="utf-8"))
        return "rb-minigame-" + _rb_prod_hashlib.sha256(configuration["save_namespace"].encode("utf-8")).hexdigest()[:16]

    def _rb_prod_reference():
        return _rb_prod_pathlib.Path(config.savedir) / "checkpoint-reference.json"

    def _rb_prod_remember():
        metadata = renpy.slot_json(_rb_prod_slot())
        if not metadata or metadata.get("rb_minigame_receipt") != rb_minigame_state.get("last_commit"):
            return False
        snapshot = {"state": _rb_mg._copy(rb_minigame_state), "pid": _rb_prod_os.getpid()}
        _rb_prod_reference().write_text(_rb_prod_json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
        return True

    def _rb_prod_verify_restore():
        # Reference metadata is assertion evidence only. FileLoad restores the
        # actual .save; this function never changes any story/runtime state.
        reference = _rb_prod_json.loads(_rb_prod_reference().read_text(encoding="utf-8"))
        old = reference["state"]
        return (reference["pid"] != _rb_prod_os.getpid()
            and rb_minigame_state["logical_entry_id"] == old["logical_entry_id"]
            and rb_minigame_state["snapshot"] == old["snapshot"]
            and rb_minigame_state["attempt_id"] != old["attempt_id"]
            and rb_minigame_state["session_id"] != old["session_id"]
            and rb_minigame_state["load_epoch"] > old["load_epoch"])

    def _rb_prod_checkpoint_persisted():
        if rb_minigame_state is None or rb_minigame_state.get("snapshot") is None:
            return False
        receipt = rb_minigame_state.get("last_commit")
        metadata = renpy.slot_json(_rb_prod_slot())
        return (receipt is not None and receipt["kind"] == "checkpoint"
            and rb_minigame_state["snapshot"]["state"]["stage"] == 1
            and metadata is not None and metadata.get("rb_minigame_receipt") == receipt)
'''


MANUAL_SAVE_QA = r'''
init 20 python:
    import json as _rb_manual_json
    import pathlib as _rb_manual_pathlib
    import os as _rb_manual_os

    def _rb_manual_reference():
        return _rb_manual_pathlib.Path(config.savedir) / "manual-save-reference.json"

    def _rb_manual_remember():
        # This reference is assertion evidence only. Only FileLoad reads and
        # restores the actual native manual save in the next SDK process.
        metadata = renpy.slot_json("1-1")
        if not metadata or not FileLoadable(1, page="1"):
            return False
        reference = {"state": _rb_mg._copy(rb_minigame_state), "pid": _rb_manual_os.getpid(),
            "reward": rb_story_variables["reward"], "metadata": metadata}
        _rb_manual_reference().write_text(_rb_manual_json.dumps(reference, ensure_ascii=False), encoding="utf-8")
        return True

    def _rb_manual_verify_restore():
        reference = _rb_manual_json.loads(_rb_manual_reference().read_text(encoding="utf-8"))
        old = reference["state"]
        return (reference["pid"] != _rb_manual_os.getpid()
            and rb_minigame_state["logical_entry_id"] == old["logical_entry_id"]
            and rb_minigame_state["package_digest"] == old["package_digest"]
            and rb_minigame_state["last_commit"] == old["last_commit"]
            and rb_minigame_state["attempt_id"] != old["attempt_id"]
            and rb_minigame_state["session_id"] != old["session_id"]
            and rb_minigame_state["load_epoch"] > old["load_epoch"]
            and rb_minigame_state["save_mode"] == "entry_restart"
            and rb_minigame_state["snapshot"] is None
            and rb_story_variables["reward"] == reference["reward"] == 0)

    def _rb_manual_completion_once():
        return (rb_story_variables["reward"] == 1
            and rb_minigame_state["phase"] == "completed"
            and rb_minigame_state["completion"]["receipt_id"] == rb_minigame_state["last_commit"]["receipt_id"]
            and rb_minigame_state["last_commit"]["kind"] == "finish")
'''


def tests(scenario):
    if scenario == "manual_load":
        return NATIVE_QA_SOURCE + WINDOW_QA + CHECKPOINT_QA + MANUAL_SAVE_QA + f'''
testsuite global:
    teardown:
        pause 3.0
        exit

testcase manual_create:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
    run Start({stable_label("frame_game")!r})
    pause 0.1 until screen "rb_minigame_waiting"
    pause 0.1 until eval _rb_mg.manager().can_pause(rb_minigame_state)
    assert eval _rb_prod_assert_geometry("manual_actual_page_ready", True)
    screenshot "production_manual_waiting"
    click "暂停与存档"
    pause 0.1 until screen "rb_minigame_menu"
    pause 0.5
    assert eval _rb_prod_assert_geometry("manual_save_paused", False)
    click "存档"
    pause 0.1 until screen "save"
    run FilePage("1")
    pause 0.5
    screenshot "production_manual_save_menu"
    click "空存档位"
    pause 0.1 until eval FileLoadable(1, page="1")
    assert eval _rb_manual_remember()
    assert eval (rb_story_variables["reward"] == 0)
    screenshot "production_manual_slot_saved"
    click "返回"
    pause 0.1 until screen "rb_minigame_menu"
    assert eval _rb_prod_assert_geometry("manual_saved_paused", False)
    screenshot "production_manual_before_exit"

testcase manual_restore:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
    run FileLoad(1, page="1", confirm=False)
    pause 0.1 until screen "rb_minigame_menu"
    assert eval _rb_manual_verify_restore()
    assert eval _rb_prod_observe_window("manual_loaded_native_menu")
    screenshot "production_manual_loaded_menu"
    click "继续小游戏"
    pause 0.1 until screen "rb_minigame_waiting"
    pause 0.1 until eval _rb_mg.manager().can_pause(rb_minigame_state)
    assert eval _rb_prod_assert_geometry("manual_actual_resumed", True)
    assert eval (rb_story_variables["reward"] == 0)
    screenshot "production_manual_restarted_html"
    pause 0.1 until eval (rb_current_frame_id == "frame_win" and _rb_qa_visible_text() == {TEXT["win"]!r})
    pause 0.8
    assert eval _rb_manual_completion_once()
    assert eval _rb_prod_observe_window("manual_correct_returned_story")
    screenshot "production_manual_win"

testcase manual_completed_restore:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
    run FileLoad(_rb_prod_slot(), slot=True, confirm=False)
    pause 0.1 until eval (rb_current_frame_id == "frame_win" and _rb_qa_visible_text() == {TEXT["win"]!r})
    pause 0.8
    assert eval _rb_manual_completion_once()
    assert eval _rb_prod_observe_window("manual_completed_correct_returned_story")
    screenshot "production_manual_completed_restored_once"
'''
    if scenario == "checkpoint_load":
        return NATIVE_QA_SOURCE + WINDOW_QA + CHECKPOINT_QA + f'''
testsuite global:
    teardown:
        pause 3.0
        exit

testcase checkpoint_create:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
    run Start()
    pause 0.1 until eval (rb_current_frame_id == "frame_game" and _rb_qa_visible_text() is not None)
    pause 0.5
    screenshot "production_checkpoint_entry"
    click {ENTRY_TEXT!r}
    pause 0.1 until eval (rb_minigame_state is not None)
    pause 0.1 until screen "rb_minigame_waiting"
    pause 0.1 until eval _rb_mg.manager().can_pause(rb_minigame_state)
    assert eval _rb_prod_observe_window("checkpoint_actual_page_ready")
    pause 0.1 until eval _rb_prod_checkpoint_persisted()
    click "暂停与存档"
    pause 0.1 until screen "rb_minigame_menu"
    assert eval _rb_prod_checkpoint_persisted()
    assert eval _rb_prod_remember()
    assert eval (rb_story_variables["reward"] == 0)
    assert eval _rb_prod_observe_window("checkpoint_saved_paused")
    screenshot "production_checkpoint_saved"

testcase checkpoint_restore:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
    run FileLoad(_rb_prod_slot(), slot=True, confirm=False)
    pause 0.1 until screen "rb_minigame_waiting"
    pause 0.1 until eval _rb_mg.manager().can_pause(rb_minigame_state)
    assert eval _rb_prod_verify_restore()
    assert eval (rb_story_variables["reward"] == 0)
    assert eval _rb_prod_observe_window("checkpoint_actual_resumed")
    pause 0.3
    screenshot "production_checkpoint_restored"
    pause 0.1 until eval (rb_current_frame_id == "frame_win" and _rb_qa_visible_text() == {TEXT["win"]!r})
    pause 0.8
    assert eval (rb_story_variables["reward"] == 1)
    assert eval _rb_prod_observe_window("checkpoint_correct_returned_story")
    screenshot "production_checkpoint_win"

testcase completed_restore:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
    run FileLoad(_rb_prod_slot(), slot=True, confirm=False)
    pause 0.1 until eval (rb_current_frame_id == "frame_win" and _rb_qa_visible_text() == {TEXT["win"]!r})
    pause 0.8
    assert eval (rb_story_variables["reward"] == 1)
    assert eval _rb_prod_observe_window("completed_correct_returned_story")
    screenshot "production_completed_restored_once"
'''
    geometry_steps = ""
    if scenario == "geometry":
        for stage, action in [("portrait_1024_900", "$ renpy.set_physical_size((1024, 900))"),
                ("classic_800_600", "$ renpy.set_physical_size((800, 600))"),
                ("wide_1920_1080", "$ renpy.set_physical_size((1920, 1080))"),
                ("fullscreen", "run Preference('display', 'fullscreen')"),
                ("return_window", "run Preference('display', 'any window')"),
                ("restored_1280_720", "$ renpy.set_physical_size((1280, 720))")]:
            geometry_steps += f'''    {action}
    pause 1.0
    assert eval _rb_prod_assert_geometry("geometry_{stage}_visible", True)
    screenshot "production_geometry_{stage}"
    click "暂停与存档"
    pause 0.1 until screen "rb_minigame_menu"
    pause 0.3
    assert eval _rb_prod_assert_geometry("geometry_{stage}_paused", False)
    screenshot "production_geometry_{stage}_menu"
    click "继续小游戏"
    pause 0.1 until screen "rb_minigame_waiting"
    pause 0.5
    assert eval _rb_prod_assert_geometry("geometry_{stage}_resumed", True)
'''
        geometry_steps += '    click "返回剧情"\n'
    outcome = "cancel" if scenario in {"pause_cancel", "geometry"} else scenario
    target = "frame_" + outcome
    entry_sequence = f'''    run Start({stable_label("frame_game")!r})
    pause 0.1 until eval (rb_minigame_state is not None)
''' if scenario == "geometry" else f'''    run Start()
    pause 0.1 until eval (rb_current_frame_id == "frame_game" and _rb_qa_visible_text() is not None)
    pause 0.5
    screenshot "production_minigame_entry"
    click {ENTRY_TEXT!r}
    pause 0.1 until eval (rb_minigame_state is not None)
'''
    return NATIVE_QA_SOURCE + WINDOW_QA + f'''
testsuite global:
    teardown:
        pause 3.0
        exit

testcase production_minigame:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
{entry_sequence.rstrip()}
    pause 0.1 until screen "rb_minigame_waiting"
    pause 0.1 until eval _rb_mg.manager().can_pause(rb_minigame_state)
    assert eval _rb_prod_observe_window("actual_page_ready")
    pause 0.3
    screenshot "production_minigame_waiting"
'''+ (geometry_steps if scenario == "geometry" else '''    click "返回剧情"
''' if scenario == "cancel" else '''    pause 0.1 until eval _rb_mg.manager().can_pause(rb_minigame_state)
    click "暂停与存档"
    pause 0.1 until screen "rb_minigame_menu"
    pause 0.5
    assert eval _rb_prod_observe_window("paused_native_menu")
    screenshot "production_minigame_paused"
    click "存档"
    pause 0.1 until screen "save"
    run FilePage("1")
    pause 0.5
    screenshot "production_minigame_manual_save_menu"
    click "空存档位"
    pause 0.1 until eval FileLoadable(1, page="1")
    click "返回"
    pause 0.1 until screen "rb_minigame_menu"
    click "继续小游戏"
    pause 0.1 until screen "rb_minigame_waiting"
    pause 1.0
    assert eval _rb_prod_observe_window("resumed_embedded_html")
    screenshot "production_minigame_resumed"
    click "返回剧情"
''' if scenario == "pause_cancel" else '''    pause 0.1 until eval _rb_mg.development_host_running(rb_minigame_state)
    $ _rb_mg.development_terminate_own_host(rb_minigame_state)
''' if scenario == "error" else "") + f'''
    pause 0.1 until eval (rb_current_frame_id == {target!r} and _rb_qa_visible_text() == {TEXT[outcome]!r})
    pause 0.8
    assert eval (rb_story_variables["reward"] == {1 if scenario == "win" else 0})
    assert eval _rb_prod_observe_window("correct_returned_story")
    screenshot "production_minigame_{scenario}"
'''


def prepare(args):
    run = EVIDENCE / "runs" / str(time.time_ns())
    run.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "probe": "production-generated-native-minigame", "run_directory": str(run),
              "scenario": args.scenario, "complete": False, "commands": {}, "formal_demo_modified": False}
    report["probe_tool_sha256"] = sha(Path(__file__))
    dump(run / "report.json", report)
    (run / "probe-source.py").write_bytes(Path(__file__).read_bytes())
    try:
        authoring = run / "authoring"
        info = import_package(args.package_root.resolve(strict=True), authoring / "minigames", allow_prototype=True)
        definition = {"game_id": info.manifest["game_id"], "package_digest": info.digest,
            "package_version": info.manifest["version"], "path": "minigames/" + info.digest, "manifest": info.manifest}
        outcomes = info.manifest["results"]["outcomes"]
        branches = [{"branch_id": "outcome_" + str(index), "outcome": value,
            "target_frame_id": "frame_win" if value == "win" else "frame_lose",
            "effects": [{"effect_id": "win_reward", "op": "add", "variable_id": "reward", "value": {"kind": "literal", "value": 1}}] if value == "win" else []}
            for index, value in enumerate(outcomes)]
        flow = {"kind": "minigame", "game_id": definition["game_id"], "instance_id": "instance_game",
            "inputs": {key: {"kind": "literal", "value": value} for key, value in json.loads(args.inputs_json).items()},
            "save_mode": "checkpoint" if args.scenario == "checkpoint_load" else args.save_mode, "result_branches": branches,
            "cancel_target_frame_id": "frame_cancel", "error_target_frame_id": "frame_error", "fallback_target_frame_id": "frame_fallback"}
        project = Project(schema_version="1.1", project_id="production_html_" + run.name, name="生产生成器 HTML 游戏验收",
            save_namespace="production_html_" + run.name, entry_frame_id="frame_game",
            minigames={definition["game_id"]: definition},
            variables={"reward": {"variable_id": "reward", "type": "int", "default": 0, "minimum": 0, "maximum": 10}},
            acts={"act": {"act_id": "act", "frame_ids": ["frame_game", *["frame_" + key for key in TEXT]]}},
            frames={"frame_game": {"frame_id": "frame_game", "act_id": "act", "dialogue": None if args.scenario in {"geometry", "manual_load"} else {"text": ENTRY_TEXT}, "flow": flow},
                **{"frame_" + key: {"frame_id": "frame_" + key, "act_id": "act", "dialogue": {"text": value}, "flow": {"kind": "end"}} for key, value in TEXT.items()}})
        dump(authoring / "fixture-project.json", project.model_dump(mode="json"))
        sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
        target = run / "generated"
        generation = generate_project(project, authoring, target, sdk,
            minigame_host_root=args.host_root, minigame_fixed_runtime_root=args.fixed_runtime_root,
            allow_prototype_minigames=True, minigame_development=True, defer_minigame_integrity=True)
        installed_configuration = json.loads((target / "game/bridge-minigames.json").read_text(encoding="utf-8"))
        full_host_inventory = installed_configuration["host"]["files"]
        host_inventory_json = json.dumps(full_host_inventory, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        qa = target / "game/bridge_minigame_generator_qa.rpy"
        qa.write_text(tests(args.scenario), encoding="utf-8")
        window_qa = target / "game/python-packages/embedded_window.py"
        window_qa.write_bytes((ROOT / "prototypes/html_embedded/embedded_window.py").read_bytes())
        generation["derived_sources"] = record_derived_sources(target, managed_roots=[
            *generation["derived_sources"]["managed_roots"], "game/python-packages/embedded_window.py"])
        qa_options = run / "qa-options.json"
        dump(qa_options, {"development": True, "devtools_port": args.devtools_port,
            "event_log_path": str(run / "private-saves/runtime-events.jsonl")})
        report.update(fixture=str(target), authoring_model=str(authoring / "fixture-project.json"), generation=generation,
            package_hash=info.digest, sdk_version=sdk.version, prepared=True,
            env={"RENPY_BRIDGE_MINIGAME_QA_JSON": str(qa_options), **json.loads(args.env_json)},
            prototype_package=info.prototype, source_map=target / "bridge-source-map.json",
            host_inventory_evidence={"algorithm": "sha256-canonical-json-utf8-sort-keys-compact",
                "sha256": hashlib.sha256(host_inventory_json.encode("utf-8")).hexdigest(),
                "file_count": len(full_host_inventory), "files": full_host_inventory})
        report["source_map"] = str(report["source_map"])
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    dump(run / "report.json", report)
    print(json.dumps({"run_directory": str(run), "report_path": str(run / "report.json"), "prepared": report.get("prepared", False)}, ensure_ascii=False), flush=True)
    return 0 if report.get("prepared") else 1


def execute(run, checks_only=False, phase="standard"):
    path = run / "report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["complete"] = False
    try:
        if not report.get("prepared"):
            raise RuntimeError("preparation did not complete")
        target = Path(report["fixture"])
        sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
        saves, multi = run / "private-saves", run / "private-multipersistent"
        saves.mkdir(exist_ok=True)
        multi.mkdir(exist_ok=True)
        runtime_log = saves / "runtime-events.jsonl"
        runtime_log_offset = runtime_log.stat().st_size if runtime_log.exists() else 0
        env = {"RENPY_MULTIPERSISTENT": str(multi), **report.get("env", {})}
        if report.get("probe_tool_sha256") is not None and report["probe_tool_sha256"] != sha(Path(__file__)):
            raise RuntimeError("the prepared probe source changed before execution")
        if report["scenario"] == "manual_load":
            if phase not in {"manual_create", "manual_restore", "manual_completed_restore"}:
                raise RuntimeError("manual_load requires explicit manual_create/restore or manual_completed_restore phases")
            test_name = phase
        elif report["scenario"] == "checkpoint_load":
            if phase not in {"checkpoint_create", "checkpoint_restore", "completed_restore"}:
                raise RuntimeError("checkpoint_load requires an explicit checkpoint_create/restore or completed_restore phase")
            test_name = phase
        else:
            if phase != "standard":
                raise RuntimeError("extra phases are only available for checkpoint_load/manual_load")
            test_name = "production_minigame"
        for name, command, args, bound in [("compile", "compile", [], 60), ("lint", "lint", ["--error-code"], 60),
            ("real-minigame", "test", [test_name, "--report-detailed"], 210)]:
            if command == "test" and checks_only:
                report["checks_complete"] = True
                dump(path, report)
                print(json.dumps({"report_path": str(path), "checks_complete": True}, ensure_ascii=False), flush=True)
                return 0
            result = run_sdk(sdk, target, command, [*args, "--savedir", str(saves)], timeout_seconds=bound, env=env)
            command_key = name if phase == "standard" else phase + "-" + name
            report["commands"][command_key] = asdict(result)
            (run / (command_key + ".stdout.log")).write_text(result.stdout, encoding="utf-8")
            (run / (command_key + ".stderr.log")).write_text(result.stderr, encoding="utf-8")
            dump(path, report)
            if result.exit_code != 0 or result.timed_out or not result.cleanup_complete:
                raise RuntimeError(name + " failed: " + (result.stdout + result.stderr)[-4000:])
            if command == "test" and "[rpytest] Status: PASSED" not in result.stdout + result.stderr:
                raise RuntimeError("real native test did not explicitly report PASSED")
            if command == "test":
                statuses = re.findall(r'^\[rpytest\]\s+(PASSED|PENDING|SKIPPED|XFAILED|FAILED|XPASSED)\s+(?:global::)?'
                    + re.escape(test_name) + r'(?=\s|$)', result.stdout + result.stderr, flags=re.MULTILINE)
                if not statuses or any(value != "PASSED" for value in statuses):
                    raise RuntimeError("the requested native testcase did not finish PASSED: " + repr(statuses))
                report.setdefault("requested_testcase_evidence", {})[phase] = {"name": test_name, "statuses": statuses}
        report["derived_integrity_after_execution"] = verify_derived_sources(target, run / "integrity-evidence")
        report["screenshots"] = [{"path": str(p), "sha256": sha(p)} for p in sorted((target / "tests/screenshots").glob("*.png"))]
        report["save_files"] = [{"path": str(p), "sha256": sha(p)} for p in sorted(saves.rglob("*.save"))]
        window_path = saves / "production-window.jsonl"
        if phase == "standard":
            windows = [json.loads(line) for line in window_path.read_text(encoding="utf-8").splitlines()]
            ready = [item for item in windows if item["stage"] == "actual_page_ready"]
            returned = [item for item in windows if item["stage"] == "correct_returned_story"]
            if not ready or not returned or not all(item["visible_owned_top_level_count"] == 1 for item in windows):
                raise RuntimeError("same-window native ownership evidence is incomplete")
            if {(item["hwnd"], item["process_id"]) for item in ready} != {(item["hwnd"], item["process_id"]) for item in returned}:
                raise RuntimeError("the returned story did not use the same SDK root window")
            if not all(any(child["visible"] and child["class_name"] == "Chrome_RenderWidgetHostHWND" and child["root_hwnd"] == item["hwnd"]
                for child in item["child_windows"]) for item in ready):
                raise RuntimeError("the real WebView renderer was not observed under the SDK root")
            if any(child["visible"] and child["class_name"] == "Chrome_RenderWidgetHostHWND" for item in returned for child in item["child_windows"]):
                raise RuntimeError("the real WebView renderer remained visible over the returned story")
            report["same_window_evidence"] = {"passed": True, "observations": windows,
                "evidence_path": str(window_path), "sha256": sha(window_path)}
        if phase != "standard":
            all_windows = [json.loads(line) for line in window_path.read_text(encoding="utf-8").splitlines()]
            stages = {"manual_create": {"manual_actual_page_ready", "manual_save_paused", "manual_saved_paused"},
                "manual_restore": {"manual_loaded_native_menu", "manual_actual_resumed", "manual_correct_returned_story"},
                "manual_completed_restore": {"manual_completed_correct_returned_story"},
                "checkpoint_create": {"checkpoint_actual_page_ready", "checkpoint_saved_paused"},
                "checkpoint_restore": {"checkpoint_actual_resumed", "checkpoint_correct_returned_story"},
                "completed_restore": {"completed_correct_returned_story"}}[phase]
            phase_windows = [item for item in all_windows if item["stage"] in stages]
            if {item["stage"] for item in phase_windows} != stages or not all(item["visible_owned_top_level_count"] == 1 for item in phase_windows):
                raise RuntimeError("checkpoint phase lacks its actual same-window native observations")
            if len({(item["hwnd"], item["process_id"]) for item in phase_windows}) != 1:
                raise RuntimeError("checkpoint phase changed SDK root window identity")
            visible_renderer = lambda item: any(child["visible"] and child["class_name"] == "Chrome_RenderWidgetHostHWND"
                and child["root_hwnd"] == item["hwnd"] for child in item["child_windows"])
            for item in phase_windows:
                expect_renderer = item["stage"] in {"checkpoint_actual_page_ready", "checkpoint_actual_resumed",
                    "manual_actual_page_ready", "manual_actual_resumed"}
                if visible_renderer(item) != expect_renderer:
                    raise RuntimeError("checkpoint native renderer visibility contradicts its phase: " + item["stage"])
            with runtime_log.open("rb") as stream:
                stream.seek(runtime_log_offset)
                phase_events = [json.loads(line) for line in stream.read().decode("utf-8").splitlines()]
            if phase in {"completed_restore", "manual_completed_restore"} and any(item["type"] == "launch" for item in phase_events):
                raise RuntimeError("loading a completed native save launched another minigame host")
            native_saves = sorted(saves.glob("1-1-*.save")) if phase == "manual_create" else sorted(saves.glob("rb-minigame-*.save"))
            if len(native_saves) != 1:
                raise RuntimeError("expected exactly one actual phase-specific native .save")
            with zipfile.ZipFile(native_saves[0]) as archive:
                metadata = json.loads(archive.read("json"))
            receipt = metadata.get("rb_minigame_receipt")
            expected_kind = "checkpoint" if phase == "checkpoint_create" else "finish"
            if phase == "manual_create":
                if "_renpy_version" not in metadata or "_ctime" not in metadata:
                    raise RuntimeError("the actual native manual slot lacks standard SDK metadata")
                reference = json.loads((saves / "manual-save-reference.json").read_text(encoding="utf-8"))
                if reference["metadata"] != metadata:
                    raise RuntimeError("native manual slot metadata does not match the saved reference")
            elif not isinstance(receipt, dict) or receipt.get("kind") != expected_kind:
                raise RuntimeError("actual disk save has no matching checkpoint/finish receipt")
            report.setdefault("phase_evidence", {})[phase] = {"passed": True, "disk_save": str(native_saves[0]),
                "disk_save_sha256": sha(native_saves[0]), "receipt": receipt,
                "disk_metadata": metadata,
                "native_process_id": report["commands"][phase + "-real-minigame"]["process_id"],
                "same_window_observations": phase_windows,
                "runtime_event_log_offset": runtime_log_offset,
                "new_host_launches": sum(item["type"] == "launch" for item in phase_events)}
            create_phase = "manual_create" if report["scenario"] == "manual_load" else "checkpoint_create"
            report["complete"] = phase != create_phase and report["phase_evidence"].get(create_phase, {}).get("passed", False)
            report["coverage"] = ["production-generator", "actual-native-sdk-compile-lint", "fixed-runtime-full-copy",
                "derived-integrity-after-execution", "actual-disk-manual-save-created" if report["scenario"] == "manual_load" else "actual-disk-checkpoint-created", "same-native-top-level-window"]
            if report["complete"]:
                report["coverage"].extend(["new-sdk-process-fileload", "actual-html-entry-restart-win" if report["scenario"] == "manual_load" else "actual-html-snapshot-resume-win", "typed-variable-win-effect"])
            if report["scenario"] == "manual_load":
                report["manual_save_evidence"] = {"reference_path": str(saves / "manual-save-reference.json"),
                    "reference_sha256": sha(saves / "manual-save-reference.json"), "reference_used_to_restore_state": False,
                    "load_action": "FileLoad(1, page='1', confirm=False)", "save_mode": "entry_restart"}
            if phase in {"completed_restore", "manual_completed_restore"}:
                report["coverage"].append("completed-save-load-no-duplicate-reward")
        else:
            report["coverage"] = ["production-generator", "actual-native-sdk-compile-lint", "actual-html-" + report["scenario"] + "-to-native-dialogue",
                "typed-variable-win-effect" if report["scenario"] == "win" else "typed-variable-no-reward",
                "fixed-runtime-full-copy", "derived-integrity-after-execution", "same-native-top-level-window"]
            if report["scenario"] == "pause_cancel":
                if not any(Path(item["path"]).name.startswith("1-1-") for item in report["save_files"]):
                    raise RuntimeError("the actual native menu did not produce the expected manual slot save")
                paused = [item for item in windows if item["stage"] == "paused_native_menu"]
                resumed = [item for item in windows if item["stage"] == "resumed_embedded_html"]
                if not paused or not resumed or any(child["visible"] and child["class_name"] == "Chrome_RenderWidgetHostHWND"
                    for item in paused for child in item["child_windows"]):
                    raise RuntimeError("pause did not hide the embedded WebView over the native menu")
                if not all(any(child["visible"] and child["class_name"] == "Chrome_RenderWidgetHostHWND" for child in item["child_windows"]) for item in resumed):
                    raise RuntimeError("continue did not redisplay the same-window HTML renderer")
                report["coverage"].extend(["native-footer-pause", "actual-native-save-menu-click", "native-footer-resume"])
            elif report["scenario"] == "error":
                report["coverage"].append("development-terminate-exact-owned-host")
            elif report["scenario"] == "geometry":
                geometry_windows = [item for item in windows if item["stage"].startswith("geometry_")]
                if len({item["stage"] for item in geometry_windows}) != 18:
                    raise RuntimeError("native geometry did not verify all six actual resize/display modes")
                if not any(item["fullscreen"] for item in geometry_windows) or not any(not item["fullscreen"] for item in geometry_windows):
                    raise RuntimeError("native fullscreen/window transition was not observed")
                report["coverage"].extend(["actual-renpy-physical-resize", "non-16-9-letterboxed-child-viewport",
                    "actual-fullscreen-and-window-return", "actual-native-footer-reachable-in-six-modes"])
                report["geometry_evidence"] = geometry_windows
            report["complete"] = True
    except Exception as error:
        report["complete"] = False
        report["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    dump(path, report)
    print(json.dumps({"report_path": str(path), "complete": report["complete"]}, ensure_ascii=False), flush=True)
    return 0 if report["complete"] or report.get("phase_evidence", {}).get(phase, {}).get("passed") else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", type=Path)
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--host-root", type=Path)
    parser.add_argument("--fixed-runtime-root", type=Path)
    parser.add_argument("--env-json", default="{}")
    parser.add_argument("--inputs-json", default="{}")
    parser.add_argument("--devtools-port", type=int, default=9340)
    parser.add_argument("--save-mode", choices=["entry_restart", "checkpoint"], default="entry_restart")
    parser.add_argument("--scenario", choices=["win", "lose", "cancel", "pause_cancel", "geometry", "error", "checkpoint_load", "manual_load"], default="win")
    parser.add_argument("--phase", choices=["standard", "checkpoint_create", "checkpoint_restore", "completed_restore", "manual_create", "manual_restore", "manual_completed_restore"], default="standard")
    args = parser.parse_args()
    if args.execute:
        return execute(args.execute.resolve(strict=True), args.checks_only, args.phase)
    if not args.prepare or args.package_root is None:
        parser.error("use --prepare --package-root PATH or --execute RUN_DIRECTORY")
    return prepare(args)


if __name__ == "__main__":
    sys.exit(main())
