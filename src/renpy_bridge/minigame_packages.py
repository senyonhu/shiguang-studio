"""Typed, self-contained HTML package validation and immutable staging.

This module does not run HTML and does not certify browser, save, or release
behavior. Prototype manifests require an explicit opt-in and retain that mark.
The package digest includes manifest.json; its files list excludes itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import ctypes
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import unicodedata
import uuid
import zipfile

from .errors import BridgeError


@dataclass(frozen=True)
class PackageLimits:
    max_bytes: int = 256 * 1024 * 1024
    max_files: int = 4096
    max_manifest_bytes: int = 1024 * 1024
    max_snapshot_bytes: int = 64 * 1024
    max_depth: int = 32
    max_path_bytes: int = 1024
    max_compression_ratio: int = 200


DEFAULT_LIMITS = PackageLimits()
_ID = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_NATIVE = {".exe", ".dll", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".lnk", ".url", ".py", ".rpy", ".rpyc", ".sh"}
_KINDS = {"null", "boolean", "integer", "number", "string", "object", "array"}
_CAPABILITIES = {"local_content", "host_messages", "audio", "canvas", "wasm"}


def _error(code: str, message: str, **details):
    raise BridgeError(code, message, **details)


def _object(value, location: str, allowed: set[str], required: set[str] = frozenset()):
    if type(value) is not dict or any(type(key) is not str for key in value):
        _error("MINIGAME_MANIFEST_INVALID", "需要 JSON 对象", location=location)
    if set(value) - allowed or required - set(value):
        _error("MINIGAME_MANIFEST_INVALID", "清单包含未知字段或缺少必要字段", location=location,
               unknown=sorted(set(value) - allowed), missing=sorted(required - set(value)))


def _identifier(value, location: str):
    if type(value) is not str or not _ID.fullmatch(value) or value in {"__proto__", "constructor", "prototype"}:
        _error("MINIGAME_MANIFEST_INVALID", "稳定身份不合法", location=location)


def _finite(value) -> bool:
    return type(value) in {int, float} and (type(value) is int or math.isfinite(value))


def _json_bytes(value, *, limit: int, depth: int, code: str) -> bytes:
    def visit(item, level):
        if level > depth:
            _error(code, "JSON 超过深度限制", maximum_depth=depth)
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                _error(code, "JSON 对象键必须是文本")
            for child in item.values():
                visit(child, level + 1)
        elif type(item) is list:
            for child in item:
                visit(child, level + 1)
        elif item is not None and type(item) not in {bool, int, float, str}:
            _error(code, "数据不是 JSON 类型")
        elif type(item) is float and not math.isfinite(item):
            _error(code, "数值必须有限")
    visit(value, 0)
    try:
        data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError) as exc:
        _error(code, "JSON 无法编码", reason=str(exc))
    if len(data) > limit:
        _error(code, "JSON 超过尺寸限制", bytes=len(data), maximum_bytes=limit)
    return data


def validate_type_schema(schema: dict, *, limits: PackageLimits = DEFAULT_LIMITS, _depth: int = 0) -> dict:
    if _depth > limits.max_depth:
        _error("MINIGAME_MANIFEST_INVALID", "类型定义超过深度限制")
    allowed = {"type", "default", "enum", "description", "minimum", "maximum", "minLength", "maxLength",
               "properties", "required", "additionalProperties", "items", "minItems", "maxItems"}
    _object(schema, "type", allowed, {"type"})
    kind = schema["type"]
    kinds = kind if type(kind) is list else [kind]
    if (not kinds or any(type(item) is not str or item not in _KINDS for item in kinds)
            or len(set(kinds)) != len(kinds) or len(kinds) > 2
            or len(kinds) == 2 and "null" not in kinds):
        _error("MINIGAME_MANIFEST_INVALID", "类型不在受支持的有限集合内")
    primary = next((item for item in kinds if item != "null"), "null")
    specific = {"integer": {"minimum", "maximum"}, "number": {"minimum", "maximum"},
                "string": {"minLength", "maxLength"}, "object": {"properties", "required", "additionalProperties"},
                "array": {"items", "minItems", "maxItems"}}.get(primary, set())
    if set(schema) - {"type", "default", "enum", "description"} - specific:
        _error("MINIGAME_MANIFEST_INVALID", "类型约束不适用于这个类型", type=primary)
    if "description" in schema and (type(schema["description"]) is not str or len(schema["description"]) > 4096):
        _error("MINIGAME_MANIFEST_INVALID", "类型说明必须是有限文本")
    for minimum, maximum in (("minimum", "maximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
        for key in (minimum, maximum):
            if key in schema and (not _finite(schema[key]) or key not in {"minimum", "maximum"} and (type(schema[key]) is not int or schema[key] < 0)):
                _error("MINIGAME_MANIFEST_INVALID", "范围约束不合法", field=key)
        if minimum in schema and maximum in schema and schema[minimum] > schema[maximum]:
            _error("MINIGAME_MANIFEST_INVALID", "最小范围超过最大范围")
    if primary == "object":
        properties = schema.get("properties")
        if type(properties) is not dict or len(properties) > 256 or schema.get("additionalProperties", False) is not False:
            _error("MINIGAME_MANIFEST_INVALID", "对象必须声明有限字段并禁止额外字段")
        for name, child in properties.items():
            _identifier(name, "properties")
            validate_type_schema(child, limits=limits, _depth=_depth + 1)
        required = schema.get("required", [])
        if type(required) is not list or any(type(name) is not str or name not in properties for name in required) or len(set(required)) != len(required):
            _error("MINIGAME_MANIFEST_INVALID", "对象必填字段不合法")
    if primary == "array":
        if "items" not in schema:
            _error("MINIGAME_MANIFEST_INVALID", "数组需要元素类型")
        validate_type_schema(schema["items"], limits=limits, _depth=_depth + 1)
    if "enum" in schema:
        values = schema["enum"]
        if type(values) is not list or not values or len(values) > 256:
            _error("MINIGAME_MANIFEST_INVALID", "枚举需要有限且非空的值列表")
        plain = {key: value for key, value in schema.items() if key not in {"enum", "default"}}
        encoded = []
        for value in values:
            _validate_value(plain, value, "enum", limits, _depth)
            encoded.append(_json_bytes(value, limit=limits.max_snapshot_bytes, depth=limits.max_depth, code="MINIGAME_MANIFEST_INVALID"))
        if len(set(encoded)) != len(encoded):
            _error("MINIGAME_MANIFEST_INVALID", "枚举存在重复值")
    if "default" in schema:
        _validate_value(schema, schema["default"], "default", limits, _depth)
    return copy.deepcopy(schema)


def _validate_value(schema, value, location, limits, depth=0):
    if depth > limits.max_depth:
        _error("MINIGAME_VALUE_INVALID", "值超过深度限制", location=location)
    kinds = schema["type"] if type(schema["type"]) is list else [schema["type"]]
    matches = {"null": value is None, "boolean": type(value) is bool, "integer": type(value) is int,
               "number": _finite(value), "string": type(value) is str, "object": type(value) is dict,
               "array": type(value) is list}
    if not any(matches[kind] for kind in kinds):
        _error("MINIGAME_VALUE_INVALID", "值与声明的类型不符", location=location, expected=schema["type"])
    if value is not None and _finite(value):
        if "minimum" in schema and value < schema["minimum"] or "maximum" in schema and value > schema["maximum"]:
            _error("MINIGAME_VALUE_INVALID", "数值超出范围", location=location)
    if type(value) is str:
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", limits.max_snapshot_bytes):
            _error("MINIGAME_VALUE_INVALID", "文本长度超出范围", location=location)
    if type(value) is dict:
        properties = schema["properties"]
        if set(value) - set(properties) or set(schema.get("required", [])) - set(value):
            _error("MINIGAME_VALUE_INVALID", "对象字段缺失或多余", location=location)
        for name, child in value.items():
            _validate_value(properties[name], child, location + "/" + name, limits, depth + 1)
    if type(value) is list:
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 65536):
            _error("MINIGAME_VALUE_INVALID", "数组长度超出范围", location=location)
        for index, child in enumerate(value):
            _validate_value(schema["items"], child, location + "/" + str(index), limits, depth + 1)
    if "enum" in schema:
        def same(left, right):
            if _finite(left) and _finite(right):
                return left == right
            return type(left) is type(right) and left == right
        if not any(same(value, allowed) for allowed in schema["enum"]):
            _error("MINIGAME_VALUE_INVALID", "值不在枚举中", location=location)


def validate_typed_value(schema, value, *, limits: PackageLimits = DEFAULT_LIMITS):
    validate_type_schema(schema, limits=limits)
    _json_bytes(value, limit=limits.max_snapshot_bytes, depth=limits.max_depth, code="MINIGAME_VALUE_INVALID")
    _validate_value(schema, value, "value", limits)
    return copy.deepcopy(value)


def _relative(value, limits: PackageLimits):
    if (type(value) is not str or not value or "\\" in value or value.startswith("/")
            or any(ord(character) < 32 or character in ':<>"|?*' for character in value)
            or unicodedata.normalize("NFC", value) != value):
        _error("MINIGAME_PATH_INVALID", "包内路径必须是规范的相对 UTF-8 路径", path=value)
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError:
        _error("MINIGAME_PATH_INVALID", "包内路径无法编码为 UTF-8")
    if size > limits.max_path_bytes:
        _error("MINIGAME_PACKAGE_LIMIT", "包内路径超过限制", path=value)
    for part in value.split("/"):
        if part in {"", ".", ".."} or part.rstrip(" .") != part or part.split(".", 1)[0].casefold() in _RESERVED:
            _error("MINIGAME_PATH_INVALID", "包内路径含不安全或保留名称", path=value)
    if Path(value).suffix.casefold() in _NATIVE:
        _error("MINIGAME_PATH_INVALID", "HTML 资源包不能包含原生执行或引擎脚本", path=value)
    return value


def validate_manifest(data: dict, *, allow_prototype: bool = False, limits: PackageLimits = DEFAULT_LIMITS) -> dict:
    _json_bytes(data, limit=limits.max_manifest_bytes, depth=limits.max_depth, code="MINIGAME_MANIFEST_INVALID")
    allowed = {"format_version", "prototype", "game_id", "version", "entry", "protocol", "display_name", "inputs",
               "results", "save_capabilities", "snapshot_schema", "automation", "required_capabilities", "license", "files"}
    required = {"format_version", "game_id", "version", "entry", "protocol", "inputs", "results", "save_capabilities", "files"}
    _object(data, "manifest", allowed, required)
    if data["format_version"] != "1.0" or data["protocol"] != "1.0":
        _error("MINIGAME_UNSUPPORTED_FORMAT", "未知小游戏包格式或协议版本")
    if type(data.get("prototype", False)) is not bool:
        _error("MINIGAME_MANIFEST_INVALID", "prototype 必须是布尔值")
    if data.get("prototype", False) and not allow_prototype:
        _error("MINIGAME_PROTOTYPE_ONLY", "原型包尚不是正式发行能力，需要明确原型验证模式")
    _identifier(data["game_id"], "game_id")
    _identifier(data["version"], "version")
    for field in ("display_name", "license"):
        if field in data and (type(data[field]) is not str or not data[field].strip() or len(data[field]) > 20000):
            _error("MINIGAME_MANIFEST_INVALID", "清单说明必须是有限非空文本", field=field)
    inputs = data["inputs"]
    if type(inputs) is not dict or len(inputs) > 256:
        _error("MINIGAME_MANIFEST_INVALID", "输入字段必须有限")
    for name, schema in inputs.items():
        _identifier(name, "inputs")
        validate_type_schema(schema, limits=limits)
        if "default" not in schema:
            _error("MINIGAME_MANIFEST_INVALID", "每个输入需要有类型的默认值", field=name)
    results = data["results"]
    _object(results, "results", {"outcomes", "fields"}, {"outcomes", "fields"})
    outcomes = results["outcomes"]
    if type(outcomes) is not list or not outcomes or len(outcomes) > 64:
        _error("MINIGAME_MANIFEST_INVALID", "完成结果必须是有限非空枚举")
    for outcome in outcomes:
        _identifier(outcome, "outcomes")
        if outcome in {"cancel", "error", "timeout"}:
            _error("MINIGAME_MANIFEST_INVALID", "宿主系统结果不能声明为玩法完成结果")
    if len(set(outcomes)) != len(outcomes) or type(results["fields"]) is not dict or len(results["fields"]) > 256:
        _error("MINIGAME_MANIFEST_INVALID", "结果枚举重复或输出字段不合法")
    for name, schema in results["fields"].items():
        _identifier(name, "results")
        validate_type_schema(schema, limits=limits)
    saves = data["save_capabilities"]
    _object(saves, "save_capabilities", {"entry_restart", "checkpoint", "snapshot_version"}, {"entry_restart", "checkpoint", "snapshot_version"})
    if saves["entry_restart"] is not True or type(saves["checkpoint"]) is not bool:
        _error("MINIGAME_MANIFEST_INVALID", "入口重开为必备，检查点能力必须明确声明")
    if saves["checkpoint"]:
        _identifier(saves["snapshot_version"], "snapshot_version")
        if type(data.get("snapshot_schema")) is not dict or data["snapshot_schema"].get("type") != "object":
            _error("MINIGAME_MANIFEST_INVALID", "检查点能力需要对象快照类型")
        validate_type_schema(data["snapshot_schema"], limits=limits)
    elif saves["snapshot_version"] is not None or "snapshot_schema" in data:
        _error("MINIGAME_MANIFEST_INVALID", "不支持检查点的包不能声明快照")
    capabilities = data.get("required_capabilities", [])
    if type(capabilities) is not list or any(type(value) is not str or value not in _CAPABILITIES for value in capabilities) or len(set(capabilities)) != len(capabilities):
        _error("MINIGAME_MANIFEST_INVALID", "浏览器能力清单不合法")
    if "automation" in data:
        if type(data["automation"]) is not dict or len(data["automation"]) > 32 or any(type(key) is not str or type(value) is not str or len(value) > 4096 for key, value in data["automation"].items()):
            _error("MINIGAME_MANIFEST_INVALID", "自动化说明必须是有限文本映射")
    files = data["files"]
    if type(files) is not list or not files or len(files) + 1 > limits.max_files:
        _error("MINIGAME_PACKAGE_LIMIT", "资源清单为空或超过文件数量限制")
    known = set()
    total = 0
    for item in files:
        _object(item, "files", {"path", "sha256", "bytes"}, {"path", "sha256", "bytes"})
        relative = _relative(item["path"], limits)
        if relative.casefold() == "manifest.json" or relative.casefold() in known:
            _error("MINIGAME_MANIFEST_INVALID", "资源身份重复或包含清单自身", path=relative)
        known.add(relative.casefold())
        if type(item["sha256"]) is not str or not _HASH.fullmatch(item["sha256"]) or type(item["bytes"]) is not int or item["bytes"] < 0:
            _error("MINIGAME_MANIFEST_INVALID", "资源需要完整小写哈希和有效大小", path=relative)
        total += item["bytes"]
    if total > limits.max_bytes:
        _error("MINIGAME_PACKAGE_LIMIT", "声明的资源总大小超过限制")
    entry = _relative(data["entry"], limits)
    if Path(entry).suffix.casefold() not in {".html", ".htm"} or entry not in {item["path"] for item in files}:
        _error("MINIGAME_MANIFEST_INVALID", "入口必须是已登记的 HTML 文件")
    _identities((item["path"], False) for item in files)
    return copy.deepcopy(data)


normalise_manifest = validate_manifest
normalize_manifest = validate_manifest


def validate_inputs(manifest: dict, values: dict, *, limits: PackageLimits = DEFAULT_LIMITS) -> dict:
    schemas = manifest["inputs"]
    if type(values) is not dict or set(values) - set(schemas):
        _error("MINIGAME_INPUT_INVALID", "小游戏输入包含未声明字段")
    result = {name: copy.deepcopy(values[name] if name in values else schema["default"]) for name, schema in schemas.items()}
    _json_bytes(result, limit=limits.max_snapshot_bytes, depth=limits.max_depth, code="MINIGAME_INPUT_INVALID")
    try:
        for name, schema in schemas.items():
            _validate_value(schema, result[name], name, limits)
    except BridgeError as exc:
        _error("MINIGAME_INPUT_INVALID", exc.message, **exc.details)
    return result


def validate_results(manifest: dict, outcome: str, values: dict, *, limits: PackageLimits = DEFAULT_LIMITS) -> dict:
    if type(outcome) is not str or outcome not in manifest["results"]["outcomes"]:
        _error("MINIGAME_RESULT_INVALID", "玩法完成结果未声明")
    schemas = manifest["results"]["fields"]
    if type(values) is not dict or set(values) != set(schemas):
        _error("MINIGAME_RESULT_INVALID", "输出字段缺失或多余")
    _json_bytes(values, limit=limits.max_snapshot_bytes, depth=limits.max_depth, code="MINIGAME_RESULT_INVALID")
    try:
        for name, schema in schemas.items():
            _validate_value(schema, values[name], name, limits)
    except BridgeError as exc:
        _error("MINIGAME_RESULT_INVALID", exc.message, **exc.details)
    return copy.deepcopy({"outcome": outcome, "results": values})


def validate_snapshot(manifest: dict, snapshot: dict, *, limits: PackageLimits = DEFAULT_LIMITS) -> dict:
    if not manifest["save_capabilities"]["checkpoint"]:
        _error("MINIGAME_SNAPSHOT_INVALID", "该小游戏未提供检查点恢复")
    if type(snapshot) is not dict or set(snapshot) - {"snapshot_version", "state", "checkpoint_id"} or {"snapshot_version", "state"} - set(snapshot):
        _error("MINIGAME_SNAPSHOT_INVALID", "检查点格式不合法")
    if snapshot["snapshot_version"] != manifest["save_capabilities"]["snapshot_version"]:
        _error("MINIGAME_SNAPSHOT_INVALID", "检查点版本不匹配")
    _json_bytes(snapshot, limit=limits.max_snapshot_bytes, depth=limits.max_depth, code="MINIGAME_SNAPSHOT_INVALID")
    if "checkpoint_id" in snapshot:
        _identifier(snapshot["checkpoint_id"], "checkpoint_id")
    try:
        _validate_value(manifest["snapshot_schema"], snapshot["state"], "state", limits)
    except BridgeError as exc:
        _error("MINIGAME_SNAPSHOT_INVALID", exc.message, **exc.details)
    return copy.deepcopy(snapshot)


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class PackageInfo:
    root: Path
    manifest: dict
    digest: str
    files: tuple[FileRecord, ...]
    total_bytes: int
    archive: bool = False

    @property
    def prototype(self) -> bool:
        return self.manifest.get("prototype", False)

    def as_dict(self) -> dict:
        return {"path": str(self.root), "game_id": self.manifest["game_id"], "package_version": self.manifest["version"],
                "package_hash": self.digest, "prototype": self.prototype, "status": "validated_prototype" if self.prototype else "validated_package",
                "file_count": len(self.files), "total_bytes": self.total_bytes}


def tree_digest(files) -> str:
    """Digest already independently hashed files, including manifest.json."""
    digest = hashlib.sha256()
    for item in sorted(files, key=lambda value: value.path.encode("utf-8")):
        digest.update(item.path.encode("utf-8") + b"\0" + item.sha256.encode("ascii") + b"\n")
    return digest.hexdigest()


def _regular(path: Path, *, directory: bool | None = None):
    try:
        info = path.lstat()
    except OSError as exc:
        _error("MINIGAME_PATH_INVALID", "路径不存在或无法读取", path=str(path), reason=str(exc))
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        _error("MINIGAME_PATH_INVALID", "包不允许链接、硬链接或重解析点", path=str(path))
    wanted = stat.S_ISDIR(info.st_mode) if directory is True else stat.S_ISREG(info.st_mode) if directory is False else stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
    if not wanted:
        _error("MINIGAME_PATH_INVALID", "路径类型不是需要的常规文件或目录", path=str(path))
    return info


def _safe_root(path: Path, *, directory: bool | None):
    path = Path(path).absolute()
    for ancestor in reversed((path, *path.parents)):
        _regular(ancestor, directory=directory if ancestor == path else True)
    return path


def _hash_file(path: Path, maximum: int) -> FileRecord:
    before = _regular(path, directory=False)
    if before.st_size > maximum:
        _error("MINIGAME_PACKAGE_LIMIT", "文件超过包体限制", path=str(path))
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                _error("MINIGAME_PACKAGE_LIMIT", "读取的文件超过包体限制", path=str(path))
            digest.update(chunk)
    after = _regular(path, directory=False)
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size) or size != before.st_size:
        _error("MINIGAME_PACKAGE_CHANGED", "验证期间资源发生变化", path=str(path))
    return FileRecord("", digest.hexdigest(), size)


def _json_manifest(data: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                _error("MINIGAME_MANIFEST_INVALID", "JSON 包含重复键", key=key)
            result[key] = value
        return result
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: _error("MINIGAME_MANIFEST_INVALID", "JSON 包含非有限数值", value=value))
    except (UnicodeError, ValueError, RecursionError) as exc:
        _error("MINIGAME_MANIFEST_INVALID", "清单必须是有效 UTF-8 JSON", reason=str(exc))


def _identities(entries):
    identities = {}
    for relative, is_directory in entries:
        parts = relative.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            directory = index < len(parts) or is_directory
            folded = prefix.casefold()
            previous = identities.get(folded)
            if previous is not None and previous != (prefix, directory):
                _error("MINIGAME_PATH_INVALID", "包内路径大小写或目录身份冲突", path=relative)
            identities[folded] = (prefix, directory)


def _finish_info(root, records, manifest_bytes, *, allow_prototype, limits, archive=False):
    manifest = validate_manifest(_json_manifest(manifest_bytes), allow_prototype=allow_prototype, limits=limits)
    actual = {item.path: item for item in records if item.path != "manifest.json"}
    expected = {item["path"]: item for item in manifest["files"]}
    if set(actual) != set(expected):
        _error("MINIGAME_PACKAGE_FILES", "实际包文件与清单不一致", missing=sorted(set(expected) - set(actual)), extra=sorted(set(actual) - set(expected)))
    for relative, item in expected.items():
        if item["sha256"] != actual[relative].sha256 or item["bytes"] != actual[relative].bytes:
            _error("MINIGAME_PACKAGE_HASH_MISMATCH", "资源哈希或大小与清单不同", path=relative)
    total = sum(item.bytes for item in records)
    if total > limits.max_bytes or len(records) > limits.max_files:
        _error("MINIGAME_PACKAGE_LIMIT", "实际包超过大小或文件数限制")
    return PackageInfo(root, manifest, tree_digest(records), tuple(sorted(records, key=lambda item: item.path.encode("utf-8"))), total, archive)


def validate_directory(path: Path, *, allow_prototype: bool = False, limits: PackageLimits = DEFAULT_LIMITS) -> PackageInfo:
    root = _safe_root(path, directory=True)
    records, identities = [], []
    pending = [root]
    directories = 0
    total = 0
    while pending:
        directory = pending.pop()
        _safe_root(directory, directory=True)
        with os.scandir(directory) as iterator:
            for entry in iterator:
                item = Path(entry.path)
                info = _regular(item)
                relative = _relative(item.relative_to(root).as_posix(), limits)
                is_directory = stat.S_ISDIR(info.st_mode)
                identities.append((relative, is_directory))
                if is_directory:
                    directories += 1
                    if directories > limits.max_files * 2:
                        _error("MINIGAME_PACKAGE_LIMIT", "包目录数量超过限制")
                    pending.append(item)
                else:
                    record = _hash_file(item, min(limits.max_manifest_bytes, limits.max_bytes) if relative == "manifest.json" else limits.max_bytes)
                    records.append(FileRecord(relative, record.sha256, record.bytes))
                    total += record.bytes
                    if total > limits.max_bytes or len(records) > limits.max_files:
                        _error("MINIGAME_PACKAGE_LIMIT", "包超过大小或文件数限制")
    _identities(identities)
    manifest = root / "manifest.json"
    if not any(item.path == "manifest.json" for item in records):
        _error("MINIGAME_MANIFEST_INVALID", "包入口目录缺少 manifest.json")
    if _regular(manifest, directory=False).st_size > limits.max_manifest_bytes:
        _error("MINIGAME_PACKAGE_LIMIT", "清单文件过大")
    data = manifest.read_bytes()
    recorded_manifest = next(item for item in records if item.path == "manifest.json")
    if hashlib.sha256(data).hexdigest() != recorded_manifest.sha256:
        _error("MINIGAME_PACKAGE_CHANGED", "清单在验证期间变化")
    return _finish_info(root, records, data, allow_prototype=allow_prototype, limits=limits)


def _zip_entries(archive: zipfile.ZipFile, limits):
    records = archive.infolist()
    if len(records) > limits.max_files * 3:
        _error("MINIGAME_PACKAGE_LIMIT", "ZIP 条目数量超过限制")
    seen, identities, files = set(), [], []
    total = 0
    for item in records:
        raw_name = item.orig_filename[:-1] if item.is_dir() else item.orig_filename
        _relative(raw_name, limits)
        if item.orig_filename != item.filename:
            _error("MINIGAME_ARCHIVE_INVALID", "ZIP 名称含截断或空字符")
        name = item.filename[:-1] if item.is_dir() else item.filename
        relative = _relative(name, limits)
        mode = item.external_attr >> 16
        kind = stat.S_IFMT(mode)
        is_directory = item.is_dir()
        if (relative.casefold() in seen or kind not in {0, stat.S_IFREG, stat.S_IFDIR}
                or bool(kind == stat.S_IFDIR) != is_directory and kind != 0
                or item.external_attr & 0x400 or item.flag_bits & 1):
            _error("MINIGAME_ARCHIVE_INVALID", "ZIP 包含重复、链接、特殊或加密条目", path=relative)
        seen.add(relative.casefold())
        identities.append((relative, is_directory))
        if not is_directory:
            total += item.file_size
            if item.file_size > limits.max_bytes or item.file_size > max(1, item.compress_size) * limits.max_compression_ratio:
                _error("MINIGAME_PACKAGE_LIMIT", "ZIP 解压大小或压缩膨胀超过限制", path=relative)
            files.append((relative, item))
    _identities(identities)
    if total > limits.max_bytes or len(files) > limits.max_files:
        _error("MINIGAME_PACKAGE_LIMIT", "ZIP 解压总大小或文件数超过限制")
    return files


def validate_zip(path: Path, *, allow_prototype: bool = False, limits: PackageLimits = DEFAULT_LIMITS) -> PackageInfo:
    root = _safe_root(path, directory=False)
    if root.stat().st_size > limits.max_bytes:
        _error("MINIGAME_PACKAGE_LIMIT", "ZIP 文件过大")
    records, manifest_bytes = [], None
    try:
        with zipfile.ZipFile(root) as archive:
            for relative, item in _zip_entries(archive, limits):
                if relative == "manifest.json" and item.file_size > limits.max_manifest_bytes:
                    _error("MINIGAME_PACKAGE_LIMIT", "清单文件过大")
                digest, count, chunks = hashlib.sha256(), 0, []
                with archive.open(item) as stream:
                    while chunk := stream.read(1024 * 1024):
                        count += len(chunk)
                        if count > item.file_size or count > limits.max_bytes:
                            _error("MINIGAME_PACKAGE_LIMIT", "ZIP 实际解压大小超过声明")
                        digest.update(chunk)
                        if relative == "manifest.json":
                            chunks.append(chunk)
                if count != item.file_size:
                    _error("MINIGAME_ARCHIVE_INVALID", "ZIP 实际资源大小不符", path=relative)
                records.append(FileRecord(relative, digest.hexdigest(), count))
                if relative == "manifest.json":
                    manifest_bytes = b"".join(chunks)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError) as exc:
        _error("MINIGAME_ARCHIVE_INVALID", "ZIP 不能完整读取", reason=str(exc))
    if manifest_bytes is None:
        _error("MINIGAME_MANIFEST_INVALID", "ZIP 根目录缺少 manifest.json")
    return _finish_info(root, records, manifest_bytes, allow_prototype=allow_prototype, limits=limits, archive=True)


def _publish_without_replace(source: Path, destination: Path):
    if os.name == "nt":
        os.rename(source, destination)  # Windows rename refuses any existing destination.
    elif sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, "renameat2", None)
        if function is None:
            _error("MINIGAME_ATOMIC_UNSUPPORTED", "此平台没有可用的原子不覆盖目录提交")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(destination))
    else:
        _error("MINIGAME_ATOMIC_UNSUPPORTED", "尚未验证此平台的原子包导入")


def import_package(source: Path, destination_root: Path, *, expected_digest: str | None = None,
                   allow_prototype: bool = False, limits: PackageLimits = DEFAULT_LIMITS) -> PackageInfo:
    """Verify, copy to a private sibling stage, reverify and publish without overwrite."""
    source = _safe_root(source, directory=None)
    validator = validate_directory if source.is_dir() else validate_zip
    original = validator(source, allow_prototype=allow_prototype, limits=limits)
    if expected_digest is not None and (type(expected_digest) is not str or not _HASH.fullmatch(expected_digest) or expected_digest != original.digest):
        _error("MINIGAME_PACKAGE_HASH_MISMATCH", "包整体哈希与请求不同")
    destination_root = Path(destination_root).absolute()
    if source.is_dir() and destination_root.is_relative_to(source):
        _error("MINIGAME_PATH_INVALID", "导入目标不能位于来源包中")
    # Validate every existing ancestor before creating only the requested directory chain.
    for ancestor in reversed((destination_root, *destination_root.parents)):
        if ancestor.exists() or ancestor.is_symlink():
            _regular(ancestor, directory=True)
    destination_root.mkdir(parents=True, exist_ok=True)
    _safe_root(destination_root, directory=True)
    destination = destination_root / original.digest
    lock = destination_root / (".import-" + original.digest + ".lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _error("MINIGAME_IMPORT_BUSY", "同一资源包正在导入，请重试")
    os.close(descriptor)
    stage = destination_root / (".minigame-stage-" + uuid.uuid4().hex)
    try:
        if destination.exists() or destination.is_symlink():
            try:
                existing = validate_directory(destination, allow_prototype=allow_prototype, limits=limits)
            except BridgeError as exc:
                _error("MINIGAME_PACKAGE_IMMUTABLE", "已有内容版本不能重新登记或覆盖", path=str(destination), cause=exc.code)
            if existing.digest != original.digest:
                _error("MINIGAME_PACKAGE_IMMUTABLE", "已有内容版本损坏，拒绝覆盖", path=str(destination))
            return existing
        stage.mkdir()
        if original.archive:
            with zipfile.ZipFile(source) as archive:
                members = {relative: item for relative, item in _zip_entries(archive, limits)}
                for item in original.files:
                    target = stage / item.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(members[item.path]) as incoming, target.open("xb") as outgoing:
                        _copy_stream(incoming, outgoing, item, limits)
        else:
            for item in original.files:
                incoming = source / item.path
                _safe_root(incoming, directory=False)
                target = stage / item.path
                target.parent.mkdir(parents=True, exist_ok=True)
                with incoming.open("rb") as reader, target.open("xb") as writer:
                    _copy_stream(reader, writer, item, limits)
        verified = validate_directory(stage, allow_prototype=allow_prototype, limits=limits)
        current = validator(source, allow_prototype=allow_prototype, limits=limits)
        if verified.digest != original.digest or current.digest != original.digest:
            _error("MINIGAME_PACKAGE_CHANGED", "来源或暂存包在导入期间变化")
        _publish_without_replace(stage, destination)
        return PackageInfo(destination, verified.manifest, verified.digest, verified.files, verified.total_bytes)
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError, NotImplementedError) as exc:
        _error("MINIGAME_IMPORT_FAILED", "包导入未提交，已有内容未覆盖", reason=str(exc))
    finally:
        if stage.exists():
            # stage is created by this function and never follows caller-supplied links.
            if stage.is_symlink() or getattr(stage.lstat(), "st_file_attributes", 0) & 0x400:
                stage.unlink()
            else:
                shutil.rmtree(stage)
        lock.unlink(missing_ok=True)


def _copy_stream(incoming, outgoing, record, limits):
    digest, size = hashlib.sha256(), 0
    while chunk := incoming.read(1024 * 1024):
        size += len(chunk)
        if size > record.bytes or size > limits.max_bytes:
            _error("MINIGAME_PACKAGE_CHANGED", "来源文件在复制时超过已验证大小", path=record.path)
        outgoing.write(chunk)
        digest.update(chunk)
    outgoing.flush()
    os.fsync(outgoing.fileno())
    if size != record.bytes or digest.hexdigest() != record.sha256:
        _error("MINIGAME_PACKAGE_CHANGED", "来源文件在复制时变化", path=record.path)
