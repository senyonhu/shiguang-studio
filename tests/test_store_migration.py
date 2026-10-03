import json
import pytest
from renpy_bridge.storage import Store, read_json, atomic_json, file_hash
from renpy_bridge.errors import BridgeError


def legacy_project(tmp_path):
    store = Store(tmp_path)
    pid = store.create("迁移夹具", "create")["project_id"]
    directory = store.directory(pid)
    head = store.head(pid)
    path = directory / head["model_path"]
    raw = read_json(path)
    raw.update(schema_version="0.9", screen=raw.pop("canvas"), entry_frame=raw.pop("entry_frame_id"))
    atomic_json(path, raw)
    head["schema_version"] = "0.9"
    atomic_json(directory / "model/head.json", head)
    return store, pid, path


def test_migration_exact_backup_new_revision_and_idempotency(tmp_path):
    store, pid, path = legacy_project(tmp_path)
    original = path.read_bytes()
    plan = store.plan_migration(pid)
    assert plan["requires_backup"] and plan["candidate"]["save_namespace"] == pid
    assert path.read_bytes() == original
    result = store.apply_migration(pid, 0, "upgrade")
    from pathlib import Path
    assert (Path(result["backup_path"]) / "original-model.json").read_bytes() == original
    assert store.load(pid).revision == 1
    assert store.load(pid).save_namespace == pid
    assert Store(tmp_path).apply_migration(pid, 0, "upgrade") == result
    assert not store.plan_migration(pid)["requires_backup"]
    with pytest.raises(BridgeError) as error:
        store.apply_migration(pid, 1, "upgrade-again")
    assert error.value.code == "NO_MIGRATION_NEEDED"


def test_backup_failure_does_not_publish_candidate(tmp_path, monkeypatch):
    store, pid, path = legacy_project(tmp_path)
    original_head = (store.directory(pid) / "model/head.json").read_bytes()
    def fail(*args):
        raise OSError("simulated disk full during backup")
    monkeypatch.setattr("renpy_bridge.storage.shutil.copyfile", fail)
    with pytest.raises(OSError):
        store.apply_migration(pid, 0, "fail")
    assert (store.directory(pid) / "model/head.json").read_bytes() == original_head
    assert read_json(path)["schema_version"] == "0.9"


def test_stale_or_future_schema_and_identity_mismatch_refused(tmp_path):
    store, pid, path = legacy_project(tmp_path)
    with pytest.raises(BridgeError) as error:
        store.apply_migration(pid, 9, "stale")
    assert error.value.code == "VERSION_CONFLICT"
    raw = read_json(path)
    raw["project_id"] = "another_project"
    atomic_json(path, raw)
    with pytest.raises(BridgeError) as error:
        store.plan_migration(pid)
    assert error.value.code == "MIGRATION_IDENTITY_CONFLICT"
    head_path = store.directory(pid) / "model/head.json"
    head = read_json(head_path)
    head["schema_version"] = "9.0"
    atomic_json(head_path, head)
    with pytest.raises(BridgeError) as error:
        store.apply_migration(pid, 0, "future")
    assert error.value.code == "UNSUPPORTED_SCHEMA"
