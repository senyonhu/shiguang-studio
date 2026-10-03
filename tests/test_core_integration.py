"""Exercise persistence contracts across source, import, chat and recovery APIs.

Failure injection happens at actual JSON commit boundaries so these tests cover
reopening a Store after an interrupted write, rather than only orphan fixtures.
"""

from __future__ import annotations

import copy
import asyncio
import json
import wave
from pathlib import Path

import pytest

import renpy_bridge.sources as source_module
import renpy_bridge.storage as storage_module
from renpy_bridge.errors import BridgeError
from renpy_bridge.models import analyze_flow
from renpy_bridge.storage import Store, file_hash, read_json


@pytest.fixture
def managed_project(tmp_path):
    store = Store(tmp_path / "制作项目 中文")
    project_id = store.create("中断恢复作品", "create-fixture")["project_id"]
    return store, project_id


def source_frames():
    return [
        {
            "frame_id": "opening", "act_id": "act_one", "order": 0,
            "dialogue": {"text": "第一句"},
            "decorations": [{"component_id": "title", "kind": "text", "text": "第一幕"}],
            "flow": {"kind": "next", "target_frame_id": "ending"},
            "provenance": {"source_id": "source_story", "cell": "B2"},
        },
        {
            "frame_id": "ending", "act_id": "act_one", "order": 1,
            "dialogue": {"text": "结束"}, "flow": {"kind": "end"},
            "provenance": {"source_id": "source_story", "cell": "B3"},
        },
    ]


def initialize(store, project_id):
    return store.apply_batch(
        project_id, 0, "initial-source", source_frames(),
        acts=[{"act_id": "act_one", "name": "第一幕"}],
        characters=[{"character_id": "hero", "name": "小明"}],
    )


def test_head_write_failure_keeps_old_model_and_same_operation_recovers(managed_project, monkeypatch):
    store, project_id = managed_project
    initialize(store, project_id)
    original = store.load(project_id).model_dump(mode="json")
    real_atomic = storage_module.atomic_json
    head_path = store.directory(project_id) / "model" / "head.json"

    def fail_head(path, value):
        if path == head_path and value.get("revision") == 2:
            raise OSError("injected head replacement failure")
        return real_atomic(path, value)

    patch = {"dialogue": {"text": "聊天修改"}}
    with monkeypatch.context() as context:
        context.setattr(storage_module, "atomic_json", fail_head)
        with pytest.raises(OSError, match="injected"):
            store.patch_frame(project_id, "opening", patch, 1, "chat-edit")

    reopened = Store(store.home)
    assert reopened.load(project_id).model_dump(mode="json") == original
    result = reopened.patch_frame(project_id, "opening", patch, 1, "chat-edit")
    assert result["revision"] == 2
    assert reopened.patch_frame(project_id, "opening", patch, 1, "chat-edit") == result
    assert reopened.load(project_id).frames["opening"].dialogue.text == "聊天修改"


def test_revision_written_before_failed_head_commit_is_not_readable(managed_project, monkeypatch):
    store, project_id = managed_project
    initialize(store, project_id)
    real_atomic = storage_module.atomic_json

    def fail_head(path, value):
        if path.name == "head.json" and value.get("revision") == 2:
            raise OSError("injected head failure")
        return real_atomic(path, value)

    with monkeypatch.context() as context:
        context.setattr(storage_module, "atomic_json", fail_head)
        with pytest.raises(OSError):
            store.patch_frame(project_id, "opening", {"dialogue": None}, 1, "interrupted")
    assert (store.directory(project_id) / "history/revisions/00000002.json").exists()
    with pytest.raises(BridgeError) as error:
        Store(store.home).load(project_id, revision=2)
    assert error.value.code == "REVISION_NOT_FOUND"


