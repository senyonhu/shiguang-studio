from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from renpy_bridge.models import (
    Asset,
    Canvas,
    Component,
    Flow,
    Frame,
    FramePatch,
    Layout,
    Project,
    analyze_flow,
    apply_frame_patch,
    flow_targets,
)


FIXTURES = Path(__file__).parent / "fixtures" / "models"


def read_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def project_data() -> dict:
    return read_fixture("valid_branching.json")


def test_empty_project_is_creatable_and_has_explicit_defaults():
    project = Project.model_validate(read_fixture("valid_empty.json"))
    assert project.schema_version == "1.0"
    assert project.revision == 0
    assert (project.canvas.width, project.canvas.height) == (1280, 720)
    assert project.entry_frame_id is None
    assert not project.frames
    assert analyze_flow(project) == {
        "reachable": [], "unreachable": [], "ending_frames": [],
        "without_exit": [], "cycles": [],
    }


def test_complete_states_and_short_branches_match_independent_expectations(project_data):
    project = Project.model_validate(project_data)
    expected = read_fixture("expected_branching.json")
    report = analyze_flow(project)
    assert project.entry_frame_id == expected["entry"]
    for key in ("unreachable", "without_exit", "cycles"):
        assert report[key] == expected[key]
    assert report["ending_frames"] == expected["endings"]
    assert len(report["reachable"]) == 6
    for frame_id, expected_characters in expected["frame_character_ids"].items():
        actual = [component.character_id for component in project.frames[frame_id].characters]
        assert actual == expected_characters
    for route in expected["routes"]:
        for source, target in zip(route, route[1:]):
            assert target in flow_targets(project.frames[source])
        assert project.frames[route[-1]].flow.kind == "end"
    assert project.frames["frame_opening"].dialogue.text == expected["literal_opening_text"]


def test_omitted_state_is_empty_not_previous_frame_inheritance(project_data):
    project = Project.model_validate(project_data)
    empty = project.frames["frame_end"]
    assert empty.bg is None
    assert empty.characters == []
    assert empty.decorations == []
    assert empty.dialogue is None
    assert empty.voice is None and empty.bgm is None and empty.sfx == []


def test_roundtrip_preserves_sources_ids_and_literal_text(project_data):
    project_data["frames"]["frame_opening"]["dialogue"]["text"] = '  原文\n"引号" \\ [x] {b}  '
    project = Project.model_validate(project_data)
    restored = Project.model_validate_json(project.model_dump_json())
    assert restored == project
    assert restored.frames["frame_opening"].provenance["cell"] == "B2"
    assert restored.frames["frame_opening"].dialogue.text == '  原文\n"引号" \\ [x] {b}  '


@pytest.mark.parametrize("name", [
    "invalid_future_schema.json", "invalid_dangling_target.json",
    "invalid_path_escape.json", "invalid_ambiguous_flow.json",
])
def test_invalid_fixed_inputs_are_rejected(name):
    with pytest.raises(ValidationError):
        Project.model_validate(read_fixture(name))


@pytest.mark.parametrize("path", [
    "../outside.png", "assets/../outside.png", "/absolute.png", "C:/game/a.png",
    "C:a.png", "assets\\a.png", "//server/share/a.png", "assets//a.png",
    "./assets/a.png", "assets/a.png.", "assets/a.png ", "assets/CON.png",
    "assets/lpt1.ogg", "assets/a:stream.png", "assets/a?.png", "assets/\x00.png",
])
def test_portable_managed_paths_reject_traversal_and_windows_hazards(path):
    with pytest.raises(ValidationError):
        Asset(asset_id="asset_test", kind="image", path=path, sha256="a" * 64)


def test_chinese_and_spaces_in_managed_paths_are_preserved():
    asset = Asset(asset_id="asset_test", kind="image", path="assets/第一幕/人物 笑.png", sha256="a" * 64)
    assert asset.path == "assets/第一幕/人物 笑.png"


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(unknown_setting=True),
    lambda data: data["frames"]["frame_end"].update(python="print('bad')"),
    lambda data: data["frames"]["frame_choice"]["flow"].update(expression="random()"),
    lambda data: data["assets"]["asset_bg"].update(runtime_path="C:/outside.png"),
])
def test_unknown_fields_are_rejected_at_nested_boundaries(project_data, mutate):
    mutate(project_data)
    with pytest.raises(ValidationError):
        Project.model_validate(project_data)


