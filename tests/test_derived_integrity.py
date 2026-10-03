"""Real source bytes, immutable baselines, external edits, and path boundaries."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from renpy_bridge.compatibility import prepare_previous_release
from renpy_bridge.derived_integrity import REGISTRY, record_derived_sources, verify_derived_sources
from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import atomic_json, file_hash, read_json


def tree(tmp_path):
    root = tmp_path / "generated"
    (root / "game").mkdir(parents=True)
    (root / "game/script.rpy").write_bytes('label start:\n    "原文"\n'.encode())
    (root / "game/screens.rpy").write_bytes(b"screen custom():\n    pass\n")
    return root


def all_bytes(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_original_bytes_and_hashes_are_saved_and_existing_baseline_cannot_be_replaced(tmp_path):
    root = tree(tmp_path)
    result = record_derived_sources(root)
    record = read_json(root / REGISTRY)
    assert result["file_count"] == 2 and file_hash(root / REGISTRY) == result["sha256"]
    original = (root / "game/script.rpy").read_bytes()
    entry = record["sources"]["game/script.rpy"]
    assert (root / entry["baseline_path"]).read_bytes() == original
    assert entry["sha256"] == hashlib.sha256(original).hexdigest()
    registry_bytes = (root / REGISTRY).read_bytes()
    (root / "game/script.rpy").write_bytes(b"label start:\n    pass\n")
    with pytest.raises(BridgeError) as failure:
        record_derived_sources(root, controlled_paths=["game/script.rpy"])
    assert failure.value.code == "DERIVED_SOURCES_CHANGED"
    assert (root / REGISTRY).read_bytes() == registry_bytes
    assert (root / entry["baseline_path"]).read_bytes() == original


@pytest.mark.parametrize("kind", ["modified", "added", "missing"])
def test_external_change_preserves_actual_and_baseline_bytes_without_changing_original(tmp_path, kind):
    root = tree(tmp_path)
    record_derived_sources(root)
    selected = root / ("game/new.rpy" if kind == "added" else "game/script.rpy")
    if kind == "missing":
        selected.unlink()
    else:
        selected.write_bytes(b"# external\nlabel example:\n    pass\n")
    before = all_bytes(root)
    with pytest.raises(BridgeError) as failure:
        verify_derived_sources(root, tmp_path / "evidence")
    assert failure.value.code == "DERIVED_SOURCES_CHANGED"
    details = failure.value.details
    assert details["status"] == "failed" and details["previous_originals_modified"] is False
    assert len(details["diagnostics"]) == 1
    difference = details["diagnostics"][0]
    assert difference["kind"] == kind
    if kind != "missing":
        assert Path(difference["actual_backup_path"]).read_bytes() == selected.read_bytes()
        assert file_hash(Path(difference["actual_backup_path"])) == difference["actual_sha256"]
    if kind != "added":
        assert file_hash(Path(difference["baseline_backup_path"])) == difference["expected_sha256"]
        assert "原文" in Path(difference["baseline_backup_path"]).read_text(encoding="utf-8")
    assert read_json(Path(details["report_path"])) == details
    assert all_bytes(root) == before


def test_controlled_qa_addition_is_explicit_and_later_changes_are_detected(tmp_path):
    root = tree(tmp_path)
    record_derived_sources(root)
    qa = root / "game/bridge_testcases.rpy"
    qa.write_bytes(b"testcase custom:\n    exit\n")
    with pytest.raises(BridgeError):
        record_derived_sources(root)
    result = record_derived_sources(root, controlled_paths=["game/bridge_testcases.rpy"])
    assert result["file_count"] == 3
    assert verify_derived_sources(root, tmp_path / "evidence")["status"] == "passed"
    assert not (tmp_path / "evidence").exists()
    qa.write_bytes(b"# external test injection\n")
    with pytest.raises(BridgeError) as failure:
        verify_derived_sources(root, tmp_path / "evidence")
    assert failure.value.details["diagnostics"][0]["path"] == "game/bridge_testcases.rpy"


def test_legacy_missing_baseline_is_explicitly_unverified_without_inventing_hashes(tmp_path):
    root = tree(tmp_path)
    before = all_bytes(root)
    result = verify_derived_sources(root, tmp_path / "evidence")
    assert result["status"] == "unverified"
    assert result["diagnostics"][0]["code"] == "DERIVED_BASELINE_UNVERIFIED"
    assert all_bytes(root) == before and not (tmp_path / "evidence").exists()


def test_corrupt_immutable_baseline_is_not_treated_as_a_valid_external_edit(tmp_path):
    root = tree(tmp_path)
    record_derived_sources(root)
    entry = read_json(root / REGISTRY)["sources"]["game/script.rpy"]
    (root / entry["baseline_path"]).write_bytes(b"corrupt baseline")
    with pytest.raises(BridgeError) as failure:
        verify_derived_sources(root, tmp_path / "evidence")
    assert failure.value.code == "DERIVED_BASELINE_INVALID"
    assert not (tmp_path / "evidence").exists()


def test_untrusted_baseline_pointer_is_rejected_without_reading_outside_bytes(tmp_path):
    root = tree(tmp_path)
    record_derived_sources(root)
    record = read_json(root / REGISTRY)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"not a baseline")
    record["sources"]["game/script.rpy"]["baseline_path"] = "../outside.bin"
    atomic_json(root / REGISTRY, record)
    with pytest.raises(BridgeError) as failure:
        verify_derived_sources(root, tmp_path / "evidence")
    assert failure.value.code == "DERIVED_BASELINE_INVALID"
    assert outside.read_bytes() == b"not a baseline" and not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("bad_path", ["../outside.rpy", "game/../outside.rpy", "C:/outside.rpy", "game\\outside.rpy"])
def test_manifest_source_paths_are_rejected_before_backup_or_outside_reads(tmp_path, bad_path):
    root = tree(tmp_path)
    record_derived_sources(root)
    record = read_json(root / REGISTRY)
    record["sources"][bad_path] = record["sources"].pop("game/script.rpy")
    atomic_json(root / REGISTRY, record)
    outside = tmp_path / "outside.rpy"
    outside.write_bytes(b"must remain untouched")
    with pytest.raises(BridgeError) as failure:
        verify_derived_sources(root, tmp_path / "evidence")
    assert failure.value.code == "INVALID_PATH"
    assert outside.read_bytes() == b"must remain untouched" and not (tmp_path / "evidence").exists()


def test_evidence_cannot_be_written_inside_previous_original_tree(tmp_path):
    root = tree(tmp_path)
    record_derived_sources(root)
    (root / "game/script.rpy").write_bytes(b"external edit")
    before = all_bytes(root)
    with pytest.raises(BridgeError) as failure:
        verify_derived_sources(root, root / "new-evidence")
    assert failure.value.code == "INVALID_PATH"
    assert all_bytes(root) == before and not (root / "new-evidence").exists()


def test_compatibility_refuses_external_script_change_before_copying_any_old_game(tmp_path):
    old = tree(tmp_path)
    new = tmp_path / "new"
    new.mkdir()
    atomic_json(old / "bridge-manifest.json", {"project_id": "project_probe", "sdk_version": "8.5.3", "revision": 1, "assets": {}})
    atomic_json(new / "bridge-manifest.json", {"project_id": "project_probe", "sdk_version": "8.5.3", "revision": 2, "assets": {}})
    (old / "game/script.rpyc").write_bytes(b"previous compiled identity")
    record_derived_sources(old)
    (old / "game/script.rpy").write_bytes(b"external source edit")
    before = all_bytes(old)
    with pytest.raises(BridgeError) as failure:
        prepare_previous_release(old, new, "project_probe", "8.5.3")
    assert failure.value.code == "DERIVED_SOURCES_CHANGED"
    assert Path(failure.value.details["report_path"]).is_file()
    assert not (new / "old-game").exists() and not (new / "bridge-retained-assets.json").exists()
    assert all_bytes(old) == before


def test_real_link_or_junction_is_rejected_before_reading_linked_script(tmp_path):
    root = tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "untrusted.rpy").write_bytes(b"linked outside bytes")
    link = root / "game/external"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                 "New-Item -ItemType Junction -Path $env:BRIDGE_DERIVED_LINK -Value $env:BRIDGE_DERIVED_OUTSIDE -ErrorAction Stop | Out-Null"],
                                env=dict(os.environ, BRIDGE_DERIVED_LINK=str(link), BRIDGE_DERIVED_OUTSIDE=str(outside)),
                                capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
    with pytest.raises(BridgeError) as failure:
        record_derived_sources(root)
    assert failure.value.code == "INVALID_PATH"
    assert not (root / REGISTRY).exists()
    assert (outside / "untrusted.rpy").read_bytes() == b"linked outside bytes"