def test_creation_recovers_same_identity_when_index_commit_fails(tmp_path, monkeypatch):
    store = Store(tmp_path / "creation")
    real_atomic = storage_module.atomic_json

    def fail_index(path, value):
        if path == store.projects / "index.json":
            raise OSError("injected index failure")
        return real_atomic(path, value)

    with monkeypatch.context() as context:
        context.setattr(storage_module, "atomic_json", fail_index)
        with pytest.raises(OSError, match="injected"):
            store.create("同一个作品", "stable-create")
    committed = [item.parent.parent for item in store.projects.glob("*/model/head.json")]
    assert len(committed) == 1
    result = Store(store.home).create("同一个作品", "stable-create")
    assert Path(result["path"]) == committed[0]
    assert len(list(store.projects.glob("*/model/head.json"))) == 1
    assert result["project_id"] in store.list_projects()["projects"]


def test_import_commit_recovers_after_session_marker_failure(managed_project, monkeypatch):
    store, project_id = managed_project
    session = store.begin_import(project_id, 0, ["opening", "ending"], "begin-large")["import_session_id"]
    store.stage_import(project_id, session, "first", [source_frames()[0]], [{"act_id": "act_one"}])
    store.stage_import(project_id, session, "second", [source_frames()[1]])
    path = store.import_path(project_id, session)
    real_atomic = storage_module.atomic_json

    def fail_marker(candidate, value):
        if candidate == path and value.get("state") == "committed":
            raise OSError("injected import marker failure")
        return real_atomic(candidate, value)

    with monkeypatch.context() as context:
        context.setattr(storage_module, "atomic_json", fail_marker)
        with pytest.raises(OSError, match="injected"):
            store.commit_import(project_id, session, 0, "commit-large")
    reopened = Store(store.home)
    assert reopened.load(project_id).revision == 1
    assert read_json(path)["state"] == "staged"
    result = reopened.commit_import(project_id, session, 0, "commit-large")
    assert result["revision"] == 1
    assert reopened.commit_import(project_id, session, 0, "commit-large") == result
    assert reopened.load(project_id).revision == 1


def test_staged_import_sources_can_be_reimported_after_chat_change(managed_project):
    store, project_id = managed_project
    session = store.begin_import(project_id, 0, ["opening", "ending"], "large-source")["import_session_id"]
    store.stage_import(project_id, session, "first", [source_frames()[0]], [{"act_id": "act_one"}])
    Store(store.home).stage_import(project_id, session, "last", [source_frames()[1]])
    store.commit_import(project_id, session, 0, "commit-source")
    store.patch_frame(project_id, "opening", {"dialogue": {"text": "聊天修改"}}, 1, "chat")
    incoming = source_frames()
    incoming[1]["dialogue"]["text"] = "来源更新结束"
    plan = Store(store.home).plan_reimport(project_id, "source_story", incoming)
    assert not plan["conflicts"]
    store.apply_reimport(project_id, plan["plan_id"], {}, 2, "reimport")
    project = Store(store.home).load(project_id)
    assert project.frames["opening"].dialogue.text == "聊天修改"
    assert project.frames["ending"].dialogue.text == "来源更新结束"
    assert project.frames["opening"].provenance["cell"] == "B2"


def test_source_omitted_defaults_preserve_chat_changes_and_saved_layout(managed_project):
    store, project_id = managed_project
    initialize(store, project_id)
    store.patch_frame(project_id, "opening", {"dialogue": {"speaker_id": "hero", "text": "第一句"}}, 1, "choose-speaker")
    store.layout(project_id, "frames", {"title": {"x": 0.7}}, 2, "layout", ["opening"])
    incoming = source_frames()
    incoming[1]["dialogue"]["text"] = "新表结尾"
    plan = store.plan_reimport(project_id, "source_story", incoming)
    assert not plan["conflicts"], "原来源省略的 speaker_id 仍为 null，不能把它误认成来源删除"
    store.apply_reimport(project_id, plan["plan_id"], {}, 3, "merge-source")
    project = store.load(project_id)
    assert project.frames["opening"].dialogue.speaker_id == "hero"
    assert project.frames["opening"].decorations[0].layout.x == 0.7
    assert project.frames["ending"].dialogue.text == "新表结尾"
    repeat = store.plan_reimport(project_id, "source_story", incoming)
    assert repeat["changes"] == [] and repeat["conflicts"] == []


