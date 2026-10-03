import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import Store, atomic_json


@pytest.fixture
def project(tmp_path):
    store = Store(tmp_path)
    project_id = store.create("作品", "create")["project_id"]
    return store, project_id


def frames():
    return [
        {"frame_id": "f1", "act_id": "a1", "dialogue": {"text": "第一句"}, "flow": {"kind": "next", "target_frame_id": "f2"}},
        {"frame_id": "f2", "act_id": "a1", "dialogue": {"text": "第二句"}, "flow": {"kind": "end"}},
    ]


def initialize(store, pid):
    return store.apply_batch(pid, 0, "initial", frames(), acts=[{"act_id": "a1"}])


def test_creation_idempotent_and_different_payload_rejected(project):
    store, pid = project
    assert store.create("作品", "create")["project_id"] == pid
    with pytest.raises(BridgeError, match="不同请求"):
        store.create("另一个作品", "create")


def test_full_batch_and_patch_omission_clear_and_validation(project):
    store, pid = project
    initialize(store, pid)
    store.patch_frame(pid, "f1", {"voice": None}, 1, "voice")
    assert store.load(pid).frames["f1"].dialogue.text == "第一句"
    store.patch_frame(pid, "f1", {"dialogue": None}, 2, "clear")
    assert store.load(pid).frames["f1"].dialogue is None
    with pytest.raises(BridgeError):
        store.patch_frame(pid, "f1", {"flow": {"kind": "next", "target_frame_id": "absent"}}, 3, "bad")
    assert store.load(pid).revision == 3


def test_concurrent_revision_prevents_lost_update(project):
    store, pid = project
    initialize(store, pid)
    def write(index):
        try:
            return store.patch_frame(pid, "f1", {"dialogue": {"text": str(index)}}, 1, f"write_{index}")["revision"]
        except BridgeError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, [1, 2]))
    assert sorted(str(value) for value in results) == ["2", "VERSION_CONFLICT"]


def test_retry_after_commit_returns_same_result(project):
    store, pid = project
    first = initialize(store, pid)
    assert initialize(store, pid) == first
    assert store.load(pid).revision == 1


def test_cross_batch_forward_reference_and_reopen(project):
    store, pid = project
    sid = store.begin_import(pid, 0, ["f1", "f2"], "large")["import_session_id"]
    store.stage_import(pid, sid, "one", [frames()[0]], [{"act_id": "a1"}])
    assert store.load(pid).frames == {}
    reopened = Store(store.home)
    with pytest.raises(BridgeError) as err:
        reopened.commit_import(pid, sid, 0, "commit")
    assert err.value.code == "IMPORT_INCOMPLETE"
    reopened.stage_import(pid, sid, "two", [frames()[1]])
    result = reopened.commit_import(pid, sid, 0, "commit")
    assert result["revision"] == 1 and reopened.load(pid).frames["f1"].flow.target_frame_id == "f2"
    assert reopened.commit_import(pid, sid, 0, "commit") == result


def test_candidate_cannot_overwrite_new_revision(project):
    store, pid = project
    sid = store.begin_import(pid, 0, ["f1", "f2"], "large")["import_session_id"]
    store.stage_import(pid, sid, "all", frames(), [{"act_id": "a1"}])
    initialize(store, pid)
    with pytest.raises(BridgeError) as err:
        store.commit_import(pid, sid, 0, "commit")
    assert err.value.code == "VERSION_CONFLICT"


def test_checkpoint_retains_old_content_and_runnable_version(project):
    store, pid = project
    initialize(store, pid)
    snap = store.checkpoint(pid, "修改前")
    store.mark_runnable(pid, 1)
    store.patch_frame(pid, "f1", {"dialogue": {"text": "新句"}}, 1, "edit")
    assert store.head(pid)["runnable_revision"] == 1
    store.restore(pid, snap["snapshot_id"], 2, "restore")
    assert store.load(pid).revision == 3
    assert store.load(pid).frames["f1"].dialogue.text == "第一句"


def test_uncommitted_orphan_revision_does_not_change_head(project):
    store, pid = project
    initialize(store, pid)
    atomic_json(store.directory(pid)/"history/revisions/00000002.json", {"invalid": "orphan"})
    assert store.load(pid).revision == 1


def test_path_escape_and_unknown_future_schema_rejected(project):
    store, pid = project
    with pytest.raises(BridgeError):
        store.directory("../outside")
    head = store.head(pid)
    head["schema_version"] = "99"
    atomic_json(store.directory(pid)/"model/head.json", head)
    with pytest.raises(BridgeError) as err:
        store.load(pid)
    assert err.value.code == "UNSUPPORTED_SCHEMA"
