"""Keep real old media identities through shortened names and repeated updates."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess

import pytest

from renpy_bridge.compatibility import RETAINED_INDEX, prepare_previous_release
from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import atomic_json, file_hash, read_json


PID = "project_updates"
SDK = "8.5.3.26051504"


def release(path: Path, revision: int, payload: bytes | None = None, *, legacy=False, indexed=True):
    path.mkdir(parents=True, exist_ok=True)
    assets = {}
    game_path = None
    sha256 = None
    if payload is not None:
        sha256 = hashlib.sha256(payload).hexdigest()
        game_path = "bridge_assets/" + (sha256 if legacy else sha256[:24]) + ".png"
        media = path / "game" / game_path
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(payload)
        if indexed:
            assets["sprite"] = {"game_path": game_path, "sha256": sha256}
    (path / "game").mkdir(exist_ok=True)
    (path / "game/story.rpyc").write_bytes(b"compiled statement identities " + str(revision).encode())
    (path / "game/bridge_testcases.rpyc").write_bytes(b"exclude test harness")
    atomic_json(path / "bridge-manifest.json", {"project_id": PID, "sdk_version": SDK, "revision": revision, "assets": assets})
    return game_path, sha256


def prepare(old, target):
    return prepare_previous_release(old, target, PID, SDK)


def test_two_consecutive_upgrades_keep_retired_short_assets_and_full_hashes(tmp_path):
    first, second, third = [tmp_path / name for name in ("first", "second", "third")]
    paths = [release(first, 1, b"old sprite bytes"), release(second, 2, b"middle sprite bytes"), release(third, 3, b"new sprite bytes")]
    prepare(first, second)
    # The current manifest knows only the second asset; the persisted index
    # is the only full-hash source for the retired first shortened filename.
    assert len(read_json(second / "bridge-manifest.json")["assets"]) == 1
    result = prepare(second, third)
    index = read_json(third / RETAINED_INDEX)
    assert index["project_id"] == PID and index["sdk_version"] == SDK
    assert index["assets"] == {path: {"sha256": sha256} for path, sha256 in paths}
    for path, sha256 in paths:
        assert len(Path(path).stem) == 24
        assert file_hash(third / "game" / path) == sha256
    assert len(result["retained_media"]) == 2
    assert (third / "old-game/story.rpyc").read_bytes() == (second / "game/story.rpyc").read_bytes()
    assert not (third / "old-game/bridge_testcases.rpyc").exists()
    assert result["save_compatibility_verified"] is False
    assert prepare(second, third)["retained_media"] == result["retained_media"]


def test_unindexed_legacy_full_hash_name_survives_transition_to_short_names(tmp_path):
    old, new = tmp_path / "legacy", tmp_path / "new"
    old_path, old_hash = release(old, 1, b"old full hash file", legacy=True, indexed=False)
    new_path, new_hash = release(new, 2, b"new shortened file")
    prepare(old, new)
    assert file_hash(new / "game" / old_path) == old_hash
    assert read_json(new / RETAINED_INDEX)["assets"] == {old_path: {"sha256": old_hash}, new_path: {"sha256": new_hash}}


@pytest.mark.parametrize("where", ["old", "existing-target"])
def test_changed_old_or_existing_target_media_is_rejected_before_copy(tmp_path, where):
    old, new = tmp_path / "old", tmp_path / "new"
    path, _ = release(old, 1, b"expected immutable bytes")
    release(new, 2)
    broken = (old if where == "old" else new) / "game" / path
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"changed bytes")
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "ASSET_HASH_MISMATCH"
    assert broken.read_bytes() == b"changed bytes"
    assert not (new / RETAINED_INDEX).exists()
    assert not (new / "old-game").exists()


def test_short_name_without_manifest_or_retained_hash_is_not_guessed(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    release(old, 1, b"unknown shortened bytes", indexed=False)
    release(new, 2)
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "COMPATIBILITY_ASSET_UNKNOWN"
    assert not (new / "old-game").exists()


def test_manifest_and_retained_index_cannot_assign_two_hashes_to_one_short_name(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    path, original = release(old, 1, b"real original bytes")
    release(new, 2)
    conflicting = original[:24] + ("0" if original[24] != "0" else "1") + original[25:]
    atomic_json(old / RETAINED_INDEX, {"schema_version": "1.0", "project_id": PID, "sdk_version": SDK,
                                     "assets": {path: {"sha256": conflicting}}})
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "ASSET_NAME_COLLISION"
    assert not (new / "old-game").exists()


@pytest.mark.parametrize("malicious_path", ["../outside.png", "bridge_assets/../outside.png", "C:/outside.png", "bridge_assets\\outside.png"])
def test_manifest_paths_are_contained_before_writes(tmp_path, malicious_path):
    old, new = tmp_path / "old", tmp_path / "new"
    _, sha256 = release(old, 1, b"valid bytes")
    release(new, 2)
    manifest = read_json(old / "bridge-manifest.json")
    manifest["assets"]["sprite"]["game_path"] = malicious_path
    atomic_json(old / "bridge-manifest.json", manifest)
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "INVALID_PATH"
    assert not (new / "old-game").exists()


def test_missing_retired_media_blocks_the_next_upgrade(tmp_path):
    old, middle, new = tmp_path / "old", tmp_path / "middle", tmp_path / "new"
    old_path, _ = release(old, 1, b"first retired asset")
    release(middle, 2, b"second active asset")
    release(new, 3, b"third active asset")
    prepare(old, middle)
    (middle / "game" / old_path).unlink()
    with pytest.raises(BridgeError) as failure:
        prepare(middle, new)
    assert failure.value.code == "MISSING_ASSET"
    assert not (new / "old-game").exists()


def test_target_current_asset_collision_is_rejected_before_overwriting(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    path, original = release(old, 1, b"old bytes")
    release(new, 2)
    conflicting = original[:24] + ("0" if original[24] != "0" else "1") + original[25:]
    manifest = read_json(new / "bridge-manifest.json")
    manifest["assets"] = {"new": {"game_path": path, "sha256": conflicting}}
    atomic_json(new / "bridge-manifest.json", manifest)
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "ASSET_NAME_COLLISION"
    assert not (new / "game" / path).exists()


def test_contained_destination_rejects_real_outside_symlink(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    path, _ = release(old, 1, b"expected old media")
    release(new, 2)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (new / "game/bridge_assets").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        if os.name != "nt":
            pytest.skip(f"Creating a real symlink is unavailable: {error}")
        junction = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "New-Item -ItemType Junction -Path $env:BRIDGE_COMPAT_LINK -Value $env:BRIDGE_COMPAT_OUTSIDE -ErrorAction Stop | Out-Null"],
            env=dict(os.environ, BRIDGE_COMPAT_LINK=str(new / "game/bridge_assets"), BRIDGE_COMPAT_OUTSIDE=str(outside)),
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if junction.returncode != 0:
            pytest.skip(f"Creating a real link/junction is unavailable: {junction.stderr}")
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "INVALID_PATH"
    assert list(outside.iterdir()) == []
    assert not (new / "old-game").exists()


def test_media_changed_during_copy_does_not_publish_bad_container_or_index(tmp_path, monkeypatch):
    import renpy_bridge.compatibility as compatibility

    old, new = tmp_path / "old", tmp_path / "new"
    path, _ = release(old, 1, b"bytes validated before copying")
    release(new, 2)
    real_copy = compatibility.shutil.copyfile

    def changed_copy(source, destination, *args, **kwargs):
        if Path(source) == old / "game" / path:
            Path(source).write_bytes(b"changed during actual copy")
        return real_copy(source, destination, *args, **kwargs)

    monkeypatch.setattr(compatibility.shutil, "copyfile", changed_copy)
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "ASSET_HASH_MISMATCH"
    assert not (new / "game" / path).exists()
    assert not (new / RETAINED_INDEX).exists()
    assert not list((new / "game/bridge_assets").glob("*.tmp"))


def test_retained_index_cannot_import_outside_file(tmp_path):
    old, new = tmp_path / "old", tmp_path / "new"
    release(old, 1)
    release(new, 2)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside content is not a managed asset")
    atomic_json(old / RETAINED_INDEX, {"schema_version": "1.0", "project_id": PID, "sdk_version": SDK,
                                     "assets": {"../outside.png": {"sha256": file_hash(outside)}}})
    with pytest.raises(BridgeError) as failure:
        prepare(old, new)
    assert failure.value.code == "INVALID_PATH"
    assert not (new / "old-game").exists()
    assert outside.read_bytes() == b"outside content is not a managed asset"