def test_reimport_conflicts_require_choice_and_cannot_overwrite_new_chat_revision(managed_project):
    store, project_id = managed_project
    initialize(store, project_id)
    store.patch_frame(project_id, "opening", {"dialogue": {"text": "聊天改写"}}, 1, "first-chat")
    incoming = source_frames()
    incoming[0]["dialogue"]["text"] = "新表改写"
    plan = store.plan_reimport(project_id, "source_story", incoming)
    assert [item["path"] for item in plan["conflicts"]] == ["/frames/opening/dialogue/text"]
    with pytest.raises(BridgeError) as error:
        store.apply_reimport(project_id, plan["plan_id"], {}, 2, "unresolved")
    assert error.value.code == "IMPORT_CONFLICT"
    assert store.load(project_id).revision == 2
    store.patch_frame(project_id, "ending", {"dialogue": {"text": "后来的聊天"}}, 2, "late-chat")
    with pytest.raises(BridgeError) as error:
        store.apply_reimport(project_id, plan["plan_id"], {"/frames/opening/dialogue/text": "source"}, 2, "stale-plan")
    assert error.value.code == "VERSION_CONFLICT"
    project = store.load(project_id)
    assert project.frames["opening"].dialogue.text == "聊天改写"
    assert project.frames["ending"].dialogue.text == "后来的聊天"


def test_reimport_duplicate_frame_ids_are_rejected_before_planning(managed_project):
    store, project_id = managed_project
    initialize(store, project_id)
    incoming = source_frames()
    duplicate = copy.deepcopy(incoming[0])
    duplicate["dialogue"]["text"] = "同一身份的另一句"
    with pytest.raises(BridgeError) as error:
        store.plan_reimport(project_id, "source_story", [*incoming, duplicate])
    assert error.value.code == "DUPLICATE_ID"
    assert store.load(project_id).revision == 1


def test_reimport_new_identity_cannot_overwrite_a_different_sources_frame(managed_project):
    store, project_id = managed_project
    initialize(store, project_id)
    independent = {
        "frame_id": "other_source_frame", "act_id": "act_one", "order": 2,
        "dialogue": {"text": "另一份资料的原文"}, "flow": {"kind": "end"},
        "provenance": {"source_id": "source_other"},
    }
    store.apply_batch(project_id, 1, "other-source", [independent])
    replacement = copy.deepcopy(independent)
    replacement["dialogue"]["text"] = "错误覆盖的第一份资料"
    replacement["provenance"]["source_id"] = "source_story"
    with pytest.raises(BridgeError) as error:
        store.plan_reimport(project_id, "source_story", [*source_frames(), replacement])
    assert error.value.code == "IDENTITY_CONFLICT"
    assert store.load(project_id).frames["other_source_frame"].dialogue.text == "另一份资料的原文"
    assert store.load(project_id).revision == 2


def test_existing_frames_do_not_count_as_this_imports_staged_content(managed_project):
    store, project_id = managed_project
    initialize(store, project_id)
    session = store.begin_import(project_id, 1, ["opening", "ending"], "replace-source")["import_session_id"]
    incoming = source_frames()[0]
    incoming["dialogue"]["text"] = "来源第一批"
    progress = store.stage_import(project_id, session, "first", [incoming])
    assert progress["staged_count"] == 1
    with pytest.raises(BridgeError) as error:
        store.commit_import(project_id, session, 1, "incomplete-source")
    assert error.value.code == "IMPORT_INCOMPLETE"
    assert error.value.details["missing_frame_ids"] == ["ending"]
    assert store.load(project_id).frames["opening"].dialogue.text == "第一句"


def test_duplicate_id_inside_staged_batch_does_not_silently_replace_content(managed_project):
    store, project_id = managed_project
    session = store.begin_import(project_id, 0, ["opening", "ending"], "stage-duplicates")["import_session_id"]
    duplicate = copy.deepcopy(source_frames()[0])
    duplicate["dialogue"]["text"] = "同批重复身份的不同台词"
    with pytest.raises(BridgeError) as error:
        store.stage_import(project_id, session, "invalid-batch", [source_frames()[0], duplicate], [{"act_id": "act_one"}])
    assert error.value.code == "DUPLICATE_ID"
    candidate = read_json(store.import_path(project_id, session))
    assert candidate["model"]["frames"] == {} and candidate["batches"] == {}
    assert store.load(project_id).revision == 0


