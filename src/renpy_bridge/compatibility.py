"""Preserve prior compiler identities and immutable media for declared updates."""
from pathlib import Path, PurePosixPath
import os
import re
import shutil
import uuid

from .errors import BridgeError
from .storage import atomic_json, read_json, file_hash
from .derived_integrity import verify_derived_sources


RETAINED_INDEX = "bridge-retained-assets.json"


def _contained(root: Path, relative: str) -> Path:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise BridgeError("INVALID_PATH", "兼容产物路径越界", path=relative)
    return path


def _media_name(game_path: str) -> str:
    if not isinstance(game_path, str) or "\\" in game_path:
        raise BridgeError("INVALID_PATH", "素材清单路径不合法", path=game_path)
    relative = PurePosixPath(game_path)
    if relative.is_absolute() or relative.parts[:1] != ("bridge_assets",) or len(relative.parts) != 2:
        raise BridgeError("INVALID_PATH", "素材清单路径必须位于bridge_assets", path=game_path)
    name = relative.name
    if not re.fullmatch(r"(?:[0-9a-f]{24}|[0-9a-f]{64})\.[a-z0-9]{1,8}", name):
        raise BridgeError("INVALID_PATH", "素材文件名不是受管内容版本", path=game_path)
    return name


def _entry(game_path, value):
    name = _media_name(game_path)
    sha256 = value.get("sha256") if isinstance(value, dict) else None
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise BridgeError("COMPATIBILITY_ASSET_UNKNOWN", "素材清单缺少完整内容哈希", path=game_path)
    if not sha256.startswith(Path(name).stem):
        raise BridgeError("ASSET_HASH_MISMATCH", "素材文件名与完整内容哈希不符", path=game_path)
    return sha256


def _merge(index: dict, game_path: str, sha256: str):
    key = game_path.casefold()
    if key in index and index[key] != sha256:
        raise BridgeError("ASSET_NAME_COLLISION", "同一素材文件名对应不同完整哈希，不能覆盖", path=game_path)
    index[key] = sha256


def _read_manifest(root: Path, project_id: str, sdk_version: str):
    path = _contained(root, "bridge-manifest.json")
    if not path.is_file():
        raise BridgeError("COMPATIBILITY_BASELINE_MISSING", "可运行工程的版本清单缺失", path=str(path))
    manifest = read_json(path)
    if not isinstance(manifest, dict) or manifest.get("project_id") != project_id or manifest.get("sdk_version") != sdk_version:
        raise BridgeError("COMPATIBILITY_VERSION_MISMATCH", "编译工程的身份或SDK版本不同，需要单独兼容验收")
    assets = manifest.get("assets", {})
    if not isinstance(assets, dict):
        raise BridgeError("COMPATIBILITY_ASSET_UNKNOWN", "工程素材清单不是对象")
    index = {}
    for asset in assets.values():
        game_path = asset.get("game_path") if isinstance(asset, dict) else None
        _merge(index, game_path, _entry(game_path, asset))
    return manifest, index


def _read_retained(root: Path, project_id: str, sdk_version: str):
    path = _contained(root, RETAINED_INDEX)
    if not path.exists():
        return {}
    record = read_json(path)
    if (not isinstance(record, dict) or record.get("schema_version") != "1.0"
            or record.get("project_id") != project_id or record.get("sdk_version") != sdk_version
            or not isinstance(record.get("assets"), dict)):
        raise BridgeError("COMPATIBILITY_INDEX_INVALID", "保留素材索引的格式、身份或SDK不一致")
    return {game_path: _entry(game_path, value) for game_path, value in record["assets"].items()}


