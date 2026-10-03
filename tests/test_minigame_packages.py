"""Real files, broken archives, typed contracts, and failure-atomic immutable imports."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import zipfile

import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge import minigame_packages as packages


ROOT = Path(__file__).resolve().parents[1]


def write_manifest(directory, **changes):
    payload = []
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            data = path.read_bytes()
            payload.append({"path": path.relative_to(directory).as_posix(), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = {"format_version": "1.0", "protocol": "1.0", "game_id": "puzzle", "version": "1.0",
                "entry": "index.html", "inputs": {"difficulty": {"type": "integer", "minimum": 1, "maximum": 3, "default": 2}},
                "results": {"outcomes": ["win", "lose"], "fields": {"score": {"type": "integer", "minimum": 0, "maximum": 100}}},
                "save_capabilities": {"entry_restart": True, "checkpoint": False, "snapshot_version": None}, "files": payload,
                **changes}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


@pytest.fixture
def valid(tmp_path):
    directory = tmp_path / "来源 中文 包"
    directory.mkdir()
    (directory / "index.html").write_text('<html lang="zh-CN">真实小游戏</html>', encoding="utf-8")
    (directory / "数据").mkdir()
    (directory / "数据/level.txt").write_text("离线文本关卡", encoding="utf-8")
    write_manifest(directory)
    return directory


def archive_directory(directory, output):
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in directory.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(directory).as_posix())
    return output


def rejection(function, *args, code=None, **kwargs):
    with pytest.raises(BridgeError) as caught:
        function(*args, **kwargs)
    if code:
        assert caught.value.code == code
    return caught.value


def test_directory_zip_and_independent_utf8_hash_agree(valid, tmp_path):
    info = packages.validate_directory(valid)
    digest = hashlib.sha256()
    for path in sorted((item for item in valid.rglob("*") if item.is_file()), key=lambda item: item.relative_to(valid).as_posix().encode("utf-8")):
        digest.update(path.relative_to(valid).as_posix().encode("utf-8") + b"\0" + hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii") + b"\n")
    assert info.digest == digest.hexdigest()
    zipped = archive_directory(valid, tmp_path / "包.zip")
    assert packages.validate_zip(zipped).digest == info.digest
    assert len(info.files) == 3 and info.prototype is False
    # Changing only manifest bytes changes the outer digest without self-reference.
    write_manifest(valid, display_name="新的显示名称")
    assert packages.validate_directory(valid).digest != info.digest


@pytest.mark.parametrize("name", ["puzzle", "timed", "checkpoint"])
def test_h0_examples_require_explicit_prototype_mode_and_keep_frozen_hash(name):
    directory = ROOT / "examples/html_minigames" / name
    rejection(packages.validate_directory, directory, code="MINIGAME_PROTOTYPE_ONLY")
    info = packages.validate_directory(directory, allow_prototype=True)
    expected = json.loads((directory.parent / "prototype-digests.json").read_text(encoding="utf-8"))["packages"][name]
    assert info.digest == expected["package_hash"]
    assert info.as_dict()["status"] == "validated_prototype"


@pytest.mark.parametrize("change", [
    {"format_version": "99"}, {"protocol": "99"}, {"unknown": True}, {"game_id": "../id"},
    {"prototype": "true"}, {"inputs": {"flag": {"type": "boolean", "default": 1}}},
    {"inputs": {"x": {"type": "integer", "minimum": 5, "maximum": 1, "default": 2}}},
    {"inputs": {"x": {"type": "expression", "default": "raise RuntimeError()"}}},
    {"inputs": {"x": {"type": "string"}}},
    {"results": {"outcomes": ["error"], "fields": {}}},
    {"results": {"outcomes": ["win", "win"], "fields": {}}},
    {"save_capabilities": {"entry_restart": False, "checkpoint": False, "snapshot_version": None}},
    {"save_capabilities": {"entry_restart": True, "checkpoint": True, "snapshot_version": "1.0"}, "snapshot_schema": True},
    {"required_capabilities": ["shell_execute"]},
])
def test_invalid_contracts_fail_before_import(valid, tmp_path, change):
    write_manifest(valid, **change)
    rejection(packages.import_package, valid, tmp_path / "storage")
    assert not (tmp_path / "storage").exists()


def test_no_missing_extra_or_modified_resource_is_accepted(valid):
    path = valid / "数据/level.txt"
    original = path.read_bytes()
    path.write_bytes(b"changed")
    rejection(packages.validate_directory, valid, code="MINIGAME_PACKAGE_HASH_MISMATCH")
    path.unlink()
    rejection(packages.validate_directory, valid, code="MINIGAME_PACKAGE_FILES")
    path.write_bytes(original)
    (valid / "extra.txt").write_bytes(b"not registered")
    rejection(packages.validate_directory, valid, code="MINIGAME_PACKAGE_FILES")


@pytest.mark.parametrize("name", ["../escape.html", "/absolute.html", "C:/secret.html", "a\\b.html", "AUX.txt", "folder./a.html", "a:stream.html", "evil.exe"])
def test_zip_bad_paths_never_extract_outside(valid, tmp_path, name):
    zipped = archive_directory(valid, tmp_path / "bad.zip")
    with zipfile.ZipFile(zipped, "a") as archive:
        item = zipfile.ZipInfo("temporary.html")
        # Keep the actual bad ZIP filename; Windows ZipInfo would otherwise
        # normalize a backslash during its constructor and weaken the fixture.
        item.filename = name
        item.orig_filename = name
        archive.writestr(item, b"must never extract")
    rejection(packages.import_package, zipped, tmp_path / "storage", code="MINIGAME_PATH_INVALID")
    assert not (tmp_path / "storage").exists()
    assert not (tmp_path / "escape.html").exists()


def test_case_collision_and_file_directory_collision_rejected(valid, tmp_path):
    zipped = archive_directory(valid, tmp_path / "bad.zip")
    with zipfile.ZipFile(zipped, "a") as archive:
        archive.writestr("INDEX.html", "collision")
    rejection(packages.validate_zip, zipped, code="MINIGAME_ARCHIVE_INVALID")
    manifest = json.loads((valid / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] += [{"path": "index.html/data.json", "sha256": "0" * 64, "bytes": 0}]
    rejection(packages.validate_manifest, manifest, code="MINIGAME_PATH_INVALID")


def test_zip_symlink_and_duplicate_members_rejected(valid, tmp_path):
    zipped = archive_directory(valid, tmp_path / "link.zip")
    with zipfile.ZipFile(zipped, "a") as archive:
        link = zipfile.ZipInfo("link.html")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(link, "../outside")
    rejection(packages.validate_zip, zipped, code="MINIGAME_ARCHIVE_INVALID")
    zipped = archive_directory(valid, tmp_path / "duplicate.zip")
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(zipped, "a") as archive:
            archive.writestr("index.html", "duplicate")
    rejection(packages.validate_zip, zipped, code="MINIGAME_ARCHIVE_INVALID")


def test_hardlinks_do_not_become_packages(valid):
    os.link(valid / "index.html", valid / "hard.html")
    rejection(packages.validate_directory, valid, code="MINIGAME_PATH_INVALID")


def test_symlink_and_linked_ancestor_do_not_become_packages(valid, tmp_path):
    try:
        os.symlink(valid / "index.html", valid / "linked.html")
    except OSError as exc:
        pytest.skip(f"此系统未允许创建测试符号链接: {exc}")
    rejection(packages.validate_directory, valid, code="MINIGAME_PATH_INVALID")
    (valid / "linked.html").unlink()
    os.symlink(valid, tmp_path / "alias", target_is_directory=True)
    rejection(packages.validate_directory, tmp_path / "alias", code="MINIGAME_PATH_INVALID")


def test_windows_reparse_zip_attribute_rejected(valid, tmp_path):
    zipped = archive_directory(valid, tmp_path / "reparse.zip")
    with zipfile.ZipFile(zipped, "a") as archive:
        reparse = zipfile.ZipInfo("reparse.html")
        reparse.external_attr = 0x400
        archive.writestr(reparse, "untrusted")
    rejection(packages.validate_zip, zipped, code="MINIGAME_ARCHIVE_INVALID")


def test_size_count_and_zip_inflation_have_explicit_limits(valid, tmp_path):
    rejection(packages.validate_directory, valid, limits=replace(packages.DEFAULT_LIMITS, max_bytes=10), code="MINIGAME_PACKAGE_LIMIT")
    rejection(packages.validate_directory, valid, limits=replace(packages.DEFAULT_LIMITS, max_files=2), code="MINIGAME_PACKAGE_LIMIT")
    (valid / "zeros.txt").write_bytes(b"0" * 100000)
    write_manifest(valid)
    zipped = archive_directory(valid, tmp_path / "inflated.zip")
    rejection(packages.validate_zip, zipped, limits=replace(packages.DEFAULT_LIMITS, max_compression_ratio=10), code="MINIGAME_PACKAGE_LIMIT")


def test_duplicate_json_keys_and_non_finite_numbers_rejected(valid):
    (valid / "manifest.json").write_text('{"format_version":"1.0","format_version":"99"}', encoding="utf-8")
    rejection(packages.validate_directory, valid, code="MINIGAME_MANIFEST_INVALID")
    (valid / "manifest.json").write_text('{"number":NaN}', encoding="utf-8")
    rejection(packages.validate_directory, valid, code="MINIGAME_MANIFEST_INVALID")


def test_typed_inputs_results_and_snapshot_are_defensive_copies(valid):
    manifest = packages.validate_directory(valid).manifest
    assert packages.validate_inputs(manifest, {}) == {"difficulty": 2}
    rejection(packages.validate_inputs, manifest, {"difficulty": True}, code="MINIGAME_INPUT_INVALID")
    rejection(packages.validate_inputs, manifest, {"difficulty": 4}, code="MINIGAME_INPUT_INVALID")
    rejection(packages.validate_results, manifest, "error", {"score": 0}, code="MINIGAME_RESULT_INVALID")
    rejection(packages.validate_results, manifest, "win", {"score": 0, "jump": "secret"}, code="MINIGAME_RESULT_INVALID")
    rejection(packages.validate_results, manifest, "win", {"score": float("inf")}, code="MINIGAME_RESULT_INVALID")
    results = {"score": 40}
    output = packages.validate_results(manifest, "win", results)
    results["score"] = 100
    assert output["results"]["score"] == 40
    checkpoint = packages.validate_directory(ROOT / "examples/html_minigames/checkpoint", allow_prototype=True).manifest
    snapshot = {"snapshot_version": "1.0", "state": {"stage": 1, "sensor": "temperature", "wire": None, "calibration": 34, "mistakes": 0}}
    assert packages.validate_snapshot(checkpoint, snapshot) == snapshot
    rejection(packages.validate_snapshot, checkpoint, {**snapshot, "snapshot_version": "99"}, code="MINIGAME_SNAPSHOT_INVALID")
    rejection(packages.validate_snapshot, manifest, snapshot, code="MINIGAME_SNAPSHOT_INVALID")
    broken = copy.deepcopy(snapshot)
    broken["state"]["stage"] = True
    rejection(packages.validate_snapshot, checkpoint, broken, code="MINIGAME_SNAPSHOT_INVALID")


def test_nested_values_have_depth_and_size_limits():
    schema = {"type": "string"}
    rejection(packages.validate_typed_value, schema, "字" * 30000, code="MINIGAME_VALUE_INVALID")
    value = "leaf"
    nested = {"type": "string"}
    for _ in range(34):
        value = [value]
        nested = {"type": "array", "items": nested}
    rejection(packages.validate_type_schema, nested, code="MINIGAME_MANIFEST_INVALID")


@pytest.mark.parametrize("source_kind", ["directory", "zip"])
def test_import_is_complete_immutable_and_idempotent(valid, tmp_path, source_kind):
    source = valid if source_kind == "directory" else archive_directory(valid, tmp_path / "source.zip")
    before = {path.relative_to(valid).as_posix(): path.read_bytes() for path in valid.rglob("*") if path.is_file()}
    storage = tmp_path / "受管 包"
    imported = packages.import_package(source, storage)
    assert imported.root.name == imported.digest
    assert packages.validate_directory(imported.root).digest == imported.digest
    again = packages.import_package(source, storage, expected_digest=imported.digest)
    assert again.root == imported.root
    assert list(storage.iterdir()) == [imported.root]
    assert {path.relative_to(valid).as_posix(): path.read_bytes() for path in valid.rglob("*") if path.is_file()} == before
    (imported.root / "index.html").write_text("外部修改", encoding="utf-8")
    rejection(packages.import_package, source, storage, code="MINIGAME_PACKAGE_IMMUTABLE")
    assert (imported.root / "index.html").read_text(encoding="utf-8") == "外部修改"


def test_mismatched_request_digest_makes_no_destination(valid, tmp_path):
    rejection(packages.import_package, valid, tmp_path / "storage", expected_digest="0" * 64, code="MINIGAME_PACKAGE_HASH_MISMATCH")
    assert not (tmp_path / "storage").exists()


def test_failed_mid_copy_never_publishes_or_overwrites(valid, tmp_path, monkeypatch):
    original = packages._copy_stream
    count = 0
    def fault(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("injected disk full")
        original(*args)
    monkeypatch.setattr(packages, "_copy_stream", fault)
    storage = tmp_path / "storage"
    rejection(packages.import_package, valid, storage, code="MINIGAME_IMPORT_FAILED")
    assert not list(storage.iterdir())
    assert packages.validate_directory(valid).manifest["game_id"] == "puzzle"


def test_changed_source_and_existing_target_race_do_not_publish(valid, tmp_path, monkeypatch):
    publish = packages._publish_without_replace
    def race(stage, target):
        target.mkdir()
        (target / "existing.txt").write_text("do not replace", encoding="utf-8")
        publish(stage, target)
    monkeypatch.setattr(packages, "_publish_without_replace", race)
    storage = tmp_path / "storage"
    rejection(packages.import_package, valid, storage, code="MINIGAME_IMPORT_FAILED")
    directories = list(storage.iterdir())
    assert len(directories) == 1
    assert (directories[0] / "existing.txt").read_text(encoding="utf-8") == "do not replace"


def test_import_target_inside_source_is_rejected(valid):
    rejection(packages.import_package, valid, valid / "nested", code="MINIGAME_PATH_INVALID")
    assert not (valid / "nested").exists()


def test_source_modified_during_copy_cannot_publish(valid, tmp_path, monkeypatch):
    original = packages._copy_stream
    changed = False
    def mutate(*args):
        nonlocal changed
        original(*args)
        if not changed:
            changed = True
            (valid / "数据/level.txt").write_text("source changed during copy", encoding="utf-8")
    monkeypatch.setattr(packages, "_copy_stream", mutate)
    storage = tmp_path / "storage"
    rejection(packages.import_package, valid, storage)
    assert not list(storage.iterdir())