def test_source_backup_matches_extracted_snapshot_or_change_is_reported(managed_project, tmp_path, monkeypatch):
    store, project_id = managed_project
    source = tmp_path / "剧情 原件.txt"
    source.write_text("第一幕原稿", encoding="utf-8")
    real_extract = source_module.extract_file

    def extract_then_change(path):
        result = real_extract(path)
        source.write_text("并发修改后的另一份稿件", encoding="utf-8")
        return result

    monkeypatch.setattr(source_module, "extract_file", extract_then_change)
    try:
        result = store.extract_source(project_id, str(source), "source_story")
    except BridgeError as error:
        assert error.code == "SOURCE_CHANGED"
        return
    backup = store.directory(project_id) / "source_inputs" / (result["hash"] + ".txt")
    assert file_hash(backup) == result["hash"]
    assert backup.read_text(encoding="utf-8") == result["records"][0]["text"]


def write_wav(path, sample):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(sample.to_bytes(2, "little", signed=True) * 80)


def test_asset_update_retains_old_bytes_and_checkpoint_version(managed_project, tmp_path):
    store, project_id = managed_project
    source = tmp_path / "对白 版本.wav"
    write_wav(source, 100)
    first = store.register_assets(project_id, 0, "voice-first", [{"asset_id": "voice_hero", "kind": "audio", "source_path": str(source)}])
    old_asset = store.load(project_id).assets["voice_hero"]
    snapshot = store.checkpoint(project_id, "旧语音")
    write_wav(source, -100)
    second = store.register_assets(project_id, 1, "voice-new", [{"asset_id": "voice_hero", "kind": "audio", "source_path": str(source)}])
    new_asset = Store(store.home).load(project_id).assets["voice_hero"]
    assert (old_asset.version, new_asset.version) == (1, 2)
    assert old_asset.sha256 != new_asset.sha256 and old_asset.path != new_asset.path
    assert file_hash(store.directory(project_id) / old_asset.path) == old_asset.sha256
    assert store.register_assets(project_id, 1, "voice-new", [{"asset_id": "voice_hero", "kind": "audio", "source_path": str(source)}]) == second
    restored = store.restore(project_id, snapshot["snapshot_id"], 2, "restore-voice")
    assert restored["revision"] == 3
    assert store.load(project_id).assets["voice_hero"] == old_asset
    assert Store(store.home).load(project_id, first["revision"]).assets["voice_hero"] == old_asset
    assert Store(store.home).load(project_id, second["revision"]).assets["voice_hero"] == new_asset


def test_restore_rejects_valid_but_changed_checkpoint_model_before_commit(managed_project):
    """A valid historical JSON edit must not silently replace checkpoint content."""
    store, project_id = managed_project
    initialize(store, project_id)
    snapshot = store.checkpoint(project_id, "已确认的旧文本")
    store.patch_frame(project_id, "opening", {"dialogue": {"text": "当前正确文本"}}, 1, "current-edit")
    head_path = store.directory(project_id) / "model/head.json"
    head_before = head_path.read_bytes()
    history = store.directory(project_id) / "history/revisions/00000001.json"
    changed = read_json(history)
    changed["frames"]["opening"]["dialogue"]["text"] = "历史文件被改写"
    storage_module.atomic_json(history, changed)

    with pytest.raises(BridgeError) as failure:
        Store(store.home).restore(project_id, snapshot["snapshot_id"], 2, "restore-modified-checkpoint")
    assert failure.value.code == "CHECKPOINT_HASH_MISMATCH"
    assert head_path.read_bytes() == head_before
    assert store.load(project_id).frames["opening"].dialogue.text == "当前正确文本"