def _media_files(root: Path, known: dict):
    directory = _contained(root, "game/bridge_assets")
    files = {}
    if directory.exists():
        for path in sorted(directory.iterdir()):
            game_path = "bridge_assets/" + path.name
            _media_name(game_path)
            _contained(root, "game/" + game_path)
            if not path.is_file():
                raise BridgeError("INVALID_PATH", "受管素材必须是文件", path=str(path))
            sha256 = known.get(game_path)
            if sha256 is None:
                if len(path.stem) != 64:
                    raise BridgeError("COMPATIBILITY_ASSET_UNKNOWN", "旧短素材名缺少清单或保留索引，不能猜测完整哈希", path=game_path)
                sha256 = path.stem
                _merge(known, game_path, sha256)
            if file_hash(path) != sha256:
                raise BridgeError("ASSET_HASH_MISMATCH", "兼容素材内容与完整版本哈希不符", path=str(path))
            files[game_path] = path
    return files


def _require_files(root: Path, index: dict):
    for game_path, sha256 in index.items():
        path = _contained(root, "game/" + game_path)
        if not path.is_file():
            raise BridgeError("MISSING_ASSET", "兼容清单引用的素材文件缺失", path=game_path)
        if file_hash(path) != sha256:
            raise BridgeError("ASSET_HASH_MISMATCH", "兼容素材内容与完整版本哈希不符", path=str(path))