@pytest.mark.parametrize("mutate", [
    lambda data: data.update(revision="3"),
    lambda data: data["canvas"].update(width="1280"),
    lambda data: data["frames"]["frame_end"].update(characters=None),
    lambda data: data["frames"]["frame_end"].update(flow={"kind": "jump", "target_frame_id": "frame_join"}),
])
def test_model_does_not_coerce_wrong_types_or_add_unplanned_flow_variants(project_data, mutate):
    project_data["canvas"] = {"width": 1280, "height": 720}
    mutate(project_data)
    with pytest.raises(ValidationError):
        Project.model_validate(project_data)


@pytest.mark.parametrize("mutate", [
    lambda data: data["frames"]["frame_opening"].update(frame_id="different_id"),
    lambda data: data["acts"]["act_1"]["frame_ids"].remove("frame_opening"),
    lambda data: data["acts"]["act_2"]["frame_ids"].append("frame_opening"),
    lambda data: data["frames"]["frame_opening"].update(act_id="missing_act"),
    lambda data: data["frames"]["frame_opening"]["dialogue"].update(speaker_id="missing_character"),
    lambda data: data["frames"]["frame_opening"]["bg"].update(asset_id="missing_asset"),
    lambda data: data["frames"]["frame_opening"].update(voice="asset_bg"),
    lambda data: data.update(entry_frame_id=None),
])
def test_project_references_membership_and_asset_kinds_are_checked(project_data, mutate):
    mutate(project_data)
    with pytest.raises(ValidationError):
        Project.model_validate(project_data)


def test_same_windows_path_cannot_refer_to_different_file_contents(project_data):
    project_data["assets"]["asset_other"] = {
        "asset_id": "asset_other", "kind": "image", "path": "ASSETS/校园 背景.PNG", "sha256": "f" * 64,
    }
    with pytest.raises(ValidationError, match="同一路径"):
        Project.model_validate(project_data)


def test_duplicate_bytes_can_have_distinct_logical_asset_ids(project_data):
    project_data["assets"]["asset_other"] = copy.deepcopy(project_data["assets"]["asset_bg"])
    project_data["assets"]["asset_other"]["asset_id"] = "asset_other"
    project = Project.model_validate(project_data)
    assert project.assets["asset_other"].asset_id != project.assets["asset_bg"].asset_id


def test_choice_captions_may_repeat_but_identifiers_cannot(project_data):
    choices = project_data["frames"]["frame_choice"]["flow"]["choices"]
    choices[1]["text"] = choices[0]["text"]
    assert Project.model_validate(project_data).frames["frame_choice"].flow.kind == "choices"
    choices[1]["choice_id"] = choices[0]["choice_id"]
    with pytest.raises(ValidationError, match="choice_id"):
        Project.model_validate(project_data)


@pytest.mark.parametrize("flow", [
    {"kind": "next"}, {"kind": "choices", "choices": []},
    {"kind": "end", "target_frame_id": "frame_1"},
    {"kind": "next", "target_frame_id": "frame_1", "choices": [{"choice_id": "choice_1", "text": "走", "target_frame_id": "frame_1"}]},
])
def test_flow_variants_are_exclusive(flow):
    with pytest.raises(ValidationError):
        Flow.model_validate(flow)


def test_component_kind_specific_layout_defaults_are_reproducible():
    background = Component(component_id="bg", kind="background", asset_id="asset_bg")
    character = Component(component_id="hero", kind="character", asset_id="asset_hero", character_id="character_hero")
    text = Component(component_id="title", kind="text", text="第一幕")
    assert background.layout == Layout(x=0.5, y=0.5, anchor_x=0.5, anchor_y=0.5, width=1.0, height=1.0, fit="cover")
    assert character.layout.height == 0.85
    assert text.layout.anchor_y == 0.0 and text.layout.width == 0.9
    assert Component.model_validate_json(background.model_dump_json()) == background


