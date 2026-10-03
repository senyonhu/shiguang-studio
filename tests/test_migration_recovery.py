"""Real legacy bytes and commit-boundary failure recovery through Store."""

import json
from pathlib import Path

import pytest

import renpy_bridge.storage as storage_module
from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import Store, atomic_json, file_hash, read_json


@pytest.fixture
def legacy_project(tmp_path):
    store = Store(tmp_path / "legacy 中文")
    project_id = store.create("迁移中断夹具", "create")["project_id"]
    store.apply_batch(project_id, 0, "content", [{
        "frame_id": "stable_frame", "act_id": "stable_act", "dialogue": {"text": "原文 [x] {b}"},
        "flow": {"kind": "end"}, "provenance": {"source_id": "source_stable", "cell": "B2"},
    }], [{"act_id": "stable_act"}])
    directory = store.directory(project_id)
    head_path = directory / "model/head.json"
    head = store.head(project_id)
    path = directory / head["model_path"]
    model = read_json(path)
    model.update(schema_version="0.9", screen=model.pop("canvas"), entry_frame=model.pop("entry_frame_id"),
                 sdk_version="8.5.3.26051504", adapter_version="0.0.9")
    # Preserve intentionally noncanonical formatting to exercise byte backups.
    path.write_text(json.dumps(model, ensure_ascii=False, indent=3) + "\n\n", encoding="utf-8")
    head["schema_version"] = "0.9"
    atomic_json(head_path, head)
    lock = {"version": "8.5.3.26051504", "relative_path": "runtimes/renpy-8.5.3", "sha256": "c" * 64}
    atomic_json(store.home / "runtimes/sdk-lock.json", lock)
    return store, project_id, path, lock


def test_failed_head_commit_reopens_legacy_and_retry_publishes_once(legacy_project, monkeypatch):
    store, project_id, path, lock = legacy_project
    source_bytes = path.read_bytes()
    head_path = store.directory(project_id) / "model/head.json"
    head_bytes = head_path.read_bytes()
    real_atomic = storage_module.atomic_json

    def fail_commit(candidate, value):
        if candidate == head_path and value.get("schema_version") == "1.0":
            raise OSError("injected migration commit failure")
        return real_atomic(candidate, value)

    with monkeypatch.context() as context:
        context.setattr(storage_module, "atomic_json", fail_commit)
        with pytest.raises(OSError, match="injected"):
            store.apply_migration(project_id, 1, "upgrade")
    assert head_path.read_bytes() == head_bytes and path.read_bytes() == source_bytes
    assert (store.directory(project_id) / "history/revisions/00000002.json").is_file()
    reopened = Store(store.home)
    assert reopened.plan_migration(project_id)["expected_revision"] == 1
    with pytest.raises(BridgeError) as error:
        reopened.load(project_id)
    assert error.value.code == "UNSUPPORTED_SCHEMA"
    result = reopened.apply_migration(project_id, 1, "upgrade")
    assert result["revision"] == 2
    assert reopened.apply_migration(project_id, 1, "upgrade") == result
    backup = Path(result["backup_path"])
    assert (backup / "original-model.json").read_bytes() == source_bytes
    assert (backup / "original-head.json").read_bytes() == head_bytes
    assert file_hash(backup / "original-model.json") == result["source_model_sha256"]
    assert read_json(backup / "migration.json")["sdk_lock"] == lock
    project = Store(store.home).load(project_id)
    assert project.revision == 2 and project.entry_frame_id == "stable_frame"
    assert project.save_namespace == project_id and project.sdk_version == lock["version"]
    assert project.frames["stable_frame"].dialogue.text == "原文 [x] {b}"
    assert project.frames["stable_frame"].provenance["cell"] == "B2"
    assert store.head(project_id)["runnable_revision"] is None


def test_corrupted_backup_cannot_publish_and_can_be_retried(legacy_project, monkeypatch):
    store, project_id, path, _ = legacy_project
    head_path = store.directory(project_id) / "model/head.json"
    original_head = head_path.read_bytes()
    real_copy = storage_module.shutil.copyfile

    def copy_then_corrupt(source, destination):
        result = real_copy(source, destination)
        if Path(destination).name == "original-model.json":
            Path(destination).write_bytes(b"injected corrupt backup")
        return result

    with monkeypatch.context() as context:
        context.setattr(storage_module.shutil, "copyfile", copy_then_corrupt)
        with pytest.raises(BridgeError) as error:
            store.apply_migration(project_id, 1, "backup-corrupted")
    assert error.value.code == "MIGRATION_BACKUP_FAILED"
    assert head_path.read_bytes() == original_head
    assert not (store.directory(project_id) / "history/revisions/00000002.json").exists()
    result = Store(store.home).apply_migration(project_id, 1, "backup-corrupted")
    assert result["revision"] == 2
    assert (Path(result["backup_path"]) / "original-model.json").read_bytes() == path.read_bytes()


@pytest.mark.parametrize("schema", [["0.9"], {"schema_version": "0.9"}])
def test_non_string_legacy_head_schema_is_a_hard_error(legacy_project, schema):
    store, project_id, _, _ = legacy_project
    head_path = store.directory(project_id) / "model/head.json"
    head = read_json(head_path)
    head["schema_version"] = schema
    atomic_json(head_path, head)
    original = head_path.read_bytes()
    with pytest.raises(BridgeError) as error:
        Store(store.home).plan_migration(project_id)
    assert error.value.code == "UNSUPPORTED_SCHEMA"
    assert head_path.read_bytes() == original
