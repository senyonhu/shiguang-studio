import copy

import pytest

from renpy_bridge.reimport import compare_import, resolve_import


def test_source_only_edit_and_chat_only_edit_merge_without_conflict():
    baseline = {"frames": {"frame_1": {"text": "原文", "voice": "voice_1", "layout": {"x": 0.5}}}}
    current = copy.deepcopy(baseline)
    incoming = copy.deepcopy(baseline)
    current["frames"]["frame_1"]["text"] = "聊天修改"
    incoming["frames"]["frame_1"]["voice"] = "voice_2"
    plan = compare_import(baseline, current, incoming)
    assert not plan["conflicts"]
    assert plan["merged"]["frames"]["frame_1"] == {"text": "聊天修改", "voice": "voice_2", "layout": {"x": 0.5}}
    assert [change["path"] for change in plan["changes"]] == ["/frames/frame_1/voice"]


def test_same_edit_does_not_create_a_conflict_or_an_extra_change():
    baseline = {"text": "原文"}
    assert compare_import(baseline, {"text": "新文"}, {"text": "新文"}) == {"merged": {"text": "新文"}, "changes": [], "conflicts": []}


def test_different_edits_produce_exact_field_conflict_and_can_be_resolved():
    baseline = {"frames": {"frame_1": {"dialogue": {"text": "原文"}}}}
    current = {"frames": {"frame_1": {"dialogue": {"text": "聊天"}}}}
    incoming = {"frames": {"frame_1": {"dialogue": {"text": "新表"}}}}
    plan = compare_import(baseline, current, incoming)
    conflict = plan["conflicts"][0]
    assert conflict["path"] == "/frames/frame_1/dialogue/text"
    assert (conflict["baseline"], conflict["current"], conflict["incoming"]) == ("原文", "聊天", "新表")
    source = resolve_import(plan, {conflict["path"]: "source"})
    keep = resolve_import(plan, {conflict["path"]: "current"})
    assert not source["conflicts"] and not keep["conflicts"]
    assert source["merged"] == incoming and keep["merged"] == current
    assert plan["conflicts"] and plan["merged"] == current


def test_partial_resolution_leaves_other_conflicts_and_rejects_unknown_paths():
    plan = compare_import({"a": 0, "b": 0}, {"a": 1, "b": 1}, {"a": 2, "b": 2})
    resolved = resolve_import(plan, {"/a": "source"})
    assert resolved["merged"] == {"a": 2, "b": 1}
    assert [item["path"] for item in resolved["conflicts"]] == ["/b"]
    with pytest.raises(ValueError, match="未知冲突"):
        resolve_import(plan, {"/missing": "source"})
    with pytest.raises(ValueError, match="source/current"):
        resolve_import(plan, {"/a": "invented"})


def test_layout_omission_in_new_source_preserves_saved_decision():
    baseline = {"frames": {"frame_1": {"layout": {"x": 0.5, "y": 1.0}, "text": "原文"}}}
    current = {"frames": {"frame_1": {"layout": {"x": 0.7, "y": 1.0}, "text": "原文"}}}
    incoming = {"frames": {"frame_1": {"text": "新文"}}}
    plan = compare_import(baseline, current, incoming)
    assert not plan["conflicts"]
    assert plan["merged"]["frames"]["frame_1"]["layout"] == current["frames"]["frame_1"]["layout"]
    assert plan["merged"]["frames"]["frame_1"]["text"] == "新文"


def test_explicit_layout_edits_follow_three_way_rules():
    plan = compare_import({"layout": {"x": 0.5}}, {"layout": {"x": 0.7}}, {"layout": {"x": 0.2}})
    assert plan["conflicts"][0]["path"] == "/layout/x"


def test_stable_component_ids_allow_reorder_and_independent_field_edits():
    baseline = {"characters": [
        {"component_id": "anna", "asset_id": "anna_smile", "layout": {"x": 0.25}},
        {"component_id": "ben", "asset_id": "ben_normal", "layout": {"x": 0.75}},
    ]}
    current = copy.deepcopy(baseline)
    current["characters"][0]["layout"]["x"] = 0.3
    incoming = {"characters": [
        {"component_id": "ben", "asset_id": "ben_normal"},
        {"component_id": "anna", "asset_id": "anna_surprised"},
    ]}
    plan = compare_import(baseline, current, incoming)
    assert not plan["conflicts"]
    assert [item["component_id"] for item in plan["merged"]["characters"]] == ["ben", "anna"]
    anna = plan["merged"]["characters"][1]
    assert anna["asset_id"] == "anna_surprised" and anna["layout"]["x"] == 0.3
    assert any(change["path"] == "/characters/anna/asset_id" for change in plan["changes"])


def test_choice_list_conflicts_use_identity_instead_of_position():
    baseline = {"choices": [
        {"choice_id": "left", "text": "左", "target_frame_id": "frame_left"},
        {"choice_id": "right", "text": "右", "target_frame_id": "frame_right"},
    ]}
    current = copy.deepcopy(baseline)
    current["choices"][0]["text"] = "聊天左"
    incoming = copy.deepcopy(baseline)
    incoming["choices"][0]["text"] = "新表左"
    incoming["choices"].reverse()
    plan = compare_import(baseline, current, incoming)
    assert plan["conflicts"][0]["path"] == "/choices/left/text"
    result = resolve_import(plan, {"/choices/left/text": "source"})
    assert result["merged"]["choices"][1]["text"] == "新表左"