def test_patch_omission_and_explicit_clearing_are_different(project_data):
    original = Project.model_validate(project_data).frames["frame_opening"]
    no_change = apply_frame_patch(original, {})
    assert no_change == original
    changed = apply_frame_patch(original, {"voice": None, "characters": []})
    assert changed.voice is None and changed.characters == []
    assert changed.bg == original.bg
    assert changed.dialogue == original.dialogue
    assert original.voice == "asset_voice" and len(original.characters) == 2


@pytest.mark.parametrize("patch", [
    {"frame_id": "new_identity"}, {"act_id": "new_act"}, {"flow": None},
    {"characters": None}, {"sfx": None}, {"transition": None},
])
def test_patch_cannot_clear_required_state_or_reassign_identity(patch):
    with pytest.raises(ValidationError):
        FramePatch.model_validate(patch)


def test_patch_requires_complete_project_reference_validation(project_data):
    project = Project.model_validate(project_data)
    patched = apply_frame_patch(project.frames["frame_opening"], {"voice": "asset_missing"})
    project_data["frames"]["frame_opening"] = patched.model_dump(mode="json")
    with pytest.raises(ValidationError, match="素材不存在"):
        Project.model_validate(project_data)


def test_display_order_and_labels_do_not_change_graph_identity(project_data):
    before = Project.model_validate(project_data)
    project_data["acts"]["act_1"]["name"] = "改名后第一幕"
    project_data["acts"]["act_1"]["frame_ids"].reverse()
    project_data["frames"]["frame_opening"]["source_label"] = "第99帧"
    after = Project.model_validate(project_data)
    assert set(before.frames) == set(after.frames)
    assert analyze_flow(before) == analyze_flow(after)


def test_unreachable_content_and_cycles_are_diagnosed_without_story_repair(project_data):
    project_data["frames"]["frame_unused"] = {"frame_id": "frame_unused", "act_id": "act_2", "flow": {"kind": "end"}}
    project_data["acts"]["act_2"]["frame_ids"].append("frame_unused")
    project_data["frames"]["frame_end"]["flow"] = {"kind": "next", "target_frame_id": "frame_choice"}
    project = Project.model_validate(project_data)
    report = analyze_flow(project)
    assert report["unreachable"] == ["frame_unused"]
    assert set(report["without_exit"]) == set(project.frames) - {"frame_unused"}
    assert report["cycles"]
    assert project.frames["frame_end"].flow.kind == "next"


def test_5000_frame_graph_does_not_depend_on_python_recursion_limit():
    count = 5000
    frame_ids = [f"frame_{index}" for index in range(count)]
    frames = {
        frame_id: {
            "frame_id": frame_id, "act_id": "act_scale", "order": index,
            "flow": {"kind": "next", "target_frame_id": frame_ids[index + 1]} if index + 1 < count else {"kind": "end"},
        }
        for index, frame_id in enumerate(frame_ids)
    }
    project = Project.model_validate({
        "project_id": "project_scale", "save_namespace": "project_scale",
        "entry_frame_id": frame_ids[0],
        "acts": {"act_scale": {"act_id": "act_scale", "frame_ids": frame_ids}}, "frames": frames,
    })
    report = analyze_flow(project)
    assert len(report["reachable"]) == count
    assert report["ending_frames"] == [frame_ids[-1]]
    assert not report["cycles"] and not report["without_exit"]


def test_numeric_limits_reject_nonfinite_and_unreasonable_canvas():
    with pytest.raises(ValidationError):
        Layout(x=float("nan"))
    with pytest.raises(ValidationError):
        Layout(height=float("inf"))
    with pytest.raises(ValidationError):
        Canvas(width=0)


def test_components_do_not_mix_text_image_and_character_payloads():
    with pytest.raises(ValidationError):
        Component(component_id="mixed", kind="text", text="标题", asset_id="asset_image")
    with pytest.raises(ValidationError):
        Component(component_id="hero", kind="character", asset_id="asset_image")
    with pytest.raises(ValidationError):
        Frame(frame_id="frame_test", act_id="act_test", bg=Component(component_id="title", kind="text", text="标题"), flow=Flow(kind="end"))
