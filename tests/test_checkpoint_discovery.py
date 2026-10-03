import json

import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import Store


def test_new_session_discovers_named_checkpoint_with_pagination_and_retrieval(tmp_path):
    store = Store(tmp_path)
    pid = store.create("跨会话恢复", "create")["project_id"]
    first = store.checkpoint(pid, "修改前")
    second = store.checkpoint(pid, "另一个检查点")
    reopened = Store(tmp_path)
    page = reopened.inspect(pid, "checkpoints", limit=1)
    assert page["total"] == 2 and page["revision"] == 0
    remainder = reopened.inspect(pid, "checkpoints", offset=1, limit=1)
    assert {x["snapshot_id"] for x in page["items"] + remainder["items"]} == {first["snapshot_id"], second["snapshot_id"]}
    record = reopened.inspect(pid, "checkpoints", object_id=first["snapshot_id"])["object"]
    assert record["note"] == "修改前" and record["model_hash"] == first["model_hash"]
    restored = reopened.restore(pid, record["snapshot_id"], 0, "restore-from-discovered")
    assert restored["revision"] == 1 and reopened.load(pid).name == "跨会话恢复"


@pytest.mark.parametrize("content", ["not JSON", json.dumps({"snapshot_id": "snap_" + "a" * 16})])
def test_corrupt_checkpoint_header_is_reported_and_does_not_modify_head(tmp_path, content):
    store = Store(tmp_path)
    pid = store.create("损坏记录", "create")["project_id"]
    before = store.head(pid)
    (store.directory(pid) / "checkpoints" / ("snap_" + "a" * 16 + ".json")).write_text(content, encoding="utf-8")
    with pytest.raises(BridgeError) as error:
        store.inspect(pid, "checkpoints")
    assert error.value.code == "SNAPSHOT_CORRUPT"
    assert store.head(pid) == before


def test_unknown_checkpoint_and_invalid_page_are_readable_errors(tmp_path):
    store = Store(tmp_path)
    pid = store.create("范围", "create")["project_id"]
    for kwargs, expected in [({"object_id": "snap_" + "a" * 16}, "SNAPSHOT_NOT_FOUND"), ({"offset": -1}, "INVALID_PAGE")]:
        with pytest.raises(BridgeError) as error:
            store.inspect(pid, "checkpoints", **kwargs)
        assert error.value.code == expected
