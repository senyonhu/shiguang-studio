from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil

import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.minigame_packages import validate_directory
from renpy_bridge.minigame_protocol import serialize_message
from renpy_bridge.minigame_runtime import (
    QA_ENVIRONMENT, RPY_RUNTIME_SOURCE, RuntimeManager, evaluate_predicate,
    plan_finish, runtime_source_files, stable_label, verify_inventory,
)
from renpy_bridge.minigame_windows import geometry_from_renderer


class Process:
    status = None
    pid = 123
    def poll(self):
        return self.status


class Transport:
    """Unit seam only: native gameplay is covered by separate real SDK/CDP jobs."""
    def __init__(self, command, environment, profile):
        self.command, self.environment, self.profile = command, environment, profile
        self.messages = queue.Queue(128)
        self.failure = None
        self.process = Process()
        self.job = self
        self.sent = []
        self.flushed = 0
        self.closed = False
    def send(self, message):
        self.sent.append(message.to_dict())
    def flush(self):
        self.flushed += 1
    def close(self, *, graceful=False):
        self.closed = True
        self.graceful = graceful
        self.process.status = 0


def file_record(path):
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


@pytest.fixture
def fixture(tmp_path):
    package = validate_directory(Path(__file__).parents[1] / "examples/html_minigames/production/checkpoint")
    root = tmp_path / "project"
    destination = root / "game/bridge_minigames" / package.digest
    shutil.copytree(package.root, destination)
    host = root / "html-runtime/win-x64/HtmlMinigameEmbeddedHost.exe"
    host.parent.mkdir(parents=True)
    host.write_bytes(b"unit fixture; never executed")
    browser = root / "html-runtime/webview2-fixed/msedgewebview2.exe"
    browser.parent.mkdir(parents=True)
    browser.write_bytes(b"unit fixture; never executed")
    variables = {"reward": {"variable_id": "reward", "type": "int", "default": 0, "minimum": 0, "maximum": 100, "scope": "save"},
                 "temperature": {"variable_id": "temperature", "type": "int", "default": 37, "minimum": 20, "maximum": 50, "scope": "save"}}
    instance = {"kind": "minigame", "instance_id": "workshop", "game_id": "prod_checkpoint", "frame_id": "entry",
                "inputs": {"target_temperature": {"kind": "variable", "variable_id": "temperature"}}, "save_mode": "checkpoint",
                "cancel_target_frame_id": "cancel", "error_target_frame_id": "error", "fallback_target_frame_id": "fallback",
                "result_branches": [{"branch_id": "win", "outcome": "win", "predicate": {"op": "gte", "left": {"kind": "result", "field": "score"}, "right": {"kind": "literal", "value": 200}, "conditions": []},
                    "effects": [{"effect_id": "award", "op": "add", "variable_id": "reward", "value": {"kind": "literal", "value": 1}}], "target_frame_id": "won"}]}
    game = {"game_id": "prod_checkpoint", "package_digest": package.digest, "package_version": package.manifest["version"],
            "package_root": "game/bridge_minigames/" + package.digest, "manifest": package.manifest}
    configuration = {"schema_version": "1.0", "project_id": "unit", "revision": 1, "save_namespace": "unit_save",
        "host": {"window_mode": "embedded_same_top_level_window", "footer_fraction": 0.1, "relative_executable": "html-runtime/win-x64/HtmlMinigameEmbeddedHost.exe", "sha256": file_record(host)["sha256"], "files": {host.name: file_record(host)}},
        "fixed_runtime": {"relative_root": "html-runtime/webview2-fixed", "version": "fixture", "files": {browser.name: file_record(browser)}},
        "variables": variables, "minigames": {game["game_id"]: game}, "instances": {"workshop": instance},
        "frame_labels": {frame: stable_label(frame) for frame in ("entry", "won", "cancel", "error", "fallback")}}
    runtime = RuntimeManager(root, tmp_path / "saves", configuration, transport_factory=Transport, parent_window_provider=lambda: {"hwnd": 42, "root_hwnd": 42, "process_id": os.getpid(), "geometry": {"parent_client": [800, 600], "viewport": [0, 75, 800, 405], "footer": [0, 480, 800, 45]}})
    values = {"reward": 0, "temperature": 37}
    return runtime, configuration, values


def begin(fixture):
    runtime, configuration, values = fixture
    state = runtime.prepare("workshop", None, values)
    state = runtime.entry_replacement(state)
    state = runtime.ensure_started(state)
    emit(runtime, state, "ready", {"game_id": state["game_id"], "package_version": state["package_version"], "capabilities": {"pause": True, "checkpoint": True}}, "ready")
    assert runtime.poll(state) == "waiting"
    return runtime, configuration, values, state


def emit(runtime, state, kind, payload, event_id):
    message = {"protocol": "1.0", "session_id": state["session_id"], "event_id": event_id, "type": kind, "payload": payload}
    runtime.active["transport"].messages.put(serialize_message(message).encode("utf-8"))


def result(score=250):
    return {"outcome": "win", "results": {"score": score, "steps_completed": 3, "mistakes": 0, "calibrated_temperature": 37}}