@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_restore_rejects_unavailable_old_asset_before_commit(managed_project, tmp_path, damage):
    """Restoring a retained asset version requires its exact old managed bytes."""
    store, project_id = managed_project
    source = tmp_path / "historical-voice.wav"
    write_wav(source, 100)
    store.register_assets(project_id, 0, "old-audio", [{"asset_id": "voice", "kind": "audio", "source_path": str(source)}])
    old_asset = store.load(project_id).assets["voice"]
    snapshot = store.checkpoint(project_id, "旧素材版本")
    write_wav(source, 200)
    store.register_assets(project_id, 1, "new-audio", [{"asset_id": "voice", "kind": "audio", "source_path": str(source)}])
    head_path = store.directory(project_id) / "model/head.json"
    head_before = head_path.read_bytes()
    old_path = store.directory(project_id) / old_asset.path
    if damage == "missing":
        old_path.unlink()
    else:
        write_wav(old_path, -300)

    with pytest.raises(BridgeError) as failure:
        Store(store.home).restore(project_id, snapshot["snapshot_id"], 2, "restore-broken-media")
    assert failure.value.code == "CHECKPOINT_ASSET_MISMATCH"
    assert head_path.read_bytes() == head_before
    assert store.load(project_id).assets["voice"].sha256 != old_asset.sha256


def test_register_assets_rejects_damaged_existing_hash_container(managed_project, tmp_path):
    """Deduplication must check existing content instead of trusting its filename."""
    store, project_id = managed_project
    source = tmp_path / "original.wav"
    write_wav(source, 100)
    store.register_assets(project_id, 0, "first-audio", [{"asset_id": "original", "kind": "audio", "source_path": str(source)}])
    asset = store.load(project_id).assets["original"]
    write_wav(store.directory(project_id) / asset.path, -100)
    head_path = store.directory(project_id) / "model/head.json"
    head_before = head_path.read_bytes()

    with pytest.raises(BridgeError) as failure:
        store.register_assets(project_id, 1, "second-audio", [{"asset_id": "second", "kind": "audio", "source_path": str(source)}])
    assert failure.value.code == "ASSET_HASH_MISMATCH"
    assert head_path.read_bytes() == head_before
    assert "second" not in store.load(project_id).assets


def test_register_assets_detects_original_changing_before_copy(managed_project, tmp_path, monkeypatch):
    """Exercise the race between source hashing and the actual byte copy."""
    store, project_id = managed_project
    source = tmp_path / "changing.wav"
    write_wav(source, 100)
    real_copy = storage_module.shutil.copyfile

    def change_then_copy(original, target, *args, **kwargs):
        if Path(original) == source:
            write_wav(source, -100)
        return real_copy(original, target, *args, **kwargs)

    monkeypatch.setattr(storage_module.shutil, "copyfile", change_then_copy)
    with pytest.raises(BridgeError) as failure:
        store.register_assets(project_id, 0, "changing-audio", [{"asset_id": "voice", "kind": "audio", "source_path": str(source)}])
    assert failure.value.code == "ASSET_CHANGED"
    assert store.load(project_id).revision == 0
    assert store.load(project_id).assets == {}
    assert list((store.directory(project_id) / "assets").iterdir()) == []


def test_mcp_model_conversion_preserves_full_frame_and_patch_contracts(tmp_path, monkeypatch):
    """Go through FastMCP's input conversion, not direct Python wrappers."""
    from renpy_bridge.server import mcp

    monkeypatch.setenv("RENPY_BRIDGE_HOME", str(tmp_path / "mcp-home"))

    async def call(name, arguments):
        content = await mcp.call_tool(name, arguments)
        result = json.loads(content[0].text)
        assert result["ok"], result
        return result

    async def scenario():
        created = await call("create_project", {"name": "MCP完整状态", "operation_id": "create"})
        project_id = created["project_id"]
        await call("apply_frame_batch", {
            "project_id": project_id, "frames": source_frames(),
            "acts": [{"act_id": "act_one"}], "characters": [{"character_id": "hero", "name": "小明"}],
            "expected_revision": 0, "operation_id": "initial",
        })
        await call("patch_frame", {
            "project_id": project_id, "frame_id": "opening", "patch": {"decorations": []},
            "expected_revision": 1, "operation_id": "clear-title",
        })
        inspected = await call("inspect_project", {"project_id": project_id, "section": "full"})
        frames = inspected["project"]["frames"]
        assert frames["opening"]["decorations"] == []
        assert frames["opening"]["dialogue"]["text"] == "第一句"
        assert frames["ending"]["decorations"] == []
        assert frames["ending"]["bg"] is None and frames["ending"]["characters"] == []
        plan = await call("plan_reimport", {
            "project_id": project_id, "source_id": "source_story", "incoming_frames": source_frames(),
        })
        assert not plan["changes"] and not plan["conflicts"]

    asyncio.run(scenario())


