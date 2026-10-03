"""Preserve generated source baselines and refuse silent external .rpy changes.

This records bytes, not a second authoring model. It never merges a script back
into a project or updates an existing baseline to accept an external edit.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

from .errors import BridgeError
from .storage import atomic_json, file_hash, read_json


REGISTRY = ".bridge-derived-integrity/manifest.json"


def _regular(path: Path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise BridgeError("INVALID_PATH", "派生脚本基准不允许链接或重解析点", path=str(path))
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise BridgeError("INVALID_PATH", "派生脚本基准只允许普通文件或目录", path=str(path))
    return info


def _root(path: Path) -> Path:
    path = Path(path).absolute()
    for ancestor in reversed([path, *path.parents]):
        if ancestor.exists() or ancestor.is_symlink():
            _regular(ancestor)
    if not stat.S_ISDIR(_regular(path).st_mode):
        raise BridgeError("INVALID_PATH", "派生脚本根目录不是目录", path=str(path))
    return path.resolve()


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or any(c in value for c in ("\\", ":", "\x00"))
            or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise BridgeError("INVALID_PATH", "派生脚本路径必须是规范的工程相对路径", path=value)
    relative = PurePosixPath(value)
    if relative.is_absolute():
        raise BridgeError("INVALID_PATH", "派生脚本路径不能是绝对路径", path=value)
    return relative.as_posix()


def _path(root: Path, relative: str) -> Path:
    relative = _relative(relative)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.exists() or current.is_symlink():
            _regular(current)
    if not current.resolve().is_relative_to(root):
        raise BridgeError("INVALID_PATH", "派生脚本路径越出工程目录", path=relative)
    return current


def _directory(root: Path, relative: str) -> Path:
    path = _path(root, relative)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        current.mkdir(exist_ok=True)
        if not stat.S_ISDIR(_regular(current).st_mode):
            raise BridgeError("INVALID_PATH", "派生脚本证据目录不是普通目录", path=str(current))
    return path


def _scripts(root: Path, managed_roots: list[str] | None = None) -> dict[str, Path]:
    scripts, folded = {}, set()
    protected = [_relative(value) for value in (managed_roots or [])]
    for current, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(current) / name
            info = _regular(path)
            relative = path.relative_to(root).as_posix()
            _path(root, relative)
            if stat.S_ISREG(info.st_mode) and (path.suffix.casefold() == ".rpy" or
                    any(relative == value or relative.startswith(value + "/") for value in protected)):
                if relative.casefold() in folded:
                    raise BridgeError("INVALID_PATH", "派生脚本路径大小写冲突", path=relative)
                folded.add(relative.casefold())
                scripts[relative] = path
    return dict(sorted(scripts.items()))


def _blob(root: Path, relative: str, data: bytes) -> Path:
    path = _path(root, relative)
    _directory(root, PurePosixPath(relative).parent.as_posix())
    expected_hash = hashlib.sha256(data).hexdigest()
    if path.exists():
        if not path.is_file() or file_hash(path) != expected_hash:
            raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本不可变副本与原始哈希不一致", path=str(path))
    else:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if file_hash(path) != expected_hash:
            raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本副本保存后哈希不一致", path=str(path))
    return path


def _load(root: Path) -> dict | None:
    path = _path(root, REGISTRY)
    if not path.exists():
        return None
    try:
        record = read_json(path)
    except (ValueError, OSError) as error:
        raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本基准清单不能读取", path=str(path)) from error
    if not isinstance(record, dict) or record.get("schema_version") not in {"1", "2"} or not isinstance(record.get("sources"), dict):
        raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本基准清单格式不受支持", path=str(path))
    roots = record.get("managed_roots", [])
    if (not isinstance(roots, list) or any(not isinstance(value, str) for value in roots)
            or len(roots) != len(set(value.casefold() for value in roots))
            or (record["schema_version"] == "1" and roots)):
        raise BridgeError("DERIVED_BASELINE_INVALID", "派生文件保护目录声明不合法")
    for value in roots:
        _relative(value)
        if value.startswith(".bridge-derived-"):
            raise BridgeError("DERIVED_BASELINE_INVALID", "保护目录不能包括完整性证据本身")
    folded = set()
    for relative, item in record["sources"].items():
        _path(root, relative)
        covered = relative.casefold().endswith(".rpy") or any(relative == value or relative.startswith(value + "/") for value in roots)
        if not covered or relative.casefold() in folded:
            raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本基准身份不合法或重复", path=relative)
        folded.add(relative.casefold())
        if (not isinstance(item, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", "")))
                or type(item.get("bytes")) is not int or item["bytes"] < 0):
            raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本基准缺少完整哈希或大小", path=relative)
        expected_blob = ".bridge-derived-integrity/baselines/" + item["sha256"] + ".bin"
        if item.get("baseline_path") != expected_blob:
            raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本基准副本路径不合法", path=relative)
        baseline = _path(root, expected_blob)
        if not baseline.is_file() or baseline.stat().st_size != item["bytes"] or file_hash(baseline) != item["sha256"]:
            raise BridgeError("DERIVED_BASELINE_INVALID", "派生脚本原始副本缺失或被改变", path=relative, baseline_path=str(baseline))
    return record


def _differences(root: Path, record: dict, scripts: dict[str, Path]) -> list[dict]:
    differences = []
    for relative in sorted(record["sources"].keys() | scripts.keys()):
        expected = record["sources"].get(relative)
        actual = scripts.get(relative)
        actual_hash = file_hash(actual) if actual is not None else None
        if expected is None or actual is None or actual_hash != expected["sha256"]:
            differences.append({"path": relative, "kind": "added" if expected is None else "missing" if actual is None else "modified",
                                "expected_sha256": expected["sha256"] if expected else None,
                                "actual_sha256": actual_hash,
                                "expected_bytes": expected["bytes"] if expected else None,
                                "actual_bytes": actual.stat().st_size if actual is not None else None})
    return differences


def record_derived_sources(root: Path, *, controlled_paths: list[str] | None = None,
                           managed_roots: list[str] | None = None) -> dict:
    """Initial registration, or explicit registration of newly written owned scripts.

    Existing source baselines cannot be replaced through this function. A caller
    adding QA/build scripts must explicitly name only those new relative paths.
    """
    root = _root(root)
    record = _load(root)
    supplied_roots = [_relative(value) for value in (managed_roots or [])]
    if len(supplied_roots) != len(set(value.casefold() for value in supplied_roots)):
        raise BridgeError("INVALID_PATH", "派生文件保护目录不能重复")
    if any(value.startswith(".bridge-derived-") for value in supplied_roots):
        raise BridgeError("INVALID_PATH", "保护目录不能包括完整性证据本身")
    if record is None:
        if controlled_paths is not None:
            raise BridgeError("DERIVED_BASELINE_MISSING", "登记新增受控脚本前必须先有生成基准")
        record = {"schema_version": "2" if supplied_roots else "1", "sources": {}}
        if supplied_roots:
            record["managed_roots"] = supplied_roots
        scripts = _scripts(root, supplied_roots)
        selected = list(scripts)
    else:
        old_roots = record.get("managed_roots", [])
        if supplied_roots and supplied_roots != old_roots:
            # A v1 baseline cannot silently trust arbitrary files found later.
            # Regenerate from authoritative inputs for new protected directories.
            raise BridgeError("DERIVED_BASELINE_UPGRADE_REQUIRED", "旧基准不能自动接受新增宿主或HTML文件；请从受管内容重新生成")
        scripts = _scripts(root, old_roots)
        selected = [_relative(path) for path in (controlled_paths or [])]
        if any(not path.casefold().endswith('.rpy') for path in selected):
            raise BridgeError("INVALID_PATH", "只允许显式新增受控.rpy；宿主和HTML包必须重新完整生成")
        if len(selected) != len(set(selected)):
            raise BridgeError("INVALID_PATH", "受控脚本登记路径不能重复")
        changes = _differences(root, record, scripts)
        unexpected = [item for item in changes if item["kind"] != "added" or item["path"] not in selected]
        if unexpected:
            raise BridgeError("DERIVED_SOURCES_CHANGED", "不能重写原始基准来接受外部脚本改动", diagnostics=unexpected)
    for relative in selected:
        if relative not in scripts:
            raise BridgeError("INVALID_PATH", "受控登记必须指向工程内现存的.rpy文件", path=relative)
        if relative in record["sources"]:
            continue
        data = scripts[relative].read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        baseline_path = ".bridge-derived-integrity/baselines/" + sha256 + ".bin"
        _blob(root, baseline_path, data)
        record["sources"][relative] = {"sha256": sha256, "bytes": len(data), "baseline_path": baseline_path}
    if _differences(root, record, _scripts(root, record.get("managed_roots", []))):
        raise BridgeError("DERIVED_SOURCES_CHANGED", "脚本在原始基准登记期间发生变化")
    _directory(root, ".bridge-derived-integrity")
    manifest_path = _path(root, REGISTRY)
    atomic_json(manifest_path, record)
    return {"status": "passed", "manifest_path": str(manifest_path), "sha256": file_hash(manifest_path), "file_count": len(record["sources"])}


def verify_derived_sources(previous: Path, evidence_root: Path) -> dict:
    """Inspect a prior generated tree; preserve changed bytes before refusing use."""
    previous = _root(previous)
    record = _load(previous)
    scripts = _scripts(previous, record.get("managed_roots", []) if record else None)
    if record is None:
        return {"status": "unverified", "diagnostics": [{"code": "DERIVED_BASELINE_UNVERIFIED",
                "message": "旧工程未登记原始.rpy哈希；不能推断是否存在外部脚本修改", "project_path": str(previous)}]}
    changes = _differences(previous, record, scripts)
    result = {"status": "passed", "manifest_path": str(_path(previous, REGISTRY)), "file_count": len(record["sources"]), "diagnostics": []}
    if not changes:
        return result
    evidence_root = Path(evidence_root).absolute()
    candidate = evidence_root.resolve()
    if candidate == previous or candidate.is_relative_to(previous) or previous.is_relative_to(candidate):
        raise BridgeError("INVALID_PATH", "外部脚本检查证据必须与旧工程分离", path=str(evidence_root))
    if not evidence_root.exists():
        parent = _root(evidence_root.parent)
        evidence_root = _directory(parent, evidence_root.name)
    evidence_root = _root(evidence_root)
    if evidence_root == previous or evidence_root.is_relative_to(previous) or previous.is_relative_to(evidence_root):
        raise BridgeError("INVALID_PATH", "外部脚本检查证据必须与旧工程分离", path=str(evidence_root))
    run = _directory(evidence_root, uuid.uuid4().hex)
    for item in changes:
        relative = item["path"]
        if item["actual_sha256"] is not None:
            data = scripts[relative].read_bytes()
            if hashlib.sha256(data).hexdigest() != item["actual_sha256"]:
                raise BridgeError("DERIVED_SOURCES_CHANGED", "外部改动文件在保存证据期间再次改变", path=relative)
            item["actual_backup_path"] = str(_blob(run, "actual/" + item["actual_sha256"] + ".bin", data))
        if item["expected_sha256"] is not None:
            baseline = _path(previous, record["sources"][relative]["baseline_path"])
            item["baseline_backup_path"] = str(_blob(run, "baseline/" + item["expected_sha256"] + ".bin", baseline.read_bytes()))
    _blob(run, "registry/original.json", _path(previous, REGISTRY).read_bytes())
    report_path = run / "report.json"
    result.update(status="failed", previous_project=str(previous), diagnostics=changes,
                  report_path=str(report_path), previous_originals_modified=False, automatic_merge_performed=False)
    atomic_json(report_path, result)
    raise BridgeError("DERIVED_SOURCES_CHANGED", "可运行工程的派生.rpy被外部修改、增加或删除；已保留字节副本，停止兼容复制", **result)