def snapshot():
    return {"checkpoint_id": "sensor", "snapshot_version": "1.0", "state": {"stage": 1, "sensor": "temperature", "wire": None, "calibration": 0, "mistakes": 0}}


def failure(code, callback):
    with pytest.raises(BridgeError) as error:
        callback()
    assert error.value.code == code


def test_entry_detaches_variables_and_freezes_input_before_native_launch(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    values["temperature"] = 40
    assert state["inputs"] == {"target_temperature": 37}
    assert runtime.active is None and state["last_commit"] is None
    saved = runtime.entry_replacement(state)
    assert saved["last_commit"]["kind"] == "entry"
    assert state["last_commit"] is None
    assert "event_id" not in saved["last_commit"]


def test_finish_validation_never_awards_until_whole_replacement(fixture):
    runtime, _, values, state = begin(fixture)
    emit(runtime, state, "finish", result(), "victory")
    assert runtime.poll(state) == "finish"
    transport = runtime.active["transport"]
    assert values["reward"] == 0 and state["completion"] is None
    assert not any(item["type"] == "ack" for item in transport.sent)
    replacement_values, replacement = runtime.finish_replacement(state, values)
    assert replacement_values["reward"] == 1 and values["reward"] == 0
    assert replacement["completion"]["target_frame_id"] == "won"
    assert not any(item["type"] == "ack" for item in transport.sent)
    failure("MINIGAME_PERSISTENCE_UNCONFIRMED", lambda: runtime.acknowledge(replacement, persisted_receipt={}))
    runtime.acknowledge(replacement, persisted_receipt=replacement["last_commit"])
    assert transport.sent[-1]["payload"]["persisted"] is True and transport.flushed == 1
    assert runtime.finish_replacement(replacement, replacement_values)[0]["reward"] == 1


def test_duplicate_finish_and_replay_do_not_apply_second_reward(fixture):
    runtime, _, values, state = begin(fixture)
    emit(runtime, state, "finish", result(), "victory")
    assert runtime.poll(state) == "finish"
    emit(runtime, state, "finish", result(), "victory")
    assert runtime.poll(state) == "finish"
    values, completed = runtime.finish_replacement(state, values)
    runtime.acknowledge(completed, persisted_receipt=completed["last_commit"])
    first_ack = runtime.active["transport"].sent[-1]
    emit(runtime, state, "finish", result(), "victory")
    assert runtime.poll(state) == "waiting"
    assert runtime.active["transport"].sent[-1] == first_ack
    assert runtime.finish_replacement(completed, values)[0]["reward"] == 1


def test_reused_result_id_with_changed_payload_routes_error_without_award(fixture):
    runtime, _, values, state = begin(fixture)
    emit(runtime, state, "finish", result(), "victory")
    assert runtime.poll(state) == "finish"
    emit(runtime, state, "finish", result(270), "victory")
    assert runtime.poll(state) == "error"
    assert runtime.last_error["code"] == "EVENT_ID_REUSED" and values["reward"] == 0


def test_effect_overflow_rejects_entire_candidate(fixture):
    runtime, configuration, values = fixture
    original = copy.deepcopy(values)
    effects = configuration["instances"]["workshop"]["result_branches"][0]["effects"]
    effects.append({"effect_id": "overflow", "op": "add", "variable_id": "temperature", "value": {"kind": "literal", "value": 100}})
    with pytest.raises(BridgeError):
        plan_finish(configuration, "workshop", values, result())
    assert values == original


def test_ordered_predicate_and_fallback_apply_only_selected_branch(fixture):
    _, configuration, values = fixture
    configuration["instances"]["workshop"]["result_branches"].append({"branch_id": "also_win", "outcome": "win", "effects": [{"effect_id": "huge", "op": "set", "variable_id": "reward", "value": {"kind": "literal", "value": 50}}], "target_frame_id": "fallback"})
    candidate, completed = plan_finish(configuration, "workshop", values, result())
    assert candidate["reward"] == 1 and completed["branch_id"] == "win"
    configuration["instances"]["workshop"]["result_branches"].pop()
    candidate, completed = plan_finish(configuration, "workshop", values, result(150))
    assert candidate == values and completed["target_frame_id"] == "fallback"


def test_boolean_is_not_numeric_predicate_or_addition():
    assert evaluate_predicate({"op": "eq", "left": {"kind": "literal", "value": True}, "right": {"kind": "literal", "value": 1}}, {}, {}) is False
    failure("MINIGAME_PREDICATE_INVALID", lambda: evaluate_predicate({"op": "gt", "left": {"kind": "literal", "value": True}, "right": {"kind": "literal", "value": 0}}, {}, {}))


def test_checkpoint_persists_only_snapshot_and_ack_after_save_receipt(fixture):
    runtime, _, values, state = begin(fixture)
    emit(runtime, state, "checkpoint", snapshot(), "cp1")
    assert runtime.poll(state) == "checkpoint"
    candidate = runtime.checkpoint_replacement(state)
    assert state["snapshot"] is None and candidate["snapshot"]["state"]["stage"] == 1
    assert values["reward"] == 0
    waiting = runtime.acknowledge(candidate, persisted_receipt=candidate["last_commit"])
    assert waiting["phase"] == "waiting" and candidate["phase"] == "checkpoint"


def test_load_fences_stale_ack_and_reopens_new_session_with_snapshot(fixture):
    runtime, _, _, state = begin(fixture)
    emit(runtime, state, "checkpoint", snapshot(), "cp1")
    assert runtime.poll(state) == "checkpoint"
    candidate = runtime.checkpoint_replacement(state)
    old_transport = runtime.active["transport"]
    loaded = runtime.after_load(candidate)
    assert old_transport.closed and runtime.active is None
    assert loaded["attempt_id"] != state["attempt_id"] and loaded["session_id"] != state["session_id"]
    assert loaded["logical_entry_id"] == state["logical_entry_id"] and loaded["snapshot"] == candidate["snapshot"]
    runtime.acknowledge(loaded, persisted_receipt=loaded["last_commit"])
    assert not any(item["type"] == "ack" for item in old_transport.sent)
    assert runtime.poll(loaded) == "restart" and runtime.active is None
    reopened = runtime.ensure_started(runtime.entry_replacement(loaded))
    emit(runtime, reopened, "ready", {"game_id": reopened["game_id"], "package_version": reopened["package_version"], "capabilities": {"pause": True, "checkpoint": True}}, "newready")
    assert runtime.poll(reopened) == "waiting"
    assert runtime.active["transport"].sent[0]["payload"]["snapshot"] == candidate["snapshot"]


def test_entry_restart_load_discards_snapshot_but_freezes_inputs(fixture):
    runtime, _, values = fixture
    runtime.configuration["instances"]["workshop"]["save_mode"] = "entry_restart"
    state = runtime.prepare("workshop", None, values)
    state["snapshot"] = snapshot()
    loaded = runtime.after_load(state)
    assert loaded["snapshot"] is None and loaded["inputs"] == state["inputs"]


def test_completed_load_keeps_award_and_target_without_new_host(fixture):
    runtime, _, values, state = begin(fixture)
    emit(runtime, state, "finish", result(), "victory")
    runtime.poll(state)
    values, completed = runtime.finish_replacement(state, values)
    loaded = runtime.after_load(completed)
    assert loaded == completed and runtime.active is None
    assert runtime.target(loaded) == stable_label("won") and values["reward"] == 1
    assert runtime.ensure_started(loaded) == loaded and runtime.active is None


def test_cancel_then_explicit_retry_creates_new_logical_entry(fixture):
    runtime, _, values, state = begin(fixture)
    transport = runtime.active["transport"]
    runtime.request_cancel()
    assert runtime.poll(state) == "cancel"
    cancelled = runtime.terminal_replacement(state, "cancel")
    assert transport.closed and cancelled["completion"]["outcome"] == "cancel"
    assert runtime.target(cancelled) == stable_label("cancel")
    again = runtime.prepare("workshop", cancelled, values)
    assert again["logical_entry_id"] != state["logical_entry_id"] and again["completion"] is None


def test_reader_failure_and_process_exit_never_create_victory(fixture):
    runtime, _, values, state = begin(fixture)
    runtime.active["transport"].process.status = 0
    assert runtime.poll(state) == "error"
    failed = runtime.terminal_replacement(state, "error")
    assert failed["completion"]["outcome"] == "error" and values["reward"] == 0
    assert failed["target_frame_id"] == "error"


def test_shutdown_latch_prevents_timer_reopening(fixture):
    runtime, _, _, state = begin(fixture)
    runtime.shutdown()
    assert runtime.poll(state) == "cancel" and runtime.active is None
    assert runtime.ensure_started(state) == state and runtime.active is None
    failure("MINIGAME_RUNTIME_CLOSED", lambda: runtime.prepare("workshop", None, {"reward": 0, "temperature": 37}))


def test_thread_cannot_commit_or_poll_story_state(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    with ThreadPoolExecutor(max_workers=1) as pool:
        failure("MINIGAME_MAIN_THREAD_REQUIRED", lambda: pool.submit(runtime.entry_replacement, state).result())


def test_package_digest_mismatch_does_not_silently_restart_new_version(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    state["package_digest"] = "e" * 64
    failure("MINIGAME_SAVE_PACKAGE_CHANGED", lambda: runtime.after_load(state))
    assert runtime.active is None


def test_retained_package_uses_saved_flow_not_updated_rewards(fixture):
    runtime, _, values, state = begin(fixture)
    original_game = copy.deepcopy(runtime.configuration["minigames"]["prod_checkpoint"])
    runtime.configuration["retained_games"] = {original_game["package_digest"]: original_game}
    runtime.configuration["minigames"]["prod_checkpoint"]["package_digest"] = "f" * 64
    runtime.configuration["instances"]["workshop"]["result_branches"][0]["effects"][0]["value"]["value"] = 99
    loaded = runtime.after_load(state)
    assert loaded["entry_definition"]["result_branches"][0]["effects"][0]["value"]["value"] == 1
    reopened = runtime.ensure_started(runtime.entry_replacement(loaded))
    emit(runtime, reopened, "ready", {"game_id": reopened["game_id"], "package_version": reopened["package_version"], "capabilities": {"pause": True, "checkpoint": True}}, "ready2")
    runtime.poll(reopened)
    emit(runtime, reopened, "finish", result(), "victory2")
    assert runtime.poll(reopened) == "finish"
    candidate, _ = runtime.finish_replacement(reopened, values)
    assert candidate["reward"] == 1


def test_changed_variables_and_removed_saved_target_need_explicit_migration(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    runtime.configuration["variables"]["reward"]["maximum"] = 200
    failure("MINIGAME_SAVE_VARIABLES_CHANGED", lambda: runtime.after_load(state))
    runtime.configuration["variables"]["reward"]["maximum"] = 100
    del runtime.configuration["frame_labels"]["won"]
    failure("MINIGAME_SAVE_TARGET_REMOVED", lambda: runtime.after_load(state))


def test_removed_call_or_error_target_recovers_to_stable_incompatible_state(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    state["snapshot"] = snapshot()
    del runtime.configuration["instances"]["workshop"]
    with pytest.raises(BridgeError) as error:
        runtime.after_load(state)
    recovered = runtime.recover_loaded_failure(state, error.value)
    assert recovered["phase"] == "incompatible" and recovered["snapshot"] == state["snapshot"]
    assert recovered["completion"] is None and values["reward"] == 0
    assert runtime.active is None


def test_missing_error_frame_does_not_throw_inside_load_recovery(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    del runtime.configuration["frame_labels"]["error"]
    recovered = runtime.recover_loaded_failure(state, BridgeError("MINIGAME_SAVE_TARGET_REMOVED", "旧出口已移除"))
    assert recovered["phase"] == "incompatible"


def test_runtime_inventory_catches_extra_missing_and_modified_file(fixture):
    runtime, configuration, _ = fixture
    root = runtime.basedir / "html-runtime/win-x64"
    expected = configuration["host"]["files"]
    assert verify_inventory(root, expected, "HtmlMinigameEmbeddedHost.exe") == root
    extra = root / "extra.dll"
    extra.write_bytes(b"extra")
    failure("MINIGAME_RUNTIME_CHANGED", lambda: verify_inventory(root, expected, "HtmlMinigameEmbeddedHost.exe"))
    extra.unlink()
    (root / "HtmlMinigameEmbeddedHost.exe").write_bytes(b"modified")
    failure("MINIGAME_RUNTIME_CHANGED", lambda: verify_inventory(root, expected, "HtmlMinigameEmbeddedHost.exe"))


def test_launch_removes_inherited_browser_overrides_and_prod_has_no_devtools(fixture, monkeypatch):
    monkeypatch.setenv("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--remote-debugging-port=9999")
    runtime, _, values = fixture
    state = runtime.ensure_started(runtime.entry_replacement(runtime.prepare("workshop", None, values)))
    transport = runtime.active["transport"]
    assert "--development" not in transport.command and "--devtools-port" not in transport.command
    assert "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS" not in transport.environment
    assert transport.environment["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"].endswith("webview2-fixed")
    assert state["session_id"] in transport.command
    assert "--parent-window-handle" in transport.command and "--parent-process-id" in transport.command
    assert "--footer-fraction" in transport.command
    assert "hwnd" not in state and "parent" not in state


def test_pause_menu_and_resume_preserve_one_attempt_and_pause_protocol(fixture):
    runtime, _, _, state = begin(fixture)
    assert runtime.can_pause(state)
    paused = runtime.pause_for_menu(state)
    assert paused["phase"] == "paused" and state["phase"] == "waiting"
    assert runtime.active["transport"].sent[-1]["type"] == "pause"
    resumed = runtime.resume_after_menu(paused)
    assert resumed["phase"] == "waiting" and resumed["session_id"] == state["session_id"]
    assert runtime.active["transport"].sent[-1]["type"] == "resume"


def test_load_from_paused_menu_reopens_snapshot_not_old_transport(fixture):
    runtime, _, _, state = begin(fixture)
    paused = runtime.pause_for_menu(state)
    loaded = runtime.after_load(paused)
    assert loaded["phase"] == "entry" and runtime.active is None
    assert runtime.pause_for_menu(loaded) == loaded
    assert runtime.active is None
    assert runtime.resume_after_menu(loaded) == loaded


def test_loaded_menu_continue_uses_new_attempt_and_can_return_without_award(fixture):
    runtime, _, values, state = begin(fixture)
    old_transport = runtime.active["transport"]
    loaded = runtime.after_load(runtime.pause_for_menu(state))
    assert old_transport.closed
    menu = runtime.pause_for_menu(loaded)
    assert menu["logical_entry_id"] == state["logical_entry_id"]
    assert menu["session_id"] != state["session_id"] and menu["attempt_id"] != state["attempt_id"]
    continued = runtime.ensure_started(runtime.entry_replacement(runtime.resume_after_menu(menu)))
    assert continued["phase"] == "waiting"
    assert runtime.active["transport"] is not old_transport
    cancelled = runtime.terminal_replacement(continued, "cancel")
    assert cancelled["target_frame_id"] == "cancel" and values["reward"] == 0
    failure("MINIGAME_PAUSE_UNAVAILABLE", lambda: runtime.pause_for_menu(loaded))


@pytest.mark.parametrize("field,value", [("attempt_id", "f" * 32), ("session_id", "f" * 32),
                                      ("logical_entry_id", "f" * 32), ("load_epoch", 0), ("phase", "paused")])
def test_old_or_modified_menu_identity_cannot_reopen_without_host(fixture, field, value):
    runtime, _, _, state = begin(fixture)
    loaded = runtime.after_load(runtime.pause_for_menu(state))
    changed = {**loaded, field: value}
    failure("MINIGAME_PAUSE_UNAVAILABLE", lambda: runtime.pause_for_menu(changed))
    assert runtime.active is None


def test_menu_continuation_still_validates_frozen_loaded_flow(fixture):
    runtime, _, _, state = begin(fixture)
    loaded = runtime.after_load(runtime.pause_for_menu(state))
    loaded["entry_definition_digest"] = "f" * 64
    failure("MINIGAME_SAVE_FLOW_CHANGED", lambda: runtime.pause_for_menu(loaded))


def test_fresh_unstarted_entry_is_not_a_restored_menu(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    failure("MINIGAME_PAUSE_UNAVAILABLE", lambda: runtime.pause_for_menu(state))


@pytest.mark.parametrize("change,phase", [("variable", "error"), ("instance", "incompatible"), ("target", "incompatible")])
def test_loaded_invalid_menu_continuation_returns_only_trusted_recovery(fixture, change, phase):
    runtime, _, values, state = begin(fixture)
    paused = runtime.pause_for_menu(state)
    if change == "variable":
        runtime.configuration["variables"]["reward"]["maximum"] = 200
    elif change == "instance":
        del runtime.configuration["instances"]["workshop"]
    else:
        del runtime.configuration["frame_labels"]["error"]
    with pytest.raises(BridgeError) as error:
        runtime.after_load(paused)
    recovered = runtime.recover_loaded_failure(paused, error.value)
    assert recovered["phase"] == phase and runtime.active is None
    assert runtime.pause_for_menu(recovered) == recovered
    assert values["reward"] == 0
    altered = {**recovered, "session_id": "f" * 32}
    failure("MINIGAME_PAUSE_UNAVAILABLE", lambda: runtime.pause_for_menu(altered))
    runtime.close_active("story_return")
    failure("MINIGAME_PAUSE_UNAVAILABLE", lambda: runtime.pause_for_menu(recovered))


def test_terminal_state_without_load_recovery_cannot_bypass_pause_guard(fixture):
    runtime, _, values = fixture
    state = runtime.terminal_replacement(runtime.prepare("workshop", None, values), "error")
    failure("MINIGAME_PAUSE_UNAVAILABLE", lambda: runtime.pause_for_menu(state))


def test_menu_checks_loaded_terminal_before_native_screen():
    menu = RPY_RUNTIME_SOURCE.split("label rb_minigame_menu_phase:\n", 1)[1].split("label rb_minigame_checkpoint:", 1)[0]
    replay = menu.split(".pause_for_menu(rb_minigame_state)\n", 1)[1].split("call screen rb_minigame_menu", 1)[0]
    assert "jump rb_minigame_incompatible_phase" in replay
    assert "jump rb_minigame_done" in replay


def test_unconfigured_load_failure_jumps_before_saved_manager_continuation():
    source = RPY_RUNTIME_SOURCE.split("    def rb_minigame_after_load():\n", 1)[1].split("    def rb_minigame_poll():", 1)[0]
    assert 'if _rb_mg._MANAGER is None and renpy.store.rb_minigame_state.get("phase") == "incompatible":' in source
    assert '                renpy.jump("rb_minigame_incompatible_phase")' in source
    # Control flow is outside the business exception handler, so JumpException
    # remains Ren'Py's label transition rather than a swallowed load failure.
    assert source.index('            if _rb_mg._MANAGER') > source.index('            except Exception as error:')


def test_development_fault_requires_qa_and_live_current_running_identity(fixture):
    runtime, _, values, state = begin(fixture)
    assert runtime.development_host_running(state) is False
    failure("MINIGAME_QA_FAULT_FORBIDDEN", lambda: runtime.development_terminate_own_host(state))
    runtime.qa = {"event_log_path": str(runtime.savedir / "events.jsonl")}
    assert runtime.development_host_running(state)
    stale = {**state, "session_id": "f" * 32}
    failure("MINIGAME_QA_FAULT_FORBIDDEN", lambda: runtime.development_terminate_own_host(stale))
    receipt = runtime.development_terminate_own_host(state)
    assert receipt["pid"] == 123 and receipt["session_id"] == state["session_id"]
    assert runtime.poll(state) == "error" and values["reward"] == 0


def test_development_fault_is_forbidden_during_pending_reward_commit(fixture):
    runtime, _, _, state = begin(fixture)
    runtime.qa = {"event_log_path": str(runtime.savedir / "events.jsonl")}
    emit(runtime, state, "finish", result(), "victory")
    assert runtime.poll(state) == "finish"
    assert runtime.development_host_running(state) is False
    failure("MINIGAME_QA_FAULT_FORBIDDEN", lambda: runtime.development_terminate_own_host(state))


@pytest.mark.parametrize("client,box,physical,expected", [
    ([0, 0, 1280, 720], (0, 0, 1280, 720), (1280, 720), {"parent_client": [1280, 720], "viewport": [0, 0, 1280, 648], "footer": [0, 648, 1280, 72]}),
    ([0, 0, 1024, 900], (0, 162, 1024, 576), (1024, 900), {"parent_client": [1024, 900], "viewport": [0, 162, 1024, 518], "footer": [0, 680, 1024, 58]}),
    ([0, 0, 800, 600], (0, 75, 800, 450), (800, 600), {"parent_client": [800, 600], "viewport": [0, 75, 800, 405], "footer": [0, 480, 800, 45]}),
    ([0, 0, 1920, 1080], (0, 0, 1920, 1080), (1920, 1080), {"parent_client": [1920, 1080], "viewport": [0, 0, 1920, 972], "footer": [0, 972, 1920, 108]}),
    ([0, 0, 2560, 1080], (320, 0, 1920, 1080), (2560, 1080), {"parent_client": [2560, 1080], "viewport": [320, 0, 1920, 972], "footer": [320, 972, 1920, 108]}),
])
def test_renderer_geometry_preserves_real_letterbox_footer(client, box, physical, expected):
    assert geometry_from_renderer(client, box, physical, (1920, 1080)) == expected


def test_geometry_accounts_for_native_coordinate_scale_and_rejects_nonfinite():
    result_geometry = geometry_from_renderer([0, 0, 1600, 1200], (0, 75, 800, 450), (800, 600), (1920, 1080))
    assert result_geometry == {"parent_client": [1600, 1200], "viewport": [0, 150, 1600, 810], "footer": [0, 960, 1600, 90]}
    with pytest.raises(ValueError):
        geometry_from_renderer([0, 0, 800, 600], (0, float("nan"), 800, 450), (800, 600), (1920, 1080))


def test_main_thread_geometry_updates_emit_native_control_without_story_effects(fixture):
    runtime, _, values, state = begin(fixture)
    runtime.parent_window_provider = lambda: {"hwnd": 42, "root_hwnd": 42, "process_id": os.getpid(),
        "geometry": {"parent_client": [1024, 900], "viewport": [0, 162, 1024, 518], "footer": [0, 680, 1024, 58]}}
    assert runtime.poll(state) == "waiting"
    message = runtime.active["transport"].sent[-1]
    assert message["type"] == "layout" and message["payload"]["viewport"][1] == 162
    assert values["reward"] == 0 and "hwnd" not in state


def unpresentable_parent(reason="zero_client"):
    return {"hwnd": 42, "root_hwnd": 42, "process_id": os.getpid(), "client_rect": [0, 0, 0, 0],
            "presentable": False, "presentation_reason": reason}


def test_zero_client_preserves_host_identity_and_pauses_once_then_resumes_after_real_layout(fixture):
    runtime, _, values, state = begin(fixture)
    original_state, original_values = copy.deepcopy(state), copy.deepcopy(values)
    active, old_parent = runtime.active, copy.deepcopy(runtime.active["parent"])
    runtime.parent_window_provider = unpresentable_parent
    assert runtime.poll(state) == "waiting"
    assert active["presentation_suspended"] and active["presentation_pause_owned"]
    assert active["session"].phase.value == "paused" and active["transport"].sent[-1]["type"] == "pause"
    messages = len(active["transport"].sent)
    assert runtime.poll(state) == "waiting" and len(active["transport"].sent) == messages
    assert active["transport"].closed is False
    assert state == original_state and values == original_values
    runtime.parent_window_provider = lambda: old_parent
    assert runtime.poll(state) == "waiting"
    assert [item["type"] for item in active["transport"].sent[-3:]] == ["layout", "settings", "resume"]
    assert active is runtime.active and active["session"].session_id == state["session_id"]
    assert not active["presentation_suspended"] and not active["presentation_pause_owned"]
    assert active["session"].phase.value == "running" and state == original_state and values == original_values


def test_unpresentable_entry_defers_spawn_without_new_attempt_or_fake_geometry(fixture):
    runtime, _, values = fixture
    valid = runtime.parent_window_provider
    runtime.parent_window_provider = unpresentable_parent
    state = runtime.entry_replacement(runtime.prepare("workshop", None, values))
    waiting = runtime.ensure_started(state)
    assert waiting == state and runtime.active is None
    assert runtime.poll(state) == "waiting"
    assert "geometry" not in runtime._deferred_parent["parent"]
    runtime.parent_window_provider = valid
    assert runtime.poll(state) == "restart"
    opened = runtime.ensure_started(state)
    assert opened["attempt_id"] == state["attempt_id"] and opened["session_id"] == state["session_id"]
    assert opened["logical_entry_id"] == state["logical_entry_id"] and opened["last_commit"] == state["last_commit"]
    assert runtime.active is not None and runtime._deferred_parent is None and values["reward"] == 0


def test_initial_zero_client_still_rejects_parent_replacement_and_shutdown(fixture):
    runtime, _, values = fixture
    runtime.parent_window_provider = unpresentable_parent
    state = runtime.ensure_started(runtime.prepare("workshop", None, values))
    runtime.parent_window_provider = lambda: {**unpresentable_parent(), "hwnd": 43, "root_hwnd": 43}
    assert runtime.poll(state) == "error" and runtime.last_error["code"] == "MINIGAME_PARENT_WINDOW_CHANGED"
    runtime.shutdown()
    assert runtime._deferred_parent is None and runtime.poll(state) == "cancel" and runtime.active is None


@pytest.mark.parametrize("changes", [{"hwnd": 43, "root_hwnd": 43}, {"root_hwnd": 43}, {"process_id": 999999}])
def test_suspended_window_does_not_excuse_wrong_hwnd_pid_or_root(fixture, changes):
    runtime, _, _, state = begin(fixture)
    runtime.parent_window_provider = lambda: {**unpresentable_parent(), **changes}
    assert runtime.poll(state) == "error" and runtime.active["transport"].sent[-1]["type"] != "pause"


def test_suspended_observation_cannot_supply_stale_geometry(fixture):
    runtime, _, _, state = begin(fixture)
    runtime.parent_window_provider = lambda: {**unpresentable_parent(), "geometry": runtime.active["parent"]["geometry"]}
    assert runtime.poll(state) == "error" and runtime.last_error["code"] == "MINIGAME_VIEWPORT_INVALID"


def test_window_restore_never_resumes_native_menu_pause(fixture):
    runtime, _, _, state = begin(fixture)
    parent = copy.deepcopy(runtime.active["parent"])
    paused = runtime.pause_for_menu(state)
    runtime.parent_window_provider = unpresentable_parent
    assert runtime.poll(paused) == "waiting"
    assert runtime.active["presentation_pause_owned"] is False
    runtime.parent_window_provider = lambda: parent
    assert runtime.poll(paused) == "waiting"
    assert runtime.active["session"].phase.value == "paused"
    assert runtime.active["transport"].sent[-1]["type"] == "layout"
    resumed = runtime.resume_after_menu(paused)
    assert resumed["phase"] == "waiting" and runtime.active["session"].phase.value == "running"


def test_menu_continue_while_unpresentable_transfers_pause_until_real_restore(fixture):
    runtime, _, _, state = begin(fixture)
    parent = copy.deepcopy(runtime.active["parent"])
    paused = runtime.pause_for_menu(state)
    runtime.parent_window_provider = unpresentable_parent
    resumed = runtime.resume_after_menu(paused)
    assert resumed["phase"] == "waiting" and runtime.active["session"].phase.value == "paused"
    assert runtime.active["presentation_pause_owned"] is True
    runtime.parent_window_provider = lambda: parent
    assert runtime.poll(resumed) == "waiting" and runtime.active["session"].phase.value == "running"


def test_window_pause_can_transfer_to_menu_without_duplicate_protocol_pause(fixture):
    runtime, _, _, state = begin(fixture)
    runtime.parent_window_provider = unpresentable_parent
    assert runtime.poll(state) == "waiting"
    assert runtime.can_pause(state)
    count = len(runtime.active["transport"].sent)
    paused = runtime.pause_for_menu(state)
    assert paused["phase"] == "paused" and len(runtime.active["transport"].sent) == count
    assert runtime.active["presentation_pause_owned"] is False


def test_window_restore_keeps_checkpoint_pending_until_its_original_commit(fixture):
    runtime, _, values, state = begin(fixture)
    parent = copy.deepcopy(runtime.active["parent"])
    runtime.parent_window_provider = unpresentable_parent
    assert runtime.poll(state) == "waiting"
    emit(runtime, state, "checkpoint", snapshot(), "paused-checkpoint")
    assert runtime.poll(state) == "checkpoint"
    runtime.parent_window_provider = lambda: parent
    runtime.poll(state)
    assert runtime.active["session"].phase.value == "checkpoint_pending"
    assert runtime.active["transport"].sent[-1]["type"] == "layout"
    saved = runtime.checkpoint_replacement(state)
    confirmed = runtime.acknowledge(saved, persisted_receipt=saved["last_commit"])
    assert runtime.active["session"].phase.value == "paused"
    assert runtime.poll(confirmed) == "waiting" and runtime.active["session"].phase.value == "running"
    assert confirmed["snapshot"] == snapshot() and values["reward"] == 0


def test_loading_clock_excludes_unpresentable_duration_but_retains_real_timeout_and_exit(fixture):
    runtime, _, values = fixture
    now = [0.0]
    runtime.clock = lambda: now[0]
    parent = runtime.parent_window_provider
    state = runtime.ensure_started(runtime.prepare("workshop", None, values))
    now[0] = 5.0
    runtime.parent_window_provider = unpresentable_parent
    assert runtime.poll(state) == "waiting"
    now[0] = 100.0
    assert runtime.poll(state) == "waiting"
    runtime.parent_window_provider = parent
    assert runtime.poll(state) == "waiting"
    now[0] = 124.0
    assert runtime.poll(state) == "waiting"
    now[0] = 126.0
    assert runtime.poll(state) == "error" and runtime.last_error["code"] == "MINIGAME_LOAD_TIMEOUT"


def test_host_exit_while_window_suspended_is_still_an_error(fixture):
    runtime, _, values, state = begin(fixture)
    runtime.parent_window_provider = unpresentable_parent
    assert runtime.poll(state) == "waiting"
    runtime.active["transport"].process.status = 1
    assert runtime.poll(state) == "error" and runtime.last_error["code"] == "MINIGAME_HOST_EXITED"
    assert values["reward"] == 0


def test_ready_received_while_minimized_starts_then_immediately_pauses_same_session(fixture):
    runtime, configuration, values = fixture
    state = runtime.ensure_started(runtime.prepare("workshop", None, values))
    runtime.parent_window_provider = unpresentable_parent
    game = configuration["minigames"][state["game_id"]]
    emit(runtime, state, "ready", {"game_id": game["game_id"], "package_version": game["package_version"],
         "capabilities": {"pause": True, "checkpoint": True}}, "ready-minimized")
    assert runtime.poll(state) == "waiting"
    assert [item["type"] for item in runtime.active["transport"].sent] == ["init", "start", "pause"]
    assert runtime.active["session"].phase.value == "paused" and runtime.active["presentation_pause_owned"]


def test_audio_settings_are_captured_at_entry_and_updated_before_resume(fixture):
    runtime, _, values = fixture
    runtime.settings_provider = lambda: {"audio_volume": 0.25}
    runtime, _, _, state = begin(fixture)
    assert runtime.active["transport"].sent[0]["payload"]["settings"] == {"audio_volume": 0.25}
    paused = runtime.pause_for_menu(state)
    runtime.settings_provider = lambda: {"audio_volume": 0.0}
    runtime.resume_after_menu(paused)
    assert runtime.active["transport"].sent[-2]["type"] == "settings"
    assert runtime.active["transport"].sent[-2]["payload"] == {"audio_volume": 0.0}
    assert runtime.active["transport"].sent[-1]["type"] == "resume"


def test_qa_env_is_ignored_in_production_and_constrained_in_developer(fixture, monkeypatch):
    runtime, configuration, _ = fixture
    qa_path = runtime.savedir / "qa.json"
    qa_path.parent.mkdir(parents=True)
    qa_path.write_text(json.dumps({"development": True, "devtools_port": 9340, "event_log_path": str(runtime.savedir / "events.jsonl")}))
    monkeypatch.setenv(QA_ENVIRONMENT, str(qa_path))
    production = RuntimeManager(runtime.basedir, runtime.savedir, configuration, transport_factory=Transport, parent_window_provider=lambda: {"hwnd": 42, "root_hwnd": 42, "process_id": os.getpid(), "geometry": {"parent_client": [800, 600], "viewport": [0, 75, 800, 405], "footer": [0, 480, 800, 45]}})
    assert production.qa is None
    development = RuntimeManager(runtime.basedir, runtime.savedir, configuration, allow_qa=True, transport_factory=Transport, parent_window_provider=lambda: {"hwnd": 42, "root_hwnd": 42, "process_id": os.getpid(), "geometry": {"parent_client": [800, 600], "viewport": [0, 75, 800, 405], "footer": [0, 480, 800, 45]}})
    assert development.qa["devtools_port"] == 9340
    qa_path.write_text(json.dumps({"development": True, "devtools_port": 9340, "event_log_path": str(runtime.basedir / "outside.log")}))
    failure("MINIGAME_QA_INVALID", lambda: RuntimeManager(runtime.basedir, runtime.savedir, configuration, allow_qa=True))


def test_saved_identity_cannot_escape_profile_directory(fixture):
    runtime, _, values = fixture
    state = runtime.prepare("workshop", None, values)
    state["attempt_id"] = "../../unrelated"
    failure("MINIGAME_STATE_INVALID", lambda: runtime.ensure_started(state))


def test_generated_package_is_stdlib_complete_and_labels_are_stable():
    files = runtime_source_files()
    assert set(Path(path).name for path in files) == {"__init__.py", "errors.py", "minigame_packages.py", "minigame_protocol.py", "minigame_runtime.py", "minigame_windows.py"}
    for source in files.values():
        compile(source, "vendored.py", "exec")
    assert "default rb_story_variables" not in RPY_RUNTIME_SOURCE
    assert "_rb_mg_sys.dont_write_bytecode = True" in RPY_RUNTIME_SOURCE
    for label in ("rb_minigame_run", "rb_minigame_entry_save", "rb_minigame_wait", "rb_minigame_checkpoint", "rb_minigame_commit", "rb_minigame_done"):
        assert "label " + label in RPY_RUNTIME_SOURCE
    assert 'screen rb_minigame_waiting()' in RPY_RUNTIME_SOURCE and 'textbutton "返回剧情"' in RPY_RUNTIME_SOURCE
