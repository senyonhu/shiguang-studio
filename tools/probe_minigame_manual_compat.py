"""Load a real earlier manual save into two independently generated fixtures.

The source run is read only. No saved story/runtime state is reconstructed from
diagnostic JSON; native FileLoad restores the copied .save bytes.
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
from renpy_bridge.generator import generate_project
from renpy_bridge.minigame_packages import import_package
from renpy_bridge.models import Project
from renpy_bridge.pipeline import _code_digest
from renpy_bridge.sdk import discover_sdk, run_sdk

from probe_minigame_generator import NATIVE_QA_SOURCE, TEXT, WINDOW_QA


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/html-minigames/manual-save-compatibility"
CASES = ("variables_changed", "instance_removed", "configuration_missing")


def dump(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def within(path: Path, root: Path):
    result = path.resolve(strict=True)
    result.relative_to(root.resolve(strict=True))
    if result.is_symlink():
        raise ValueError("evidence paths must be ordinary files/directories")
    return result


def testcase(case: str, old_state: dict):
    expected_phase = "error" if case == "variables_changed" else "incompatible"
    expected_code = {"variables_changed": "MINIGAME_SAVE_VARIABLES_CHANGED", "instance_removed": "MINIGAME_STATE_INVALID",
        "configuration_missing": "MINIGAME_SAVE_INCOMPATIBLE"}[case]
    manager_assert = '''    assert eval (_rb_mg._MANAGER is None)
''' if case == "configuration_missing" else f'''    assert eval (_rb_mg.manager().active is None)
    assert eval (_rb_mg.manager().epoch > {old_state["load_epoch"]!r})
'''
    wait = f'''    pause 0.1 until eval (rb_current_frame_id == "frame_error" and _rb_qa_visible_text() == {TEXT["error"]!r})
''' if case == "variables_changed" else '''    pause 0.1 until screen "rb_minigame_incompatible"
'''
    return NATIVE_QA_SOURCE + WINDOW_QA + f'''
init 21 python:
    def _rb_compat_record():
        manager = _rb_mg._MANAGER
        value = {{"state": _rb_mg._copy(rb_minigame_state), "reward": rb_story_variables["reward"],
            "manager_epoch": manager.epoch if manager is not None else None,
            "manager_has_active_host": manager is not None and manager.active is not None}}
        path = _rb_prod_window.Path(config.savedir) / "loaded-state-evidence.json"
        path.write_text(_rb_prod_window.json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return True

    def _rb_compat_observe_window(stage):
        # A missing configuration must recover before a Manager exists. This
        # object supplies no process/input capability to the read-only observer.
        import types as _rb_compat_types
        manager = _rb_mg._MANAGER or _rb_compat_types.SimpleNamespace(active=None)
        parent = _rb_prod_geometry.read_parent(renpy.get_sdl_dll(), renpy.get_sdl_window_pointer(), renpy.display.draw)
        value = {{"stage": stage, **parent, **_rb_prod_window._owned_windows(parent, manager)}}
        path = _rb_prod_window.Path(config.savedir) / "production-window.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(_rb_prod_window.json.dumps(value, ensure_ascii=False) + "\\n")
        return True

testsuite global:
    teardown:
        pause 3.0
        exit

testcase old_manual_{case}:
    $ _test.timeout = 90.0
    $ _preferences.text_cps = 0
    run FileLoad(1, page="1", confirm=False)
{wait.rstrip()}
    pause 0.5
    assert eval (rb_minigame_state["phase"] == {expected_phase!r})
    assert eval (rb_minigame_state["error"]["code"] == {expected_code!r})
    assert eval (rb_minigame_state["logical_entry_id"] == {old_state["logical_entry_id"]!r})
    assert eval (rb_story_variables["reward"] == 0)
{manager_assert.rstrip()}
    assert eval _rb_compat_record()
    assert eval _rb_compat_observe_window("old_manual_{case}_recovered")
    screenshot "production_manual_{case}_recovered"
''' + ('''    click "返回主菜单"
    pause 0.1 until screen "main_menu"
    screenshot "production_manual_incompatible_main_menu"
''' if case in {"instance_removed", "configuration_missing"} else "")


def prepare(source_run: Path):
    source_run = within(source_run, ROOT / "evidence/html-minigames/production-generator/runs")
    run = EVIDENCE / "runs" / str(time.time_ns())
    run.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "probe": "actual-old-manual-save-config-compatibility", "complete": False,
        "source_run": str(source_run), "run_directory": str(run), "source_modified": False,
        "diagnostic_json_used_to_restore_state": False, "cases": {}, "probe_tool_sha256": sha(Path(__file__)),
        "core_digest_at_prepare": _code_digest()}
    dump(run / "report.json", report)
    (run / "probe-source.py").write_bytes(Path(__file__).read_bytes())
    try:
        source_report_path = source_run / "report.json"
        source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
        if not source_report.get("complete") or any(not source_report.get("phase_evidence", {}).get(name, {}).get("passed")
                for name in ("manual_create", "manual_restore", "manual_completed_restore")):
            raise RuntimeError("the source must contain three successful real manual save phases")
        source_fixture = within(Path(source_report["fixture"]), source_run)
        source_integrity = verify_derived_sources(source_fixture, run / "source-integrity")
        if source_integrity["status"] != "passed":
            raise RuntimeError("source fixture does not match its earlier authoritative baseline")
        source_save = within(Path(source_report["phase_evidence"]["manual_create"]["disk_save"]), source_run)
        source_save_hash = sha(source_save)
        if source_save_hash != source_report["phase_evidence"]["manual_create"]["disk_save_sha256"]:
            raise RuntimeError("source manual .save bytes changed since the passed source run")
        with zipfile.ZipFile(source_save) as archive:
            disk_metadata = json.loads(archive.read("json"))
        source_model = within(Path(source_report["authoring_model"]), source_run)
        old_model = json.loads(source_model.read_text(encoding="utf-8"))
        reference_path = within(source_run / "private-saves/manual-save-reference.json", source_run)
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        old_state = reference["state"]
        if reference["metadata"] != disk_metadata or old_state["phase"] != "paused" or reference["reward"] != 0:
            raise RuntimeError("source saved-slot evidence is inconsistent")
        report["source_evidence"] = {"report_path": str(source_report_path), "report_sha256": sha(source_report_path),
            "save_path": str(source_save), "save_sha256": source_save_hash, "disk_metadata": disk_metadata,
            "model_path": str(source_model), "model_sha256": sha(source_model), "reference_path": str(reference_path),
            "reference_sha256": sha(reference_path), "derived_integrity": source_integrity}
        sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
        for case in CASES:
            case_root = run / case
            case_root.mkdir()
            authoring = case_root / "authoring"
            data = json.loads(json.dumps(old_model))
            if case == "variables_changed":
                data["variables"]["reward"]["maximum"] = 11
            elif case == "instance_removed":
                data["frames"]["frame_game"]["flow"]["instance_id"] = "replacement_instance"
            project = Project.model_validate(data)
            definition = next(iter(project.minigames.values()))
            package_root = within(source_model.parent / definition.path, source_run)
            info = import_package(package_root, authoring / "minigames")
            if info.digest != definition.package_digest or info.prototype:
                raise RuntimeError("the source immutable formal package does not match its project")
            dump(authoring / "fixture-project.json", project.model_dump(mode="json"))
            target = case_root / "generated"
            generation = generate_project(project, authoring, target, sdk, minigame_development=True,
                defer_minigame_integrity=True)
            config_fault = None
            if case == "configuration_missing":
                configuration_path = target / "game/bridge-minigames.json"
                config_fault = {"deliberately_absent_path": str(configuration_path),
                    "original_sha256": sha(configuration_path), "removed_before_first_authoritative_baseline": True}
                (case_root / "omitted-configuration-source.json").write_bytes(configuration_path.read_bytes())
                configuration_path.unlink()
            (target / "game/bridge_manual_compat_qa.rpy").write_text(testcase(case, old_state), encoding="utf-8")
            window_qa = target / "game/python-packages/embedded_window.py"
            window_qa.write_bytes((ROOT / "prototypes/html_embedded/embedded_window.py").read_bytes())
            generation["derived_sources"] = record_derived_sources(target, managed_roots=[
                *generation["derived_sources"]["managed_roots"], "game/python-packages/embedded_window.py"])
            saves, multi = case_root / "private-saves", case_root / "private-multipersistent"
            saves.mkdir()
            multi.mkdir()
            copied_save = saves / source_save.name
            copied_save.write_bytes(source_save.read_bytes())
            if sha(copied_save) != source_save_hash:
                raise RuntimeError("copied native slot differs from its read-only source")
            qa_options = case_root / "qa-options.json"
            dump(qa_options, {"development": True, "devtools_port": 9351, "event_log_path": str(saves / "runtime-events.jsonl")})
            report["cases"][case] = {"fixture": str(target), "generation": generation, "save_path": str(copied_save),
                "save_sha256": sha(copied_save), "prepared": True, "complete": False, "commands": {},
                "testcase_name": "old_manual_" + case, "package_digest": info.digest,
                "configuration_fault": config_fault,
                "env": {"RENPY_MULTIPERSISTENT": str(multi), "RENPY_BRIDGE_MINIGAME_QA_JSON": str(qa_options)}}
        report["prepared"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    dump(run / "report.json", report)
    print(json.dumps({"run_directory": str(run), "prepared": report.get("prepared", False)}, ensure_ascii=False), flush=True)
    return 0 if report.get("prepared") else 1


def execute(run: Path):
    run = within(run, EVIDENCE / "runs")
    path = run / "report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["complete"] = False
    try:
        if not report.get("prepared") or report["probe_tool_sha256"] != sha(Path(__file__)):
            raise RuntimeError("probe preparation/source identity is invalid")
        if report["core_digest_at_prepare"] != _code_digest():
            raise RuntimeError("core source changed after fixture generation")
        sdk = discover_sdk(ROOT / "runtimes/renpy-8.5.3")
        for case in CASES:
            detail = report["cases"][case]
            target, saves = Path(detail["fixture"]), run / case / "private-saves"
            if verify_derived_sources(target, run / case / "integrity-before")["status"] != "passed":
                raise RuntimeError("test fixture was edited after authoritative generation")
            for name, command, args, timeout in [("compile", "compile", [], 60), ("lint", "lint", ["--error-code"], 60),
                    ("native-load", "test", [detail["testcase_name"], "--report-detailed"], 120)]:
                result = run_sdk(sdk, target, command, [*args, "--savedir", str(saves)], timeout_seconds=timeout, env=detail["env"])
                detail["commands"][name] = asdict(result)
                (run / case / (name + ".stdout.log")).write_text(result.stdout, encoding="utf-8")
                (run / case / (name + ".stderr.log")).write_text(result.stderr, encoding="utf-8")
                dump(path, report)
                if result.exit_code != 0 or result.timed_out or not result.cleanup_complete:
                    raise RuntimeError(case + "/" + name + " failed")
                if command == "test":
                    statuses = re.findall(r'^\[rpytest\]\s+(PASSED|PENDING|SKIPPED|XFAILED|FAILED|XPASSED)\s+(?:global::)?'
                        + re.escape(detail["testcase_name"]) + r'(?=\s|$)', result.stdout + result.stderr, flags=re.MULTILINE)
                    if not statuses or any(status != "PASSED" for status in statuses):
                        raise RuntimeError("requested old-save testcase did not finish PASSED")
                    detail["requested_testcase_statuses"] = statuses
            loaded_path = saves / "loaded-state-evidence.json"
            loaded = json.loads(loaded_path.read_text(encoding="utf-8"))
            if loaded["reward"] != 0 or loaded["manager_has_active_host"]:
                raise RuntimeError("incompatible manual save launched a host or granted a reward")
            window_path = saves / "production-window.jsonl"
            windows = [json.loads(line) for line in window_path.read_text(encoding="utf-8").splitlines()]
            if not windows or any(item["visible_owned_top_level_count"] != 1 for item in windows):
                raise RuntimeError("native recovery did not retain one visible SDK root window")
            if any(child["class_name"] == "Chrome_RenderWidgetHostHWND" for item in windows for child in item["child_windows"]):
                raise RuntimeError("incompatible manual save created an embedded WebView")
            event_path = saves / "runtime-events.jsonl"
            events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()] if event_path.exists() else []
            if any(item["type"] == "launch" for item in events):
                raise RuntimeError("incompatible manual save launched a minigame host")
            detail.update(complete=True, loaded_state=loaded, loaded_state_path=str(loaded_path), loaded_state_sha256=sha(loaded_path),
                window_observations=windows, window_evidence_path=str(window_path), window_evidence_sha256=sha(window_path),
                new_host_launches=0, source_save_copy_unchanged=sha(Path(detail["save_path"])) == detail["save_sha256"],
                derived_integrity=verify_derived_sources(target, run / case / "integrity-after"),
                screenshots=[{"path": str(p), "sha256": sha(p)} for p in sorted((target / "tests/screenshots").glob("*.png"))])
            if not detail["source_save_copy_unchanged"] or detail["derived_integrity"]["status"] != "passed":
                raise RuntimeError("native compatibility execution altered protected source files")
            dump(path, report)
        for field in ("report", "save", "model", "reference"):
            source = report["source_evidence"]
            if sha(Path(source[field + "_path"])) != source[field + "_sha256"]:
                raise RuntimeError("read-only source evidence was changed")
        if report["core_digest_at_prepare"] != _code_digest():
            raise RuntimeError("core source changed during execution")
        report["core_source_stable"] = True
        report["complete"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    dump(path, report)
    print(json.dumps({"report_path": str(path), "complete": report["complete"]}, ensure_ascii=False), flush=True)
    return 0 if report["complete"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", type=Path)
    parser.add_argument("--execute", type=Path)
    args = parser.parse_args()
    if args.execute:
        return execute(args.execute)
    if args.source_run:
        return prepare(args.source_run)
    parser.error("use --source-run SOURCE_MANUAL_RUN or --execute PREPARED_RUN")


if __name__ == "__main__":
    sys.exit(main())