def test_large_persisted_cycle_with_exit_and_unreachable_frame_keeps_exact_diagnostics(managed_project):
    store, project_id = managed_project
    count = 5000
    ids = [f"frame_{index}" for index in range(count)]
    frames = [
        {
            "frame_id": frame_id, "act_id": "large_act", "order": index,
            "flow": {"kind": "next", "target_frame_id": ids[index + 1]} if index + 1 < count else {"kind": "end"},
        }
        for index, frame_id in enumerate(ids)
    ]
    middle = count // 2
    frames[middle]["flow"] = {
        "kind": "choices", "choices": [
            {"choice_id": "repeat", "text": "继续循环", "target_frame_id": ids[0]},
            {"choice_id": "exit", "text": "离开循环", "target_frame_id": ids[middle + 1]},
        ],
    }
    frames.append({"frame_id": "unreachable", "act_id": "large_act", "order": count, "flow": {"kind": "end"}})
    store.apply_batch(project_id, 0, "large-graph", frames, [{"act_id": "large_act"}])
    reopened = Store(store.home)
    report = analyze_flow(reopened.load(project_id))
    assert len(report["reachable"]) == count
    assert report["unreachable"] == ["unreachable"]
    assert report["without_exit"] == []
    assert report["ending_frames"] == [ids[-1], "unreachable"]
    assert report["cycles"] == [ids[:middle + 1] + [ids[0]]]
    reopened.patch_frame(project_id, ids[middle], {"dialogue": {"text": "循环处的本地修改"}}, 1, "edit-cycle")
    assert analyze_flow(Store(store.home).load(project_id)) == report


def test_mcp_unknown_flow_and_layout_fields_return_actionable_errors_without_committing(tmp_path, monkeypatch):
    from renpy_bridge.server import mcp

    monkeypatch.setenv("RENPY_BRIDGE_HOME", str(tmp_path / "mcp-invalid-home"))

    async def call(name, arguments):
        content = await mcp.call_tool(name, arguments)
        return json.loads(content[0].text)

    async def scenario():
        created = await call("create_project", {"name": "非法选项边界", "operation_id": "create"})
        project_id = created["project_id"]
        assert created["ok"]
        initialized = await call("apply_frame_batch", {
            "project_id": project_id, "frames": source_frames(), "acts": [{"act_id": "act_one"}],
            "expected_revision": 0, "operation_id": "initial",
        })
        assert initialized["ok"]
        bad_flow = await call("update_flow", {
            "project_id": project_id, "frame_id": "opening",
            "flow": {"kind": "next", "target_frame_id": "ending", "condition": "unplanned-expression"},
            "expected_revision": 1, "operation_id": "invalid-flow",
        })
        assert not bad_flow["ok"] and bad_flow["error"]["code"] == "INVALID_PATCH"
        bad_layout = await call("apply_layout", {
            "project_id": project_id, "scope": "project", "layout": {"unknown_setting": 0.4},
            "expected_revision": 1, "operation_id": "invalid-layout",
        })
        assert not bad_layout["ok"] and bad_layout["error"]["code"] == "INVALID_MODEL"
        inspected = await call("inspect_project", {"project_id": project_id, "section": "full"})
        assert inspected["project"]["revision"] == 1
        assert inspected["project"]["frames"]["opening"]["flow"]["target_frame_id"] == "ending"

    asyncio.run(scenario())