def test_conflicting_reorders_are_explicit_and_resolvable():
    baseline = {"components": [{"component_id": key} for key in ("a", "b", "c")]}
    current = {"components": [{"component_id": key} for key in ("b", "a", "c")]}
    incoming = {"components": [{"component_id": key} for key in ("a", "c", "b")]}
    plan = compare_import(baseline, current, incoming)
    assert plan["conflicts"][0]["path"] == "/components/$order"
    source = resolve_import(plan, {"/components/$order": "source"})
    assert source["merged"] == incoming


def test_list_additions_from_both_sides_are_preserved():
    baseline = {"components": [{"component_id": "b"}]}
    current = {"components": [{"component_id": "a"}, {"component_id": "b"}]}
    incoming = {"components": [{"component_id": "b"}, {"component_id": "c"}]}
    plan = compare_import(baseline, current, incoming)
    assert not plan["conflicts"]
    assert {item["component_id"] for item in plan["merged"]["components"]} == {"a", "b", "c"}


def test_delete_unchanged_object_applies_but_delete_edited_object_conflicts():
    baseline = {"assets": {"asset_1": {"path": "a.png"}}}
    unchanged = compare_import(baseline, baseline, {"assets": {}})
    assert unchanged["merged"] == {"assets": {}} and not unchanged["conflicts"]
    current = {"assets": {"asset_1": {"path": "edited.png"}}}
    conflicted = compare_import(baseline, current, {"assets": {}})
    assert conflicted["conflicts"][0]["path"] == "/assets/asset_1"
    assert conflicted["conflicts"][0]["incoming_exists"] is False
    assert resolve_import(conflicted, {"/assets/asset_1": "source"})["merged"] == {"assets": {}}


def test_null_and_missing_are_different_conflicts():
    plan = compare_import({"voice": "v1"}, {"voice": "chat"}, {"voice": None})
    conflict = plan["conflicts"][0]
    assert conflict["incoming_exists"] is True and conflict["incoming"] is None
    assert resolve_import(plan, {"/voice": "source"})["merged"] == {"voice": None}
    deletion = compare_import({"voice": "v1"}, {"voice": "chat"}, {})
    assert deletion["conflicts"][0]["incoming_exists"] is False
    assert resolve_import(deletion, {"/voice": "source"})["merged"] == {}


def test_deleted_current_component_can_be_restored_from_source_even_in_empty_list():
    baseline = {"characters": [{"component_id": "anna", "asset_id": "a"}]}
    plan = compare_import(baseline, {"characters": []}, {"characters": [{"component_id": "anna", "asset_id": "b"}]})
    assert plan["merged"] == {"characters": []}
    resolved = resolve_import(plan, {"/characters/anna": "source"})
    assert resolved["merged"] == {"characters": [{"component_id": "anna", "asset_id": "b"}]}


def test_concurrent_creation_of_different_same_id_objects_is_a_conflict():
    plan = compare_import({"frames": {}}, {"frames": {"f1": {"text": "chat"}}}, {"frames": {"f1": {"text": "source"}}})
    assert plan["conflicts"][0]["path"] == "/frames/f1"


def test_pointer_escapes_and_no_input_mutation():
    baseline, current, incoming = {"a/b~c": "old"}, {"a/b~c": "chat"}, {"a/b~c": "source"}
    originals = copy.deepcopy([baseline, current, incoming])
    plan = compare_import(baseline, current, incoming)
    assert plan["conflicts"][0]["path"] == "/a~1b~0c"
    assert resolve_import(plan, {"/a~1b~0c": "source"})["merged"] == incoming
    assert [baseline, current, incoming] == originals


def test_reordering_source_dicts_does_not_create_duplicates_or_changes():
    baseline = {"frames": {"f1": {"text": "一"}, "f2": {"text": "二"}}}
    incoming = {"frames": {"f2": {"text": "二"}, "f1": {"text": "一"}}}
    plan = compare_import(baseline, baseline, incoming)
    assert not plan["changes"] and not plan["conflicts"] and plan["merged"] == baseline


def test_duplicate_list_ids_are_rejected_instead_of_overwriting_content():
    with pytest.raises(ValueError, match="IDENTITY_CONFLICT"):
        compare_import({"characters": []}, {"characters": []}, {"characters": [{"component_id": "a"}, {"component_id": "a"}]})


def test_frame_order_lists_are_not_mistaken_for_component_lists():
    baseline = {"frame_ids": ["f1", "f2"]}
    plan = compare_import(baseline, baseline, {"frame_ids": ["f2", "f1"]})
    assert plan["merged"]["frame_ids"] == ["f2", "f1"]
    assert plan["changes"][0]["path"] == "/frame_ids"


def test_boolean_and_numeric_source_values_are_not_equated_by_python_equality():
    plan = compare_import({"value": 1}, {"value": 1}, {"value": True})
    assert plan["merged"]["value"] is True
    assert plan["changes"][0]["after"] is True