def _copy_checked(source: Path, destination: Path, sha256: str, *, immutable=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if immutable and destination.exists():
        if not destination.is_file() or file_hash(destination) != sha256:
            raise BridgeError("ASSET_HASH_MISMATCH", "目标已有素材与完整内容哈希不符，不能覆盖", path=str(destination))
        return
    temporary = destination.with_name("." + destination.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        shutil.copyfile(source, temporary)
        if file_hash(temporary) != sha256:
            raise BridgeError("ASSET_HASH_MISMATCH", "兼容文件在复制时发生变化", path=str(source))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _retain_minigames(previous: Path, target: Path, *, baseline_verified: bool) -> list[dict]:
    """Keep immutable old game packages before the new v2 baseline is frozen."""
    from .minigame_packages import validate_directory
    old_path = _contained(previous, 'game/bridge-minigames.json')
    new_path = _contained(target, 'game/bridge-minigames.json')
    if not old_path.is_file():
        return []
    if not new_path.is_file():
        raise BridgeError('MINIGAME_SAVE_BREAKING_CHANGE', '旧发行包含小游戏存档，移除全部调用需要明确迁移，当前发行不会覆盖上次可运行版本')
    if not baseline_verified:
        raise BridgeError('MINIGAME_BASELINE_UNVERIFIED', '旧HTML包没有完整性基准，不能推断旧代码可信')
    old, current = read_json(old_path), read_json(new_path)
    if old.get('project_id') != current.get('project_id') or old.get('save_namespace') != current.get('save_namespace'):
        raise BridgeError('MINIGAME_COMPATIBILITY_IDENTITY', '旧小游戏配置的作品或存档身份不一致')
    missing_instances = sorted(set(old.get('instances', {})) - set(current.get('instances', {})))
    missing_targets = set()
    for instance in old.get('instances', {}).values():
        targets = [branch['target_frame_id'] for branch in instance.get('result_branches', [])]
        targets += [instance.get(key) for key in ('cancel_target_frame_id', 'error_target_frame_id', 'fallback_target_frame_id')]
        for frame_id in targets:
            if frame_id and frame_id not in current.get('frame_labels', {}):
                missing_targets.add(frame_id)
    if missing_instances or missing_targets:
        raise BridgeError('MINIGAME_SAVE_BREAKING_CHANGE', '旧存档调用或结果目标已移除，当前发行需先制定迁移策略',
                          instance_ids=missing_instances, target_frame_ids=sorted(missing_targets))
    retained = dict(old.get('retained_games', {}))
    for game in old.get('minigames', {}).values():
        retained[game['package_digest']] = game
    validated = []
    for package_digest, game in sorted(retained.items()):
        if (not re.fullmatch(r'[0-9a-f]{64}', package_digest) or not isinstance(game, dict)
                or game.get('package_digest') != package_digest
                or game.get('package_root') != 'game/bridge_minigames/' + package_digest):
            raise BridgeError('MINIGAME_COMPATIBILITY_INVALID', '旧小游戏索引不合法')
        source = _contained(previous, game['package_root'])
        info = validate_directory(source)
        if info.digest != package_digest or info.manifest != game.get('manifest') or info.manifest['game_id'] != game.get('game_id'):
            raise BridgeError('MINIGAME_COMPATIBILITY_CHANGED', '旧小游戏包与不可变身份不一致')
        validated.append((game, info))
    # Complete validation precedes any target writes. Existing files never change.
    receipts = []
    for game, info in validated:
        destination = _contained(target, game['package_root'])
        for item in info.files:
            _copy_checked(info.root / item.path, destination / item.path, item.sha256, immutable=True)
        if validate_directory(destination).digest != info.digest:
            raise BridgeError('MINIGAME_COMPATIBILITY_CHANGED', '旧小游戏复制后哈希不一致')
        receipts.append({'game_id': game['game_id'], 'package_digest': info.digest, 'package_version': game['package_version'],
                         'package_root': game['package_root'], 'file_count': len(info.files)})
    if retained:
        current['retained_games'] = retained
        atomic_json(new_path, current)
    return receipts


def prepare_previous_release(previous: Path, target: Path, project_id: str, sdk_version: str) -> dict:
    previous, target = Path(previous), Path(target)
    if previous.resolve() == target.resolve():
        raise BridgeError("INVALID_PATH", "旧工程与目标工程不能是同一目录")
    derived_integrity = verify_derived_sources(previous, target / ".bridge-derived-checks")
    manifest, previous_index = _read_manifest(previous, project_id, sdk_version)
    _, target_index = _read_manifest(target, project_id, sdk_version)
    for game_path, sha256 in _read_retained(previous, project_id, sdk_version).items():
        _merge(previous_index, game_path, sha256)
    for game_path, sha256 in _read_retained(target, project_id, sdk_version).items():
        _merge(target_index, game_path, sha256)
    complete_index = dict(previous_index)
    for game_path, sha256 in target_index.items():
        _merge(complete_index, game_path, sha256)
    # Validate the complete input set before writing compiler identities/media.
    previous_files = _media_files(previous, previous_index)
    for game_path, sha256 in previous_index.items():
        _merge(complete_index, game_path, sha256)
    _require_files(previous, previous_index)
    _require_files(target, target_index)
    _media_files(target, complete_index)
    retained_minigames = _retain_minigames(previous, target, baseline_verified=derived_integrity['status'] == 'passed')
    compiler = []
    for path in sorted((previous / "game").rglob("*.rpyc")):
        relative = path.relative_to(previous / "game")
        _contained(previous, "game/" + relative.as_posix())
        if relative.name.startswith("bridge_test") or relative.name.startswith("bridge_interaction"):
            continue
        destination = _contained(target, "old-game/" + relative.as_posix())
        compiler.append((path, destination, file_hash(path)))
    copied = []
    for source, destination, sha256 in compiler:
        _copy_checked(source, destination, sha256)
        copied.append({"path": str(destination), "sha256": sha256})
    media = []
    for game_path, path in previous_files.items():
        destination = _contained(target, "game/" + game_path)
        sha256 = complete_index[game_path]
        _copy_checked(path, destination, sha256, immutable=True)
        media.append({"path": str(destination), "game_path": game_path, "sha256": sha256})
    index_path = _contained(target, RETAINED_INDEX)
    atomic_json(index_path, {"schema_version": "1.0", "project_id": project_id, "sdk_version": sdk_version,
                             "assets": {path: {"sha256": sha256} for path, sha256 in sorted(complete_index.items())}})
    return {"previous_revision": manifest["revision"], "previous_project": str(previous), "compiler_identity_files": copied,
            "retained_media": media, "retained_assets_index": str(index_path), "save_compatibility_verified": False,
            "retained_minigames": retained_minigames,
            "derived_integrity": derived_integrity,
            "scope": "same SDK, stable frame/label identities; arbitrary story deletion or SDK changes need explicit validation"}
