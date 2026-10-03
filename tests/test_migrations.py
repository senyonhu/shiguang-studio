from copy import deepcopy
import json
from pathlib import Path

import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.migrations import migrate_model, plan_migration
from renpy_bridge.models import Project, analyze_flow


@pytest.fixture
def legacy_data():
    current = json.loads((Path(__file__).parent / "fixtures/models/valid_branching.json").read_text(encoding="utf-8"))
    current["schema_version"] = "0.9"
    current["screen"] = current.pop("canvas", {"width": 1280, "height": 720})
    current["entry_frame"] = current.pop("entry_frame_id")
    return current


def test_internal_fixture_migration_preserves_ids_sources_assets_flow_and_original(legacy_data):
    original = deepcopy(legacy_data)
    plan = plan_migration(legacy_data)
    assert plan["requires_backup"] and plan["dry_run"]
    assert "internal 0.9 fixture" in plan["scope"]
    assert not plan["save_compatibility_verified"] and not plan["sdk_compatibility_verified"]
    candidate = plan["candidate"]
    assert candidate["schema_version"] == "1.0" and candidate["canvas"] == original["screen"]
    assert candidate["entry_frame_id"] == original["entry_frame"]
    assert candidate["project_id"] == original["project_id"]
    assert candidate["save_namespace"] == original["save_namespace"]
    assert candidate["revision"] == original["revision"]
    for collection, identity in [("acts", "act_id"), ("frames", "frame_id"), ("characters", "character_id"), ("assets", "asset_id")]:
        assert set(candidate[collection]) == set(original[collection])
        assert all(item[identity] == key for key, item in candidate[collection].items())
    project = Project.model_validate(candidate)
    assert analyze_flow(project)["unreachable"] == []
    assert project.frames["frame_opening"].provenance["cell"] == "B2"
    for asset_id, asset in original["assets"].items():
        assert candidate["assets"][asset_id]["sha256"] == asset["sha256"]
        assert candidate["assets"][asset_id]["path"] == asset["path"]
    assert legacy_data == original


@pytest.mark.parametrize("field", ["project_id", "save_namespace", "revision", "frames", "assets"])
def test_missing_required_legacy_fields_are_not_guessed(legacy_data, field):
    legacy_data.pop(field)
    with pytest.raises(BridgeError) as error:
        migrate_model(legacy_data)
    assert error.value.code == "MIGRATION_IDENTITY_REQUIRED"


@pytest.mark.parametrize("schema", ["2.0", "0.8", "", None, ["1.0"], {"version": "1.0"}])
def test_unknown_versions_hard_fail_without_modifying_inputs(legacy_data, schema):
    legacy_data["schema_version"] = schema
    original = deepcopy(legacy_data)
    with pytest.raises(BridgeError) as error:
        migrate_model(legacy_data)
    assert error.value.code == "UNSUPPORTED_SCHEMA" and legacy_data == original


def test_mixed_old_and_new_names_are_not_silently_selected(legacy_data):
    legacy_data["canvas"] = {"width": 1920, "height": 1080}
    with pytest.raises(BridgeError) as error:
        migrate_model(legacy_data)
    assert error.value.code == "AMBIGUOUS_MIGRATION_FIELDS"


@pytest.mark.parametrize("mutation", [
    lambda model: model["frames"]["frame_opening"].update(frame_id="new_identity"),
    lambda model: model["frames"]["frame_opening"].update(flow={"kind": "next", "target_frame_id": "absent"}),
    lambda model: model["assets"]["asset_bg"].update(path="../outside.png"),
    lambda model: model["screen"].update(width="1280"),
    lambda model: model.update(python="__import__('os').system('unsafe')"),
])
def test_migration_rechecks_all_current_type_and_reference_boundaries(legacy_data, mutation):
    mutation(legacy_data)
    with pytest.raises(BridgeError) as error:
        migrate_model(legacy_data)
    assert error.value.code == "INVALID_MIGRATION_MODEL"


def test_current_schema_validation_does_not_create_a_migration_or_mutate_text(legacy_data):
    current = migrate_model(legacy_data)
    literal = "__import__('os').system('unwanted') [x] {b}"
    current["frames"]["frame_opening"]["dialogue"]["text"] = literal
    plan = plan_migration(current)
    assert not plan["requires_backup"] and plan["changes"] == []
    assert plan["candidate"]["frames"]["frame_opening"]["dialogue"]["text"] == literal
    assert plan["candidate"] == current
    with pytest.raises(BridgeError) as error:
        migrate_model(current, target_version="2.0")
    assert error.value.code == "UNSUPPORTED_SCHEMA"
