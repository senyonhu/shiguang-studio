"""Prepare and run an isolated real-SDK HTML/fiction integration H0 probe.

--prepare writes only a fresh evidence fixture. --execute runs compile, lint and
native testcases. A separate operator must solve the actual HTML game through
its UI (CDP or manual input); this tool never synthesizes a winning result.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import traceback

from renpy_bridge.generator import generate_project
from renpy_bridge.models import Act, Dialogue, Flow, Frame, Project
from renpy_bridge.native_qa import NATIVE_QA_SOURCE
from renpy_bridge.sdk import discover_sdk, run_sdk


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/html-minigames/h0-renpy"
PROTOTYPE = ROOT / "prototypes/html_runtime"
OUTCOME_TEXT = {
    "win": "小游戏真实胜利，奖励已发放一次，返回正确剧情。",
    "lose": "小游戏失败，返回失败剧情，没有发放胜利奖励。",
    "cancel": "玩家主动退出，返回取消剧情，没有伪装成失败。",
    "error": "小游戏运行异常，返回恢复剧情，没有伪装成胜利。",
}


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qa_source(scenario, phase="standard"):
    expected = "win" if scenario == "checkpoint_load" else scenario
    head = NATIVE_QA_SOURCE + '''testsuite global:
    teardown:
        $ hp_rt.invalidate("native_test_teardown")
        pause 3.0
        exit

testcase html_runtime_probe:
    $ _test.timeout = 180.0
    $ _preferences.text_cps = 0
'''
    if phase == "checkpoint_restore":
        head += '''    assert eval hp_rt.load_reference_for_assertions()
    assert eval renpy.can_load("hp-checkpoint")
    run FileLoad("hp-checkpoint", slot=True, confirm=False)
    pause 0.1 until eval hp_rt.restored_checkpoint_matches(hp_state)
    assert eval hp_observe_main_thread("checkpoint_loaded_new_process", hp_state, hp_ticks)
    assert eval hp_rt.inject_stale_for_test()
    pause 0.5
    assert eval (hp_state["reward_count"] == 0 and hp_state["target"] is None)
    screenshot "html_probe_checkpoint_after_new_process_load"
'''
    else:
        head += '''
    run Start()
    pause 0.1 until eval (rb_current_frame_id == "html_probe_entry" and _rb_qa_visible_text() is not None)
    pause 0.5
    assert eval hp_observe_main_thread("entry_rendered", hp_state, hp_ticks)
    screenshot "html_probe_entry"
    keysym "dismiss" until eval (rb_current_frame_id == "html_probe_waiting")
    assert eval hp_observe_main_thread("entry_dismissed_to_wait", hp_state, hp_ticks)
    pause 0.1 until screen "hp_waiting"
    pause 0.1 until eval (hp_ticks >= 10)
    assert eval hp_observe_main_thread("responsive_wait", hp_state, hp_ticks)
    screenshot "html_probe_responsive_wait"
'''
    parts = [head]
    if phase == "checkpoint_create":
        parts.append('''    pause 0.1 until eval (hp_state["checkpoint_count"] >= 1 and renpy.can_load("hp-checkpoint"))
    $ hp_rt.remember(hp_state)
    assert eval hp_observe_main_thread("checkpoint_created", hp_state, hp_ticks)
    screenshot "html_probe_checkpoint_before_process_exit"
''')
        return "\n".join(parts)
    if scenario == "responsive":
        parts.append('''    pause 2.0
    assert eval (rb_current_frame_id == "html_probe_waiting" and hp_ticks >= 25 and hp_state["reward_count"] == 0)
    assert eval hp_observe_main_thread("responsive_verified", hp_state, hp_ticks)
''')
    else:
        if scenario == "checkpoint_load" and phase == "standard":
            parts.append('''    pause 0.1 until eval (hp_state["checkpoint_count"] >= 1 and renpy.can_load("hp-checkpoint"))
    $ hp_rt.remember(hp_state)
    screenshot "html_probe_checkpoint_before_load"
    run FileLoad("hp-checkpoint", slot=True, confirm=False)
    pause 0.1 until eval hp_rt.restored_checkpoint_matches(hp_state)
    assert eval hp_observe_main_thread("checkpoint_loaded_new_session", hp_state, hp_ticks)
    assert eval hp_rt.inject_stale_for_test()
    pause 0.5
    assert eval (hp_state["reward_count"] == 0 and hp_state["target"] is None)
    screenshot "html_probe_checkpoint_after_load"
''')
        parts.append(f'''    pause 0.1 until eval (rb_current_frame_id == "html_probe_{expected}" and _rb_qa_visible_text() == {OUTCOME_TEXT[expected]!r})
    pause 0.8
    assert eval (hp_state["target"] == {expected!r} and hp_state["reward_count"] == {1 if expected == "win" else 0} and len(hp_state["consumed"]) == 1)
    assert eval hp_observe_main_thread("correct_followup_dialogue", hp_state, hp_ticks)
    assert eval renpy.can_load("hp-completed")
    screenshot "html_probe_result_{expected}"
''')
    return "\n".join(parts)


def prepare(args):
    sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    package = args.package_root.resolve(strict=True)
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    package_files = {p.relative_to(package).as_posix(): file_hash(p) for p in package.rglob("*") if p.is_file()}
    package_files = dict(sorted(package_files.items(), key=lambda item: item[0].encode("utf-8")))
    digest = hashlib.sha256(b"".join(path.encode("utf-8") + b"\x00" + sha.encode("ascii") + b"\n"
                                    for path, sha in package_files.items())).hexdigest()
    game_id = manifest.get("game_id", package.name)
    version = manifest.get("version", manifest.get("package_version", "1.0.0"))
    host_command = json.loads(args.host_command_json) if args.host_command_json else [str((ROOT / "runtimes/html-minigame-host/win-x64/HtmlMinigameHost.exe").resolve())]
    if not isinstance(host_command, list) or not host_command or any(not isinstance(value, str) for value in host_command):
        raise ValueError("--host-command-json must contain an argument array")
    fixture = Project(project_id="project_html_h0_" + run_dir.name, name="HTML 小游戏原生衔接原型",
        save_namespace="html_h0_" + run_dir.name,
        acts={"probe": Act(act_id="probe", frame_ids=["fixture"])},
        frames={"fixture": Frame(frame_id="fixture", act_id="probe", dialogue=Dialogue(text="H0 fixture"), flow=Flow(kind="end"))},
        entry_frame_id="fixture")
    target = run_dir / "中文 HTML 衔接原型"
    generation = generate_project(fixture, run_dir, target, sdk)
    config = {"schema_version": 1, "scenario": args.scenario, "host_command": host_command,
        "package_root": str(package), "entry": manifest.get("entry", "index.html"),
        "game_id": game_id, "package_version": version, "package_hash": digest,
        "inputs": json.loads(args.inputs_json), "devtools_port": args.devtools_port,
        "events_path": str(run_dir / "native-events.jsonl"), "reference_path": str(run_dir / "checkpoint-reference.json"),
        "browser_data_root": str(run_dir / "private-browser-data")}
    write_json(target / "probe-config.json", config)
    shutil.copyfile(PROTOTYPE / "probe_story.rpy", target / "game/script.rpy")
    modules = target / "game/python-packages"
    modules.mkdir(exist_ok=True)
    shutil.copyfile(PROTOTYPE / "html_probe_runtime.py", modules / "html_probe_runtime.py")
    (target / "game/bridge_html_testcases.rpy").write_text(qa_source(args.scenario), encoding="utf-8")
    report = {"schema_version": 1, "probe": "real-native-html-h0", "run_directory": str(run_dir),
        "fixture": str(target), "scenario": args.scenario, "generation": generation,
        "sdk_version": sdk.version, "package_files": package_files, "package_hash": digest,
        "config": config, "commands": {}, "complete": False, "formal_demo_modified": False,
        "actual_audio_output_verified": False, "release_package_verified": False,
        "code_sha256": {str(path.relative_to(ROOT)): file_hash(path) for path in [Path(__file__).resolve(), PROTOTYPE / "probe_story.rpy", PROTOTYPE / "html_probe_runtime.py"]}}
    write_json(run_dir / "report.json", report)
    return run_dir


def execute(run_dir, phase="standard", checks_only=False):
    report_path = run_dir / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    target = Path(report["fixture"])
    shutil.copyfile(PROTOTYPE / "probe_story.rpy", target / "game/script.rpy")
    shutil.copyfile(PROTOTYPE / "html_probe_runtime.py", target / "game/python-packages/html_probe_runtime.py")
    (target / "game/bridge_html_testcases.rpy").write_text(qa_source(report["scenario"], phase), encoding="utf-8")
    report["code_sha256"] = {str(path.relative_to(ROOT)): file_hash(path) for path in [Path(__file__).resolve(), PROTOTYPE / "probe_story.rpy", PROTOTYPE / "html_probe_runtime.py"]}
    saves = run_dir / "private-saves"
    saves.mkdir(exist_ok=True)
    multi = run_dir / "private-multipersistent"
    multi.mkdir(exist_ok=True)
    sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
    try:
        for name, command, extra, timeout in [("compile", "compile", [], 60), ("lint", "lint", ["--error-code"], 60),
            ("html-runtime", "test", ["html_runtime_probe", "--report-detailed"], 210)]:
            if checks_only and command == "test":
                report["checks_complete"] = True
                write_json(report_path, report)
                print(json.dumps({"run_directory": str(run_dir), "report_path": str(report_path), "checks_complete": True}, ensure_ascii=False), flush=True)
                return 0
            result = run_sdk(sdk, target, command, [*extra, "--savedir", str(saves)], timeout_seconds=timeout,
                env={"RENPY_MULTIPERSISTENT": str(multi)})
            key = name if phase == "standard" else phase + "-" + name
            report["commands"][key] = asdict(result)
            (run_dir / (key + ".stdout.log")).write_text(result.stdout, encoding="utf-8")
            (run_dir / (key + ".stderr.log")).write_text(result.stderr, encoding="utf-8")
            for filename in ["log.txt", "traceback.txt", "errors.txt"]:
                if (target / filename).is_file():
                    shutil.copyfile(target / filename, run_dir / (key + "." + filename))
            write_json(report_path, report)
            if result.exit_code != 0 or result.timed_out or not result.cleanup_complete:
                raise RuntimeError(name + " failed: " + (result.stdout + result.stderr)[-4000:])
            if command == "test" and "[rpytest] Status: PASSED" not in result.stdout + result.stderr:
                raise RuntimeError("native test did not explicitly report PASSED")
        events_path = run_dir / "native-events.jsonl"
        records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        if not any(record["kind"] == "ready" for record in records):
            raise RuntimeError("real HTML host did not complete ready/init/start handshake")
        started = {record["payload"]["session_id"] for record in records if record["kind"] == "session_started"}
        reaped = {record["payload"]["session_id"] for record in records if record["kind"] == "session_reaped"}
        if started - reaped:
            raise RuntimeError("owned host cleanup lacks evidence for sessions: " + repr(started - reaped))
        native = [record["payload"] for record in records if record["kind"] == "native_state"]
        expected_stage = "checkpoint_created" if phase == "checkpoint_create" else "responsive_verified" if report["scenario"] == "responsive" else "correct_followup_dialogue"
        if not any(state["stage"] == expected_stage for state in native):
            raise RuntimeError("missing native state evidence")
        report["native_states"] = native
        report["host_cleanup"] = {"started_sessions": sorted(started), "reaped_sessions": sorted(reaped), "complete": True}
        report["save_files"] = [{"path": str(p), "sha256": file_hash(p), "bytes": p.stat().st_size} for p in sorted(saves.rglob("*.save"))]
        report["screenshots"] = [{"path": str(p), "sha256": file_hash(p)} for p in sorted((target / "tests/screenshots").glob("*.png"))]
        report["complete"] = phase != "checkpoint_create"
        report["phases"] = {**report.get("phases", {}), phase: "passed"}
        report["coverage"] = ["real-host-process", "responsive-native-main-thread", "no-runtime-handles-in-store"]
        if report["scenario"] != "responsive" and phase != "checkpoint_create":
            report["coverage"].append("real-html-result-to-native-dialogue:" + report["scenario"])
        if report["scenario"] == "checkpoint_load":
            report["coverage"].append("real-native-checkpoint-written")
            if phase != "checkpoint_create":
                report["coverage"] += ["real-native-checkpoint-save-load", "new-session-after-load", "simulated-stale-message-rejection"]
            if phase == "checkpoint_restore":
                report["coverage"].append("separate-native-process-checkpoint-restoration")
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    write_json(report_path, report)
    print(json.dumps({"run_directory": str(run_dir), "report_path": str(report_path), "complete": report["complete"]}, ensure_ascii=False), flush=True)
    return 0 if report["complete"] or report.get("phases", {}).get(phase) == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", type=Path)
    parser.add_argument("--phase", choices=["standard", "checkpoint_create", "checkpoint_restore"], default="standard")
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--host-command-json")
    parser.add_argument("--inputs-json", default="{}")
    parser.add_argument("--devtools-port", type=int, default=9333)
    parser.add_argument("--scenario", choices=["responsive", "win", "lose", "cancel", "error", "checkpoint_load"], default="win")
    args = parser.parse_args()
    if args.execute:
        return execute(args.execute.resolve(strict=True), args.phase, args.checks_only)
    if not args.prepare or args.package_root is None:
        parser.error("use --prepare --package-root PATH, or --execute RUN_DIRECTORY")
    run_dir = prepare(args)
    print(json.dumps({"run_directory": str(run_dir), "fixture": str(run_dir / "中文 HTML 衔接原型"),
                      "report_path": str(run_dir / "report.json")}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
