"""Independent acceptance gates: report fixtures and real immutable package trees.

No SDK, host, browser, or inert executable is run here. Passing these tests is
only evidence for fail-closed report/coverage and filesystem compatibility
boundaries; it is never gameplay, same-window, or old-player-save evidence.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import pytest

from renpy_bridge import compatibility, minigame_qa as qa, pipeline
from renpy_bridge.derived_integrity import record_derived_sources, verify_derived_sources
from renpy_bridge.errors import BridgeError
from renpy_bridge.minigame_packages import validate_directory
from renpy_bridge.models import Project
from renpy_bridge.sdk import ProcessResult
from renpy_bridge.storage import atomic_json, file_hash


PNG_FIXTURE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3X8AAAAASUVORK5CYII=")


def process_result(directory, stdout="", *, exit_code=0, timed_out=False):
    return ProcessResult(["never-executed-report-fixture"], str(directory), exit_code, stdout, "", 0.01, timed_out, 0, None)


def detailed(case, status="PASSED", *, extra=""):
    # Mirrors the local official 8.5.3 detailed report. A PASSED global suite
    # can contain a PENDING requested case, as preserved in job_5da07b1... .
    return ("[rpytest] Test outcomes (Detailed)\n"
            "[rpytest] PASSED  global - (1.0 s)\n"
            f"[rpytest]   {status} {case} - (0.5 s)\n" + extra +
            "[rpytest] Test outcomes (Summary)\n[rpytest] Status: PASSED\n")


def run_report_fixture(tmp_path, monkeypatch, stdout, *, fresh_images=2, stale_images=False,
                       native_exit=0, native_timeout=False, case="case_audit"):
    target, saves, evidence = tmp_path / "generated", tmp_path / "private", tmp_path / "evidence"
    for path in (target, saves, evidence):
        path.mkdir(parents=True)
    names = ("minigame_native_waiting.png", "minigame_native_return.png")
    screenshot_directory = target / "tests/screenshots"
    screenshot_directory.mkdir(parents=True)
    if stale_images:
        for name in names:
            image = screenshot_directory / name
            image.write_bytes(PNG_FIXTURE)
            os.utime(image, (time.time() - 86400, time.time() - 86400))

    def native(*args, **kwargs):
        for name in names[:fresh_images]:
            (screenshot_directory / name).write_bytes(PNG_FIXTURE)
        return process_result(target, stdout, exit_code=native_exit, timed_out=native_timeout)

    def browser(argv, *args, **kwargs):
        output = Path(argv[argv.index("--output") + 1])
        atomic_json(output / "report.json", {"status": "passed", "scope": "fixture_only_not_browser_evidence"})
        return process_result(output)

    monkeypatch.setattr(qa.shutil, "which", lambda name: "fixture-node")
    monkeypatch.setattr(qa, "run_sdk", native)
    monkeypatch.setattr(qa, "_execute", browser)
    config, actions = saves / "config.json", saves / "actions.json"
    atomic_json(config, {"development": True})
    atomic_json(actions, [])
    return qa.run_actual_qa(object(), target, saves, {}, {"config": config, "port": 54321,
                            "actions": actions, "case_name": case}, evidence)


def test_requested_case_and_both_fresh_images_are_required(tmp_path, monkeypatch):
    report = run_report_fixture(tmp_path, monkeypatch, detailed("case_audit"))
    assert report["passed"] is True
    assert len(report["screenshots"]) == 2


@pytest.mark.parametrize("status", ["PENDING", "SKIPPED", "XFAILED", "XPASSED", "FAILED"])
def test_global_pass_cannot_substitute_requested_case_pass(tmp_path, monkeypatch, status):
    report = run_report_fixture(tmp_path, monkeypatch, detailed("case_audit", status))
    assert report["passed"] is False


def test_neighbor_case_name_cannot_satisfy_requested_case(tmp_path, monkeypatch):
    report = run_report_fixture(tmp_path, monkeypatch, detailed("case_audit_extra"))
    assert report["passed"] is False


def test_log_line_outside_detailed_section_cannot_override_pending_case(tmp_path, monkeypatch):
    stdout = "[rpytest]   PASSED case_audit - (0.5 s)\n" + detailed("case_audit", "PENDING")
    report = run_report_fixture(tmp_path, monkeypatch, stdout)
    assert report["passed"] is False


def test_conflicting_duplicate_case_rows_do_not_pass(tmp_path, monkeypatch):
    stdout = detailed("case_audit", extra="[rpytest]   PENDING case_audit\n")
    report = run_report_fixture(tmp_path, monkeypatch, stdout)
    assert report["passed"] is False


@pytest.mark.parametrize("fresh_images,stale_images", [(0, False), (1, False), (0, True)])
def test_missing_or_stale_return_capture_cannot_pass(tmp_path, monkeypatch, fresh_images, stale_images):
    report = run_report_fixture(tmp_path, monkeypatch, detailed("case_audit"), fresh_images=fresh_images, stale_images=stale_images)
    assert report["passed"] is False


@pytest.mark.parametrize("native_exit,native_timeout", [(1, False), (0, True)])
def test_native_failure_cannot_be_hidden_by_detailed_text(tmp_path, monkeypatch, native_exit, native_timeout):
    report = run_report_fixture(tmp_path, monkeypatch, detailed("case_audit"), native_exit=native_exit, native_timeout=native_timeout)
    assert report["passed"] is False


@pytest.fixture
def timed_project():
    root = Path(__file__).resolve().parents[1]
    info = validate_directory(root / "examples/html_minigames/production/timed")
    frame_ids = ["entry", "high", "win", "lose", "cancel", "error", "fallback"]
    frames = {key: {"frame_id": key, "act_id": "act", "flow": {"kind": "end"}} for key in frame_ids}
    frames["entry"]["flow"] = {
        "kind": "minigame", "game_id": "prod_timed", "instance_id": "signal",
        "result_branches": [
            {"branch_id": "high", "outcome": "win", "predicate": {"op": "gte",
                "left": {"kind": "result", "field": "score"}, "right": {"kind": "literal", "value": 500}},
             "target_frame_id": "high"},
            {"branch_id": "win", "outcome": "win", "target_frame_id": "win"},
            {"branch_id": "lose", "outcome": "lose", "target_frame_id": "lose"}],
        "cancel_target_frame_id": "cancel", "error_target_frame_id": "error", "fallback_target_frame_id": "fallback",
    }
    return Project.model_validate({"schema_version": "1.1", "project_id": "audit", "save_namespace": "audit",
        "entry_frame_id": "entry", "acts": {"act": {"act_id": "act", "frame_ids": frame_ids}}, "frames": frames,
        "minigames": {"prod_timed": {"game_id": "prod_timed", "package_digest": info.digest,
            "package_version": info.manifest["version"], "path": "minigames/" + info.digest, "manifest": info.manifest}}})


def scenario(outcome="win", *, frame_id="entry"):
    result = {"frame_id": frame_id, "expected_outcome": outcome,
              "expected_target_frame_id": "high" if outcome == "win" else outcome, "actions": []}
    if outcome == "error":
        result["fault_plan"] = {"type": "terminate_own_host", "after": "running"}
    return result


def full_minimum_scenarios():
    return [scenario(outcome) for outcome in ("win", "lose", "cancel", "error")]


@pytest.mark.parametrize("missing", ["win", "lose", "cancel", "error"])
def test_build_cannot_omit_a_declared_or_terminal_outcome(timed_project, missing):
    scenarios = [item for item in full_minimum_scenarios() if item["expected_outcome"] != missing]
    with pytest.raises(BridgeError) as error:
        qa.validate_release_coverage(timed_project, scenarios)
    assert error.value.code == "MINIGAME_RELEASE_COVERAGE_INCOMPLETE"


def test_complete_minimum_outcomes_do_not_certify_predicate_branches_or_combinations(timed_project):
    coverage = qa.validate_release_coverage(timed_project, full_minimum_scenarios())
    assert coverage["result_branches_verified"] is False
    assert coverage["combination_routes_verified"] is False


def test_each_story_call_needs_its_own_outcome_set(timed_project):
    data = timed_project.model_dump(mode="json")
    data["frames"]["second_entry"] = deepcopy(data["frames"]["entry"])
    data["frames"]["second_entry"]["frame_id"] = "second_entry"
    data["frames"]["second_entry"]["flow"]["instance_id"] = "signal_again"
    data["acts"]["act"]["frame_ids"].append("second_entry")
    project = Project.model_validate(data)
    with pytest.raises(BridgeError) as error:
        qa.validate_release_coverage(project, [*full_minimum_scenarios(), scenario(frame_id="second_entry")])
    assert error.value.code == "MINIGAME_RELEASE_COVERAGE_INCOMPLETE"


def test_single_scenario_is_still_allowed_for_narrow_explicit_qa(timed_project, tmp_path):
    _, prepared, coverage = qa.prepare_scenario_suite(timed_project, [scenario()], tmp_path, tmp_path / "private")
    assert len(prepared) == 1
    assert coverage["complete_for_finite_graph"] is False
    assert coverage["combination_routes_verified"] is False


def test_error_qa_requires_explicit_own_host_fault(timed_project):
    request = scenario("error")
    request.pop("fault_plan")
    with pytest.raises(BridgeError):
        qa.validate_minigame_options(request)
    exact = qa.validate_minigame_options(scenario("error"))
    assert exact["fault_plan"] == {"type": "terminate_own_host", "after": "running"}
    text = qa.testcase_source(timed_project, exact)
    assert "development_terminate_own_host" in text
    assert 'finish("error"' not in text and "reportError(" not in text


@pytest.mark.parametrize("fault_plan", [
    {"type": "terminate_process", "after": "running"},
    {"type": "terminate_own_host", "after": "ready"},
    {"type": "terminate_own_host", "after": "running", "pid": 123},
    {"type": "terminate_own_host", "after": "running", "session_id": "arbitrary"},
    True, [], "terminate_own_host",
])
def test_fault_plan_cannot_choose_process_identity_or_arbitrary_action(fault_plan):
    request = scenario("error")
    request["fault_plan"] = fault_plan
    with pytest.raises(BridgeError):
        qa.validate_minigame_options(request)


def test_fault_plan_cannot_mix_with_win_or_page_actions():
    win = scenario()
    win["fault_plan"] = {"type": "terminate_own_host", "after": "running"}
    with pytest.raises(BridgeError):
        qa.validate_minigame_options(win)
    error = scenario("error")
    error["actions"] = [{"type": "click", "selector": "#arbitrary"}]
    with pytest.raises(BridgeError):
        qa.validate_minigame_options(error)


def create_package(path, version):
    path.mkdir(parents=True)
    (path / "index.html").write_text("<!doctype html><html><body>package compatibility fixture</body></html>", encoding="utf-8")
    (path / "LICENSE.txt").write_text("Original filesystem fixture; no executable dependency.", encoding="utf-8")
    (path / "instructions.txt").write_text("旧版说明不能在发行更新时丢失。", encoding="utf-8")
    manifest = {"format_version": "1.0", "game_id": "compat_game", "version": version,
        "entry": "index.html", "protocol": "1.0", "inputs": {},
        "results": {"outcomes": ["win"], "fields": {}},
        "save_capabilities": {"entry_restart": True, "checkpoint": False, "snapshot_version": None}, "files": []}
    for file in sorted(path.iterdir()):
        manifest["files"].append({"path": file.name, "sha256": file_hash(file), "bytes": file.stat().st_size})
    atomic_json(path / "manifest.json", manifest)
    return validate_directory(path)


def installed(info):
    return {"game_id": info.manifest["game_id"], "package_version": info.manifest["version"],
            "package_digest": info.digest, "package_root": "game/bridge_minigames/" + info.digest,
            "manifest": info.manifest, "files": {item.path: {"sha256": item.sha256, "bytes": item.bytes} for item in info.files}}


@pytest.fixture
def previous_and_current(tmp_path):
    previous, current = tmp_path / "previous", tmp_path / "current"
    old = create_package(tmp_path / "old-package", "1.0.0")
    new = create_package(tmp_path / "new-package", "1.1.0")
    for root, info in ((previous, old), (current, new)):
        game = installed(info)
        shutil.copytree(info.root, root / game["package_root"])
        (root / "game/script.rpy").write_text("# inert compatibility fixture\n", encoding="utf-8")
        atomic_json(root / "bridge-manifest.json", {"project_id": "audit", "sdk_version": "8.5.3", "revision": 1,
                                                    "assets": {}, "minigames": {"compat_game": game}})
        atomic_json(root / "game/bridge-minigames.json", {"project_id": "audit", "save_namespace": "audit",
                                                       "minigames": {"compat_game": game}})
    record_derived_sources(previous, managed_roots=["game/bridge_minigames", "game/bridge-minigames.json"])
    return previous, current, old, new


def test_old_package_retention_precedes_first_current_integrity_baseline(previous_and_current, tmp_path):
    previous, current, old, new = previous_and_current
    config_before = file_hash(current / "game/bridge-minigames.json")
    report = compatibility.prepare_previous_release(previous, current, "audit", "8.5.3")
    config = json.loads((current / "game/bridge-minigames.json").read_text(encoding="utf-8"))
    assert old.digest in config["retained_games"]
    assert file_hash(current / "game/bridge-minigames.json") != config_before
    assert validate_directory(current / "game/bridge_minigames" / old.digest).digest == old.digest
    assert validate_directory(current / "game/bridge_minigames" / new.digest).digest == new.digest
    assert report["save_compatibility_verified"] is False
    record_derived_sources(current, managed_roots=["game/bridge_minigames", "game/bridge-minigames.json"])
    assert verify_derived_sources(current, tmp_path / "verify")["status"] == "passed"


def test_changed_previous_license_fails_before_current_config_changes(previous_and_current):
    previous, current, old, _ = previous_and_current
    (previous / "game/bridge_minigames" / old.digest / "LICENSE.txt").write_text("tampered", encoding="utf-8")
    original = (current / "game/bridge-minigames.json").read_bytes()
    with pytest.raises(BridgeError):
        compatibility.prepare_previous_release(previous, current, "audit", "8.5.3")
    assert (current / "game/bridge-minigames.json").read_bytes() == original
    assert not (current / "game/bridge_minigames" / old.digest).exists()


def test_release_required_members_include_previous_manifest_license_and_text(previous_and_current):
    previous, current, old, new = previous_and_current
    compatibility.prepare_previous_release(previous, current, "audit", "8.5.3")
    manifest = json.loads((current / "bridge-manifest.json").read_text(encoding="utf-8"))
    config = json.loads((current / "game/bridge-minigames.json").read_text(encoding="utf-8"))
    # A fake distributor may omit any source member. These declarations must
    # therefore be explicitly checked in the extracted package, not inferred
    # from a broad build.classify rule or current-game QA.
    required = pipeline._minigame_required_members(manifest, config)
    for info in (old, new):
        for name in ("manifest.json", "LICENSE.txt", "instructions.txt", "index.html"):
            key = "game/bridge_minigames/" + info.digest + "/" + name
            assert required[key]["sha256"] == file_hash(info.root / name)


def test_required_runtime_members_use_actual_relative_executable_and_fixed_root(previous_and_current):
    _, current, _, _ = previous_and_current
    manifest = json.loads((current / "bridge-manifest.json").read_text(encoding="utf-8"))
    config = json.loads((current / "game/bridge-minigames.json").read_text(encoding="utf-8"))
    host_file = {"sha256": hashlib.sha256(b"inert host fixture").hexdigest(), "bytes": 18}
    notice = {"sha256": hashlib.sha256(b"notice").hexdigest(), "bytes": 6}
    browser = {"sha256": hashlib.sha256(b"inert fixed browser").hexdigest(), "bytes": 19}
    config["host"] = {"relative_executable": "html-runtime/embedded-win64/EmbeddedHost.exe",
                      "files": {"EmbeddedHost.exe": host_file, "THIRD_PARTY_NOTICES.txt": notice}}
    config["fixed_runtime"] = {"relative_root": "html-runtime/fixed-browser",
                               "files": {"msedgewebview2.exe": browser, "Locales/zh-CN.pak": notice}}
    required = pipeline._minigame_required_members(manifest, config)
    assert required["html-runtime/embedded-win64/EmbeddedHost.exe"] == host_file
    assert required["html-runtime/embedded-win64/THIRD_PARTY_NOTICES.txt"] == notice
    assert required["html-runtime/fixed-browser/msedgewebview2.exe"] == browser
    assert required["html-runtime/fixed-browser/Locales/zh-CN.pak"] == notice
    assert not any(name.startswith("html-runtime/win-x64/") for name in required)


@pytest.mark.parametrize("changed_field", ["sha256", "bytes"])
def test_duplicate_required_path_cannot_change_digest_or_size(previous_and_current, changed_field):
    _, current, _, new = previous_and_current
    manifest = json.loads((current / "bridge-manifest.json").read_text(encoding="utf-8"))
    config = json.loads((current / "game/bridge-minigames.json").read_text(encoding="utf-8"))
    conflicting = deepcopy(config["minigames"]["compat_game"])
    conflicting["files"]["LICENSE.txt"][changed_field] = "0" * 64 if changed_field == "sha256" else 1
    config["retained_games"] = {new.digest: conflicting}
    with pytest.raises(BridgeError):
        pipeline._minigame_required_members(manifest, config)


def test_required_retained_path_cannot_escape_package_root(previous_and_current):
    _, current, old, _ = previous_and_current
    manifest = json.loads((current / "bridge-manifest.json").read_text(encoding="utf-8"))
    config = json.loads((current / "game/bridge-minigames.json").read_text(encoding="utf-8"))
    entry = installed(old)
    entry["package_root"] = "../../outside"
    config["retained_games"] = {old.digest: entry}
    with pytest.raises(BridgeError):
        pipeline._minigame_required_members(manifest, config)


def test_retained_members_do_not_claim_old_player_save_routes(previous_and_current):
    previous, current, old, _ = previous_and_current
    report = compatibility.prepare_previous_release(previous, current, "audit", "8.5.3")
    assert report["retained_minigames"][0]["package_digest"] == old.digest
    assert report["save_compatibility_verified"] is False


@pytest.mark.parametrize("removed", ["instance", "return_target", "entire_configuration"])
def test_removed_old_save_call_or_destination_rejects_before_package_or_config_writes(previous_and_current, removed):
    previous, current, old, _ = previous_and_current
    # Build a distinct authoritative fixture before its first baseline. The
    # already registered previous fixture remains untouched.
    fresh_previous = previous.parent / "previous-with-call"
    shutil.copytree(previous, fresh_previous, ignore=shutil.ignore_patterns(".bridge-derived-integrity"))
    previous = fresh_previous
    old_path, current_path = (root / "game/bridge-minigames.json" for root in (previous, current))
    old_config, new_config = (json.loads(path.read_text(encoding="utf-8")) for path in (old_path, current_path))
    instance = {"instance_id": "existing_call", "game_id": "compat_game", "frame_id": "entry",
        "result_branches": [{"branch_id": "win", "outcome": "win", "target_frame_id": "old_win"}],
        "cancel_target_frame_id": "old_cancel", "error_target_frame_id": "old_error", "fallback_target_frame_id": "old_fallback"}
    for value in (old_config, new_config):
        value["instances"] = {"existing_call": deepcopy(instance)}
        value["frame_labels"] = {key: "fixture_" + key for key in ("entry", "old_win", "old_cancel", "old_error", "old_fallback")}
    atomic_json(old_path, old_config)
    record_derived_sources(previous, managed_roots=["game/bridge_minigames", "game/bridge-minigames.json"])
    if removed == "instance":
        new_config["instances"] = {}
    elif removed == "return_target":
        new_config["frame_labels"].pop("old_error")
    atomic_json(current_path, new_config)
    if removed == "entire_configuration":
        current_path.unlink()
    original_current = current_path.read_bytes() if current_path.exists() else None
    previous_files = {path.relative_to(previous).as_posix(): file_hash(path) for path in previous.rglob("*") if path.is_file()}
    with pytest.raises(BridgeError) as error:
        compatibility.prepare_previous_release(previous, current, "audit", "8.5.3")
    assert error.value.code == "MINIGAME_SAVE_BREAKING_CHANGE"
    assert (current_path.read_bytes() if current_path.exists() else None) == original_current
    assert not (current / "game/bridge_minigames" / old.digest).exists()
    assert {path.relative_to(previous).as_posix(): file_hash(path) for path in previous.rglob("*") if path.is_file()} == previous_files

