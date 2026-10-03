"""Schema 1.1 references, source ownership, and real package/candidate transactions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from renpy_bridge.errors import BridgeError
from renpy_bridge.models import Project, flow_targets, analyze_flow
from renpy_bridge.migrations import plan_migration
from renpy_bridge.minigame_packages import validate_directory, import_package
from renpy_bridge.storage import Store
from renpy_bridge.reimport import compare_import


def package(directory, version="1.0"):
    directory.mkdir(parents=True, exist_ok=True)
    data = b"<!doctype html><html><body>offline puzzle</body></html>"
    (directory / "index.html").write_bytes(data)
    manifest = {"format_version": "1.0", "protocol": "1.0", "game_id": "game_lock", "version": version,
                "entry": "index.html", "inputs": {"difficulty": {"type": "integer", "default": 2, "minimum": 1, "maximum": 3}},
                "results": {"outcomes": ["win", "lose"], "fields": {"score": {"type": "integer", "minimum": 0, "maximum": 100}}},
                "save_capabilities": {"entry_restart": True, "checkpoint": False, "snapshot_version": None},
                "files": [{"path": "index.html", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}]}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return validate_directory(directory)


@pytest.fixture
def model(tmp_path):
    info = package(tmp_path / "source")
    targets = ["frame_high", "frame_win", "frame_lose", "frame_cancel", "frame_error", "frame_fallback"]
    frames = {key: {"frame_id": key, "act_id": "act_main", "flow": {"kind": "end"}} for key in targets}
    frames["frame_game"] = {"frame_id": "frame_game", "act_id": "act_main", "flow": {
        "kind": "minigame", "game_id": "game_lock", "instance_id": "door_lock", "save_mode": "entry_restart",
        "inputs": {"difficulty": {"kind": "variable", "variable_id": "difficulty"}},
        "result_branches": [
            {"branch_id": "high", "outcome": "win", "predicate": {"op": "gte", "left": {"kind": "result", "field": "score"}, "right": {"kind": "literal", "value": 80}},
             "effects": [{"effect_id": "set_high", "op": "set", "variable_id": "score", "value": {"kind": "result", "field": "score"}}], "target_frame_id": "frame_high"},
            {"branch_id": "win", "outcome": "win", "effects": [{"effect_id": "set_win", "op": "set", "variable_id": "score", "value": {"kind": "result", "field": "score"}}], "target_frame_id": "frame_win"},
            {"branch_id": "lose", "outcome": "lose", "effects": [], "target_frame_id": "frame_lose"}],
        "cancel_target_frame_id": "frame_cancel", "error_target_frame_id": "frame_error", "fallback_target_frame_id": "frame_fallback"}}
    return {"schema_version": "1.1", "project_id": "p_example", "save_namespace": "p_example", "entry_frame_id": "frame_game",
            "acts": {"act_main": {"act_id": "act_main", "frame_ids": list(frames)}}, "frames": frames,
            "variables": {"difficulty": {"variable_id": "difficulty", "type": "int", "default": 2, "minimum": 1, "maximum": 3},
                          "score": {"variable_id": "score", "type": "int", "default": 0, "minimum": 0, "maximum": 100},
                          "flag": {"variable_id": "flag", "type": "bool", "default": False}},
            "minigames": {"game_lock": {"game_id": "game_lock", "package_digest": info.digest, "package_version": "1.0", "path": "minigames/" + info.digest, "manifest": info.manifest}}}


def test_schema_graph_includes_every_minigame_result_and_system_target(model):
    project = Project.model_validate(model)
    assert len(flow_targets(project.frames["frame_game"])) == 6
    assert analyze_flow(project)["unreachable"] == []
    dumped = project.model_dump(mode="json")
    assert Project.model_validate(dumped).model_dump(mode="json") == dumped
    assert dumped["frames"]["frame_game"]["flow"]["inputs"]["difficulty"] == {"kind": "variable", "variable_id": "difficulty"}


def test_negative_add_operand_is_typed_but_final_range_is_a_runtime_decision(model):
    effect = model["frames"]["frame_game"]["flow"]["result_branches"][0]["effects"][0]
    effect.update(op="add", value={"kind": "literal", "value": -10})
    assert Project.model_validate(model).frames["frame_game"].flow.result_branches[0].effects[0].value.value == -10
    effect["value"]["value"] = -10.5
    with pytest.raises(ValidationError):
        Project.model_validate(model)


@pytest.mark.parametrize("entry", ["register", "stage_package", "stage_definition", "batch", "reimport_source", "reimport_definition", "commit_tampered_candidate"])
def test_formal_store_writes_reject_prototype_at_every_entry(tmp_path, model, entry):
    store, pid = prepared_store(tmp_path)
    source = tmp_path / "source"
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["prototype"] = True
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    info = import_package(source, store.directory(pid) / "minigames", allow_prototype=True)
    definition = {"game_id": "game_lock", "package_digest": info.digest, "package_version": "1.0",
                  "path": "minigames/" + info.digest, "manifest": info.manifest, "provenance": {"source_id": "source_story"}}
    # Reading a marked draft is permitted; it never converts prototype to formal.
    draft = deepcopy(model)
    draft["minigames"]["game_lock"] = definition
    assert Project.model_validate(draft).minigames["game_lock"].manifest["prototype"] is True
    session = store.begin_import(pid, 1, [], "candidate", game_ids=["game_lock"])["import_session_id"]
    head = (store.directory(pid) / "model/head.json").read_bytes()
    if entry.startswith("reimport"):
        store.apply_batch(pid, 1, "baseline", [{"frame_id": "frame_end", "act_id": "act", "flow": {"kind": "end"},
                                                "provenance": {"source_id": "source_story"}}], acts=[{"act_id": "act"}])
        head = (store.directory(pid) / "model/head.json").read_bytes()
    with pytest.raises(BridgeError) as error:
        if entry == "register":
            store.register_minigame_package(pid, 1, "proto", source, allow_prototype=True)
        elif entry == "stage_package":
            store.stage_minigame_package(pid, session, "proto", source, allow_prototype=True)
        elif entry == "stage_definition":
            store.stage_import(pid, session, "proto", [], minigames=[definition])
        elif entry == "batch":
            store.apply_batch(pid, 1, "proto", [], minigames=[definition])
        elif entry == "reimport_source":
            store.plan_reimport(pid, "source_story", [], incoming_minigames=[{"source_path": str(source), "allow_prototype": True}])
        elif entry == "reimport_definition":
            store.plan_reimport(pid, "source_story", [], incoming_minigames=[definition])
        else:
            candidate_path = store.import_path(pid, session)
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            candidate["model"]["minigames"]["game_lock"] = definition
            candidate["staged_game_ids"] = ["game_lock"]
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
            store.commit_import(pid, session, 1, "proto")
    assert error.value.code == "MINIGAME_PROTOTYPE_ONLY"
    assert (store.directory(pid) / "model/head.json").read_bytes() == head


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(schema_version="1.0"),
    lambda m: m["variables"]["score"].update(default=True),
    lambda m: m["variables"]["score"].update(minimum=101, maximum=100),
    lambda m: m["minigames"]["game_lock"].update(path="../outside"),
    lambda m: m["minigames"]["game_lock"].update(package_version="99"),
    lambda m: m["frames"]["frame_game"]["flow"].update(game_id="absent"),
    lambda m: m["frames"]["frame_game"]["flow"].update(save_mode="checkpoint"),
    lambda m: m["frames"]["frame_game"]["flow"].update(cancel_target_frame_id=None),
    lambda m: m["frames"]["frame_game"]["flow"].update(error_target_frame_id="absent"),
    lambda m: m["frames"]["frame_game"]["flow"]["inputs"].update(difficulty={"kind": "variable", "variable_id": "flag"}),
    lambda m: m["frames"]["frame_game"]["flow"]["inputs"].update(difficulty={"kind": "result", "field": "score"}),
    lambda m: m["frames"]["frame_game"]["flow"]["inputs"].update(difficulty={"kind": "literal", "value": 99}),
    lambda m: m["frames"]["frame_game"]["flow"]["result_branches"][0].update(outcome="error"),
    lambda m: m["frames"]["frame_game"]["flow"]["result_branches"][0]["effects"][0].update(variable_id="absent"),
    lambda m: m["frames"]["frame_game"]["flow"]["result_branches"][0]["effects"][0].update(op="add", variable_id="flag"),
    lambda m: m["frames"]["frame_game"]["flow"]["result_branches"][0]["predicate"].update(op="eval"),
    lambda m: m["frames"]["frame_game"]["flow"]["result_branches"][0]["predicate"].update(right={"kind": "literal", "value": "80"}),
])
def test_invalid_refs_types_effects_and_code_are_not_accepted(model, mutation):
    mutation(model)
    with pytest.raises(ValidationError):
        Project.model_validate(model)


def test_duplicate_call_branch_and_effect_identities_are_rejected(model):
    duplicate = deepcopy(model["frames"]["frame_game"])
    duplicate["frame_id"] = "frame_duplicate"
    model["frames"]["frame_duplicate"] = duplicate
    model["acts"]["act_main"]["frame_ids"].append("frame_duplicate")
    with pytest.raises(ValidationError):
        Project.model_validate(model)


def test_explicit_migration_retains_old_serialization_and_identities():
    old = Project(project_id="p_old", save_namespace="p_old").model_dump(mode="json")
    assert "variables" not in old and "minigames" not in old
    upgraded = plan_migration(old, "1.1")
    assert upgraded["requires_backup"] and upgraded["candidate"]["variables"] == {}
    restored = deepcopy(upgraded["candidate"])
    restored.pop("variables")
    restored.pop("minigames")
    restored["schema_version"] = "1.0"
    assert restored == old
    with pytest.raises(BridgeError):
        plan_migration(upgraded["candidate"], "1.0")


def prepared_store(tmp_path):
    store = Store(tmp_path / "home")
    created = store.create("小游戏项目", "create")
    pid = created["project_id"]
    store.apply_migration(pid, 0, "migrate", "1.1")
    return store, pid


def test_registration_requires_migration_and_preserves_revision_lock_and_idempotency(tmp_path):
    info = package(tmp_path / "source")
    store = Store(tmp_path / "home")
    pid = store.create("游戏", "create")["project_id"]
    with pytest.raises(BridgeError) as error:
        store.register_minigame_package(pid, 0, "register", info.root)
    assert error.value.code == "MINIGAME_MIGRATION_REQUIRED"
    assert store.head(pid)["revision"] == 0
    original = (store.directory(pid) / "history/revisions/00000000.json").read_bytes()
    migration = store.apply_migration(pid, 0, "migrate", "1.1")
    assert (Path(migration["backup_path"]) / "original-model.json").read_bytes() == original
    registered = store.register_minigame_package(pid, 1, "register", info.root, expected_digest=info.digest)
    assert store.register_minigame_package(pid, 1, "register", info.root, expected_digest=info.digest) == registered
    with pytest.raises(BridgeError) as error:
        store.define_variables(pid, 1, "stale", [{"variable_id": "score", "type": "int", "default": 0}])
    assert error.value.code == "VERSION_CONFLICT"
    assert store.head(pid)["revision"] == 2


def test_candidate_forward_refs_commit_packages_variables_and_frames_once(tmp_path, model):
    store, pid = prepared_store(tmp_path)
    source = tmp_path / "source"
    session = store.begin_import(pid, 1, list(model["frames"]), "candidate", game_ids=["game_lock"], variable_ids=list(model["variables"]))["import_session_id"]
    store.stage_import(pid, session, "frames", list(model["frames"].values()), acts=list(model["acts"].values()), entry_frame_id="frame_game")
    assert store.head(pid)["revision"] == 1 and not store.load(pid).minigames
    store.stage_import(pid, session, "variables", [], variables=list(model["variables"].values()))
    with pytest.raises(BridgeError) as error:
        store.commit_import(pid, session, 1, "commit")
    assert error.value.code == "IMPORT_INCOMPLETE"
    staged = store.stage_minigame_package(pid, session, "package", source)
    assert staged["is_committed"] is False and not store.load(pid).minigames
    committed = store.commit_import(pid, session, 1, "commit")
    assert committed["revision"] == 2
    assert store.commit_import(pid, session, 1, "commit") == committed
    project = store.load(pid)
    assert len(project.frames) == 7 and len(project.variables) == 3 and len(project.minigames) == 1


def test_candidate_bad_blob_or_new_head_cannot_change_live_project(tmp_path, model):
    store, pid = prepared_store(tmp_path)
    session = store.begin_import(pid, 1, list(model["frames"]), "candidate", game_ids=["game_lock"], variable_ids=list(model["variables"]))["import_session_id"]
    store.stage_import(pid, session, "all", list(model["frames"].values()), acts=list(model["acts"].values()), variables=list(model["variables"].values()), entry_frame_id="frame_game")
    staged = store.stage_minigame_package(pid, session, "package", tmp_path / "source")
    path = store.directory(pid) / staged["minigame"]["path"] / "index.html"
    original = path.read_bytes()
    path.write_bytes(b"damaged")
    head_before = (store.directory(pid) / "model/head.json").read_bytes()
    with pytest.raises(BridgeError):
        store.commit_import(pid, session, 1, "commit")
    assert (store.directory(pid) / "model/head.json").read_bytes() == head_before
    path.write_bytes(original)
    store.define_variables(pid, 1, "other", [{"variable_id": "outside", "type": "int", "default": 0}])
    with pytest.raises(BridgeError) as error:
        store.commit_import(pid, session, 1, "commit")
    assert error.value.code == "VERSION_CONFLICT"
    assert "outside" in store.load(pid).variables and not store.load(pid).frames


def test_project_checkpoint_restores_old_package_and_rejects_missing_blob(tmp_path):
    store, pid = prepared_store(tmp_path)
    old = package(tmp_path / "source", "1.0")
    store.register_minigame_package(pid, 1, "old", old.root)
    store.define_variables(pid, 2, "variables", [{"variable_id": "score", "type": "int", "default": 0}])
    snapshot = store.checkpoint(pid, "原包")
    new = package(old.root, "1.1")
    store.register_minigame_package(pid, 3, "new", new.root)
    store.restore(pid, snapshot["snapshot_id"], 4, "restore")
    assert store.load(pid).minigames["game_lock"].package_digest == old.digest
    old_file = store.directory(pid) / "minigames" / old.digest / "index.html"
    old_file.unlink()
    head_before = (store.directory(pid) / "model/head.json").read_bytes()
    with pytest.raises(BridgeError):
        store.restore(pid, snapshot["snapshot_id"], 5, "bad-restore")
    assert (store.directory(pid) / "model/head.json").read_bytes() == head_before


def test_result_branch_order_conflict_uses_stable_ids():
    baseline = {"branches": [{"branch_id": name, "outcome": "win", "threshold": index} for index, name in enumerate(["a", "b", "c"])]}
    current = {"branches": [baseline["branches"][1], baseline["branches"][0], baseline["branches"][2]]}
    incoming = {"branches": [baseline["branches"][2], baseline["branches"][1], baseline["branches"][0]]}
    plan = compare_import(baseline, current, incoming)
    assert any(conflict["path"] == "/branches/$order" for conflict in plan["conflicts"])


def test_reimport_can_merge_package_variable_and_stable_branch_conflicts_atomically(tmp_path, model):
    store, pid = prepared_store(tmp_path)
    store.register_minigame_package(pid, 1, "package", tmp_path / "source")
    for frame in model["frames"].values():
        frame["provenance"] = {"source_id": "source_story"}
    for value in list(model["variables"].values()) + list(model["minigames"].values()):
        value["provenance"] = {"source_id": "source_story"}
    store.apply_batch(pid, 2, "original", list(model["frames"].values()), acts=list(model["acts"].values()),
                      entry_frame_id="frame_game", variables=list(model["variables"].values()), minigames=list(model["minigames"].values()))
    current_flow = store.load(pid).frames["frame_game"].flow.model_dump(mode="json")
    current_flow["result_branches"][0]["predicate"]["right"]["value"] = 70
    store.patch_frame(pid, "frame_game", {"flow": current_flow}, 3, "chat_branch")
    store.define_variables(pid, 4, "chat_variable", [{"variable_id": "score", "type": "int", "default": 10, "minimum": 0, "maximum": 100}])
    incoming_frames = deepcopy(list(model["frames"].values()))
    next(value for value in incoming_frames if value["frame_id"] == "frame_game")["flow"]["result_branches"][0]["predicate"]["right"]["value"] = 90
    incoming_variables = deepcopy(list(model["variables"].values()))
    next(value for value in incoming_variables if value["variable_id"] == "score")["default"] = 25
    new_package = package(tmp_path / "source", "1.1")
    plan = store.plan_reimport(pid, "source_story", incoming_frames, incoming_variables=incoming_variables,
                               incoming_minigames=[{"source_path": str(new_package.root), "expected_digest": new_package.digest}])
    assert store.head(pid)["revision"] == 5
    paths = {value["path"] for value in plan["conflicts"]}
    assert "/variables/score/default" in paths
    assert "/frames/frame_game/flow/result_branches/high/predicate/right/value" in paths
    with pytest.raises(BridgeError):
        store.apply_reimport(pid, plan["plan_id"], {}, 5, "unresolved")
    assert store.head(pid)["revision"] == 5
    store.apply_reimport(pid, plan["plan_id"], {path: "source" for path in paths}, 5, "resolved")
    result = store.load(pid)
    assert result.minigames["game_lock"].package_digest == new_package.digest
    assert result.variables["score"].default == 25
    assert result.frames["frame_game"].flow.result_branches[0].predicate.right.value == 90
