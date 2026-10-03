"""Large-project capacity and batch report boundaries without running an SDK.

Report doubles exercise aggregation only; these are never native acceptance.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from renpy_bridge import minigame_qa as qa, pipeline
from renpy_bridge.errors import BridgeError
from renpy_bridge.minigame_packages import validate_directory
from renpy_bridge.models import Project
from renpy_bridge.storage import file_hash, read_json


@pytest.fixture
def project():
    info = validate_directory(Path(__file__).parents[1] / "examples/html_minigames/production/v1.0.1/timed")
    ids = ["entry", "win", "lose", "cancel", "error", "fallback"]
    frames = {key: {"frame_id": key, "act_id": "act", "flow": {"kind": "end"}} for key in ids}
    frames["entry"]["flow"] = {"kind": "minigame", "game_id": "prod_timed", "instance_id": "signal",
        "result_branches": [{"branch_id": outcome, "outcome": outcome, "target_frame_id": outcome}
                            for outcome in ("win", "lose")],
        "cancel_target_frame_id": "cancel", "error_target_frame_id": "error", "fallback_target_frame_id": "fallback"}
    return Project.model_validate({"schema_version": "1.1", "project_id": "large_project", "save_namespace": "large_project", "entry_frame_id": "entry",
        "acts": {"act": {"act_id": "act", "frame_ids": ids}}, "frames": frames,
        "minigames": {"prod_timed": {"game_id": "prod_timed", "package_digest": info.digest,
            "package_version": info.manifest["version"], "path": "minigames/" + info.digest, "manifest": info.manifest}}})


def scenario(outcome="win"):
    data = {"frame_id": "entry", "expected_outcome": outcome, "expected_target_frame_id": outcome, "actions": []}
    if outcome == "error":
        data["fault_plan"] = {"type": "terminate_own_host", "after": "running"}
    return data


def test_1024_is_a_job_capacity_separate_from_64_case_execution_batches(project):
    scenarios = [scenario(outcome) for outcome in ("win", "lose", "cancel", "error")]
    scenarios += [scenario()] * (1024 - len(scenarios))
    options = pipeline.validate_options("build", {"minigame_scenarios": scenarios})
    assert len(options["minigame_scenarios"]) == 1024
    assert qa.validate_release_coverage(project, scenarios)["combination_routes_verified"] is False


@pytest.mark.parametrize("count", [0, 1025])
def test_invalid_capacity_rejects_before_any_scenario_directory_is_created(project, tmp_path, count):
    scenarios = [scenario()] * count
    with pytest.raises(BridgeError):
        pipeline.validate_options("routes", {"minigame_scenarios": scenarios})
    with pytest.raises(BridgeError):
        qa.validate_release_coverage(project, scenarios)
    with pytest.raises(BridgeError):
        qa.prepare_scenario_suite(project, scenarios, tmp_path, tmp_path / "private")
    assert not (tmp_path / "private").exists()


def test_preparation_spans_multiple_batches_with_unique_stable_case_and_save_identity(project, tmp_path):
    requests = [scenario()] * 163
    source, prepared, coverage = qa.prepare_scenario_suite(project, requests, tmp_path, tmp_path / "private")
    assert coverage["batch_size"] == 64
    assert coverage["batch_count"] == 3
    assert len(prepared) == 163
    assert len({item["saves"] for item in prepared}) == 163
    assert [prepared[index]["case_name"] for index in (0, 63, 64, 127, 128, 162)] == [
        "minigame_actual_input_" + format(index, "04d") for index in (0, 63, 64, 127, 128, 162)]
    assert source.count("testcase minigame_actual_input_") == 163
    assert coverage["combination_routes_verified"] is False


@pytest.mark.parametrize("failure_index", [None, 64, 127])
def test_batches_hash_each_report_and_never_turn_incomplete_execution_into_pass(tmp_path, monkeypatch, failure_index):
    items = [{"saves": tmp_path / "save" / str(index), "options": scenario(), "case_name": str(index)} for index in range(163)]
    calls = []

    def report_double(sdk, target, saves, environment, prepared, evidence, *, executable=None):
        index = int(prepared["case_name"])
        calls.append((index, saves))
        return {"passed": index != failure_index, "scope": "aggregation_fixture_never_native_evidence"}

    monkeypatch.setattr(qa, "run_actual_qa", report_double)
    evidence = tmp_path / "evidence"
    result = qa.run_scenario_suite(None, tmp_path, {}, items, evidence)
    expected_executed = 163 if failure_index is None else failure_index + 1
    assert result["executed"] == expected_executed
    assert result["requested"] == 163 and result["requested_batches"] == 3
    assert result["passed"] is (failure_index is None)
    assert [index for index, _ in calls] == list(range(expected_executed))
    assert len({saves for _, saves in calls}) == expected_executed
    for batch in result["batches"]:
        assert batch["first_scenario_index"] == batch["batch_index"] * 64
        for record in batch["scenario_reports"]:
            assert file_hash(Path(record["path"])) == record["sha256"]
            stored = read_json(Path(record["path"]))
            assert stored["passed"] is record["passed"]
        actual = read_json(evidence / f"minigame-batch-{batch['batch_index']:04d}.json")
        assert actual == batch
    progress = read_json(evidence / "minigame-suite-progress.json")
    assert progress["passed"] is (failure_index is None)
    assert progress["executed"] == expected_executed
    assert progress["combination_routes_verified"] is False
    if failure_index is not None:
        assert result["batches"][-1]["passed"] is False
        assert not (evidence / f"minigame-scenario-{expected_executed:04d}").exists()
