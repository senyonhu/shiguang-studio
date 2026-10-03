"""Isolated real Ren'Py SDL-child WebView2 probe; no separate game window.

Reuse the H0 logical checkpoint/result probe, adding only native ownership
observation and parent parameters. Browser runtime stays at its frozen SDK
location; this does not claim a portable production release.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "prototypes/html_embedded/embedded_window.py"
spec = importlib.util.spec_from_file_location("html_h0_probe", ROOT / "tools/probe_html_renpy.py")
h0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h0)
h0.EVIDENCE = ROOT / "evidence/html-minigames/embedded-renpy"
_original_qa_source = h0.qa_source


def embedded_qa_source(scenario, phase="standard"):
    if phase != "completed_restore":
        return _original_qa_source(scenario, phase)
    return h0.NATIVE_QA_SOURCE + f'''testsuite global:
    teardown:
        $ hp_rt.invalidate("completed_load_teardown")
        pause 3.0
        exit

testcase html_runtime_probe:
    $ _test.timeout = 60.0
    $ _preferences.text_cps = 0
    assert eval renpy.can_load("hp-completed")
    run FileLoad("hp-completed", slot=True, confirm=False)
    pause 0.1 until eval (rb_current_frame_id == "html_probe_win" and _rb_qa_visible_text() == {h0.OUTCOME_TEXT["win"]!r})
    pause 0.8
    assert eval (hp_state["target"] == "win" and hp_state["reward_count"] == 1 and len(hp_state["consumed"]) == 1)
    assert eval hp_observe_main_thread("correct_followup_dialogue", hp_state, hp_ticks)
    screenshot "html_probe_completed_save_no_second_award"
'''


h0.qa_source = embedded_qa_source


def install_embedded_fixture(target):
    source = (h0.PROTOTYPE / "probe_story.rpy").read_text(encoding="utf-8")
    source = source.replace("    import html_probe_runtime as hp_rt\n", "    import html_probe_runtime as hp_rt\n    import embedded_window as hp_embedded\n")
    source = source.replace("    def hp_observe_main_thread(stage, state, ticks=0):\n",
        "    def hp_embedded_prepare_parent():\n"
        "        return hp_embedded.prepare_parent(renpy.get_sdl_dll(), renpy.get_sdl_window_pointer(), hp_rt)\n\n"
        "    def hp_observe_main_thread(stage, state, ticks=0):\n"
        "        hp_embedded.observe(stage, renpy.get_sdl_dll(), renpy.get_sdl_window_pointer(), hp_rt)\n")
    source = source.replace("        hp_ticks += 1\n", "        hp_ticks += 1\n"
        "        if hp_rt.current_session_id() is None and hp_rt._launch_allowed:\n            hp_embedded_prepare_parent()\n")
    source = source.replace("        if event is not None:\n", "        hp_embedded.observe_session(renpy.get_sdl_dll(), renpy.get_sdl_window_pointer(), hp_rt)\n        if event is not None:\n")
    source = source.replace('    "原型入口：推进后打开专用 HTML 小游戏窗口。"', '    "剧情入口：推进后在当前游戏窗口中开始 HTML 挑战。"')
    source = source.replace("    $ hp_rt.enable_launch()\n", "    $ hp_embedded_prepare_parent()\n    $ hp_rt.enable_launch()\n")
    source = source.replace("screen hp_waiting():\n    modal True\n", "screen hp_waiting():\n    modal True\n    $ quick_menu = False\n")
    (target / "game/script.rpy").write_text(source, encoding="utf-8")
    shutil.copyfile(SOURCE, target / "game/python-packages/embedded_window.py")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--execute", type=Path)
    parser.add_argument("--checks-only", action="store_true")
    parser.add_argument("--package-root", type=Path)
    parser.add_argument("--scenario", choices=["responsive", "win", "lose", "cancel", "error", "checkpoint_load"], default="win")
    parser.add_argument("--devtools-port", type=int, default=9345)
    parser.add_argument("--inputs-json", default="{}")
    parser.add_argument("--phase", choices=["standard", "checkpoint_create", "checkpoint_restore", "completed_restore"], default="standard")
    parser.add_argument("--host", type=Path, default=ROOT / "runtimes/html-minigame-embedded-host/win-x64/HtmlMinigameEmbeddedHost.exe")
    parser.add_argument("--fixed-runtime-root", type=Path, default=ROOT / "runtimes/webview2-fixed-154.0.4258.53/Microsoft.WebView2.FixedVersionRuntime.154.0.4258.53.x64")
    args = parser.parse_args()
    if args.execute:
        run = args.execute.resolve(strict=True)
        report = json.loads((run / "report.json").read_text(encoding="utf-8"))
        report["complete"] = False
        h0.write_json(run / "report.json", report)
        target = Path(report["fixture"])
        event_file = run / "native-events.jsonl"
        events_before = len(event_file.read_text(encoding="utf-8").splitlines()) if event_file.is_file() else 0
        # H0 executor installs its copied source immediately before compile.
        # Its source-copy hook is replaced only inside this isolated process.
        original_copy = h0.shutil.copyfile
        original_run_sdk = h0.run_sdk
        def copy_for_embedded(source, destination, *copy_args, **copy_kwargs):
            result = original_copy(source, destination, *copy_args, **copy_kwargs)
            if Path(source) == h0.PROTOTYPE / "probe_story.rpy":
                install_embedded_fixture(target)
            return result
        h0.shutil.copyfile = copy_for_embedded
        def run_embedded_sdk(*sdk_args, **sdk_kwargs):
            sdk_kwargs["env"] = {**sdk_kwargs.get("env", {}), **report.get("environment", {})}
            return original_run_sdk(*sdk_args, **sdk_kwargs)
        h0.run_sdk = run_embedded_sdk
        try:
            result = h0.execute(run, args.phase, args.checks_only)
        finally:
            h0.shutil.copyfile = original_copy
            h0.run_sdk = original_run_sdk
        report = json.loads((run / "report.json").read_text(encoding="utf-8"))
        report["probe"] = "real-native-html-sdl-child-prototype"
        report["embedded_probe_sources"] = {str(Path(__file__).relative_to(ROOT)): h0.file_hash(Path(__file__)),
            str(SOURCE.relative_to(ROOT)): h0.file_hash(SOURCE), "generated/game/script.rpy": h0.file_hash(target / "game/script.rpy")}
        report["same_window_verified"] = False
        evidence = run / "parent-window.jsonl"
        if evidence.is_file():
            observations = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()]
            ready = [item for item in observations if item["stage"] == "actual_page_ready"]
            report["same_window_verified"] = bool(ready and all(item["visible_owned_top_level_count"] == 1 and any(
                child["visible"] and child["process_id"] != item["process_id"] and child["root_hwnd"] == item["hwnd"]
                for child in item["child_windows"]) for item in ready))
            report["window_observations"] = observations
        if event_file.is_file():
            new_records = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines()[events_before:]]
            launched = [item for item in new_records if item["kind"] == "session_started"]
            phase_windows = [item["payload"] for item in new_records if item["kind"] == "embedded_parent_window"]
            phase_proof = {"native_root_windows_observed": bool(phase_windows),
                "native_root_process_ids": sorted({item["process_id"] for item in phase_windows}),
                "new_host_sessions": len(launched), "unique_visible_top_level": all(item["visible_owned_top_level_count"] == 1 for item in phase_windows)}
            report.setdefault("embedded_phase_evidence", {})[args.phase] = phase_proof
            if args.phase == "completed_restore":
                phase_proof["completed_load_no_new_host_or_second_reward"] = bool(phase_windows and not launched)
                if not phase_proof["completed_load_no_new_host_or_second_reward"]:
                    report["complete"] = False
                    report["error"] = "Completed save load unexpectedly opened a new HTML host or lacked native window observations"
                    result = 1
                else:
                    report.setdefault("coverage", []).append("completed-save-new-process-no-second-reward-or-html-relaunch")
        if report.get("complete") and not report["same_window_verified"]:
            report["complete"] = False
            report["error"] = "Native ownership observations did not prove the HTML host is visible under the single SDK root window"
            result = 1
        h0.write_json(run / "report.json", report)
        print(json.dumps({"report_path": str(run / "report.json"), "complete": report.get("complete"),
            "same_window_verified": report["same_window_verified"]}, ensure_ascii=False), flush=True)
        return result
    if not args.prepare or args.package_root is None:
        parser.error("use --prepare --package-root PATH or --execute RUN_DIRECTORY")
    args.host_command_json = json.dumps([str(args.host.resolve())])
    run = h0.prepare(args)
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    install_embedded_fixture(Path(report["fixture"]))
    report.update(probe="real-native-html-sdl-child-prototype", same_window_verified=False,
        environment={"WEBVIEW2_BROWSER_EXECUTABLE_FOLDER": str(args.fixed_runtime_root.resolve())},
        embedded_host_sha256=h0.file_hash(args.host.resolve(strict=True)),
        release_package_verified=False)
    h0.write_json(run / "report.json", report)
    print(json.dumps({"run_directory": str(run), "report_path": str(run / "report.json"), "prepared": True}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
