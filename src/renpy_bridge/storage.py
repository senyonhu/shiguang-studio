"""Versioned project storage, atomic commits, asset versions and staged import."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

from filelock import FileLock
from pydantic import ValidationError

from .environment import bridge_home
from .errors import BridgeError
from .models import Project, Frame, VariableDefinition, MinigameDefinition, analyze_flow, apply_frame_patch
from .media import ALLOWED_EXTENSIONS, validate_media


def now() -> str:
    return datetime.now().astimezone().isoformat()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def contained(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise BridgeError("INVALID_PATH", "路径越出受管项目目录", path=relative)
    return candidate


def validate_project(data: dict) -> Project:
    try:
        return Project.model_validate(data)
    except ValidationError as exc:
        raise BridgeError("INVALID_MODEL", "项目内容或引用不合法", diagnostics=exc.errors(include_context=False, include_input=False)) from exc


class Store:
    def __init__(self, home: Path | None = None):
        self.home = (home or bridge_home()).resolve()
        self.projects = self.home / "projects"
        self.projects.mkdir(parents=True, exist_ok=True)

    def directory(self, project_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", project_id):
            raise BridgeError("INVALID_ID", "项目编号不合法", project_id=project_id)
        return contained(self.projects, project_id)

    def head(self, project_id: str) -> dict:
        path = self.directory(project_id) / "model/head.json"
        if not path.exists():
            raise BridgeError("PROJECT_NOT_FOUND", "没有找到受管项目", project_id=project_id)
        head = read_json(path)
        if head.get("schema_version") not in {"1.0", "1.1"}:
            raise BridgeError("UNSUPPORTED_SCHEMA", "此版本不能写入该项目格式", schema=head.get("schema_version"))
        return head

    def load(self, project_id: str, revision: int | None = None) -> Project:
        head = self.head(project_id)
        if revision is not None and (revision < 0 or revision > head["revision"]):
            raise BridgeError("REVISION_NOT_FOUND", "该版本尚未提交或不存在", revision=revision)
        relative = head["model_path"] if revision is None else f"history/revisions/{revision:08d}.json"
        path = contained(self.directory(project_id), relative)
        if not path.exists():
            raise BridgeError("REVISION_NOT_FOUND", "内容版本不存在", revision=revision)
        return validate_project(read_json(path))

    def create(self, name: str, operation_id: str, width=1280, height=720) -> dict:
        if not operation_id:
            raise BridgeError("INVALID_OPERATION_ID", "创建需要非空operation_id")
        index_path = self.projects / "index.json"
        payload_hash = digest({"name": name, "width": width, "height": height})
        with FileLock(str(self.projects / ".index.lock")):
            index = read_json(index_path) if index_path.exists() else {"projects": {}, "operations": {}}
            if operation_id in index["operations"]:
                old = index["operations"][operation_id]
                if old["digest"] != payload_hash:
                    raise BridgeError("IDEMPOTENCY_CONFLICT", "重复操作编号对应不同请求")
                return old["result"]
            project_id = "p_" + digest({"home": str(self.home), "create_operation": operation_id})[:20]
            directory = self.directory(project_id)
            if (directory / "model/head.json").exists():
                head = self.head(project_id)
                if head.get("create_digest") != payload_hash:
                    raise BridgeError("IDEMPOTENCY_CONFLICT", "创建请求与此前候选不同")
                result = {"ok": True, "project_id": project_id, "revision": head["revision"], "path": str(directory)}
                index["projects"][project_id] = {"name": name, "created_at": head["updated_at"]}
                index["operations"][operation_id] = {"digest": payload_hash, "result": result}
                atomic_json(index_path, index)
                return result
            project = Project(project_id=project_id, name=name, save_namespace=project_id,
                              canvas={"width": width, "height": height})
            for folder in ["model", "history/revisions", "assets", "source_inputs", "imports", "checkpoints", "jobs", "generated", "releases"]:
                (directory / folder).mkdir(parents=True, exist_ok=True)
            model_path = "history/revisions/00000000.json"
            atomic_json(directory / model_path, project.model_dump(mode="json"))
            atomic_json(directory / "model/head.json", {"schema_version": "1.0", "revision": 0, "model_path": model_path,
                                                      "runnable_revision": None, "operations": {}, "updated_at": now(),
                                                      "create_digest": payload_hash})
            result = {"ok": True, "project_id": project_id, "revision": 0, "path": str(directory)}
            index["projects"][project_id] = {"name": name, "created_at": now()}
            index["operations"][operation_id] = {"digest": payload_hash, "result": result}
            atomic_json(index_path, index)
            return result

    def list_projects(self) -> dict:
        index = self.projects / "index.json"
        return {"ok": True, "projects": read_json(index)["projects"] if index.exists() else {}}

    def plan_migration(self, project_id: str, target_schema: str = "1.0") -> dict:
        """Read a declared legacy head without letting ordinary writes bypass it."""
        from .migrations import plan_migration
        directory = self.directory(project_id)
        head_path = directory / "model/head.json"
        if not head_path.is_file():
            raise BridgeError("PROJECT_NOT_FOUND", "没有找到受管项目", project_id=project_id)
        head = read_json(head_path)
        if not isinstance(head.get("schema_version"), str) or head.get("schema_version") not in {"0.9", "1.0", "1.1"}:
            raise BridgeError("UNSUPPORTED_SCHEMA", "未知项目格式不能自动迁移", schema=head.get("schema_version"))
        revision = head.get("revision")
        if type(revision) is not int or revision < 0 or not isinstance(head.get("operations"), dict):
            raise BridgeError("INVALID_MIGRATION_MODEL", "原项目提交指针不完整，不能推测原有历史")
        path = contained(directory, head["model_path"])
        raw = read_json(path)
        plan = plan_migration(raw, target_schema)
        candidate = plan["candidate"]
        if (candidate["project_id"] != project_id or candidate["revision"] != revision
                or raw["schema_version"] != head["schema_version"]):
            raise BridgeError("MIGRATION_IDENTITY_CONFLICT", "原有项目身份、版本与提交指针不一致")
        return {"ok": True, "project_id": project_id, "expected_revision": revision,
                "source_model_sha256": file_hash(path), **plan}

    def apply_migration(self, project_id: str, expected_revision: int, operation_id: str,
                        target_schema: str = "1.0") -> dict:
        """Back up exact original bytes, then atomically publish a new revision."""
        if not operation_id:
            raise BridgeError("INVALID_OPERATION_ID", "迁移需要非空operation_id")
        directory = self.directory(project_id)
        payload_hash = digest({"migration": target_schema, "expected_revision": expected_revision})
        with FileLock(str(directory / ".write.lock")):
            head_path = directory / "model/head.json"
            head = read_json(head_path) if head_path.is_file() else {}
            if operation_id in head.get("operations", {}):
                previous = head["operations"][operation_id]
                if previous["digest"] != payload_hash:
                    raise BridgeError("IDEMPOTENCY_CONFLICT", "重复迁移编号对应不同请求")
                return previous["result"]
            plan = self.plan_migration(project_id, target_schema)
            if plan["expected_revision"] != expected_revision:
                raise BridgeError("VERSION_CONFLICT", "项目已有更新，请重新规划迁移",
                                  actual_revision=plan["expected_revision"])
            if not plan["requires_backup"]:
                raise BridgeError("NO_MIGRATION_NEEDED", "项目已经采用目标格式，无需迁移")
            backup = directory / "checkpoints" / ("migration_" + digest(operation_id)[:20])
            backup.mkdir(parents=True, exist_ok=True)
            original = contained(directory, head["model_path"])
            # A failed backup/validation cannot change the authoritative pointer.
            shutil.copyfile(original, backup / "original-model.json")
            shutil.copyfile(head_path, backup / "original-head.json")
            if file_hash(backup / "original-model.json") != plan["source_model_sha256"]:
                raise BridgeError("MIGRATION_BACKUP_FAILED", "迁移备份与原文件哈希不一致")
            candidate = plan["candidate"]
            candidate["revision"] = expected_revision + 1
            project = validate_project(candidate)
            self.verify_minigames(project)
            relative = f"history/revisions/{project.revision:08d}.json"
            atomic_json(directory / relative, project.model_dump(mode="json"))
            result = {"ok": True, "project_id": project_id, "revision": project.revision,
                      "source_schema": plan["source_schema"], "target_schema": target_schema,
                      "backup_path": str(backup), "source_model_sha256": plan["source_model_sha256"],
                      "save_compatibility_verified": False, "scope": plan["scope"]}
            atomic_json(backup / "migration.json", {**result, "created_at": now(),
                                                    "sdk_lock": read_json(self.home / "runtimes/sdk-lock.json")
                                                    if (self.home / "runtimes/sdk-lock.json").is_file() else None})
            head.update(schema_version=target_schema, revision=project.revision, model_path=relative, updated_at=now())
            if plan["source_schema"] == "0.9":
                head["runnable_revision"] = None
                head.pop("runnable_project_path", None)
            head["operations"][operation_id] = {"digest": payload_hash, "result": result}
            atomic_json(head_path, head)
            return result

    def inspect(self, project_id: str, section="summary", offset=0, limit=50, object_id: str | None = None) -> dict:
        project = self.load(project_id)
        head = self.head(project_id)
        if section == "summary":
            return {"ok": True, "project_id": project_id, "name": project.name, "revision": project.revision,
                    "runnable_revision": head["runnable_revision"], "counts": {key: len(getattr(project, key)) for key in (["acts", "frames", "characters", "assets"] + (["variables", "minigames"] if project.schema_version == "1.1" else []))},
                    "entry_frame_id": project.entry_frame_id, "flow": analyze_flow(project),
                    "canvas": project.canvas.model_dump(), "layout": project.layout.model_dump()}
        if section == "checkpoints":
            if offset < 0 or not 1 <= limit <= 500:
                raise BridgeError("INVALID_PAGE", "分页范围不合法")
            directory = self.directory(project_id)
            records = []
            for path in contained(directory, "checkpoints").glob("snap_*.json"):
                snapshot = contained(directory, "checkpoints/" + path.name)
                try:
                    record = read_json(snapshot)
                except (OSError, ValueError) as exc:
                    raise BridgeError("SNAPSHOT_CORRUPT", "检查点记录无法读取", path=path.name) from exc
                if (not isinstance(record, dict) or not re.fullmatch(r"snap_[0-9a-f]{16}", str(record.get("snapshot_id", "")))
                        or snapshot.name != record["snapshot_id"] + ".json" or type(record.get("revision")) is not int
                        or not 0 <= record["revision"] <= project.revision
                        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("model_hash", "")))
                        or not isinstance(record.get("created_at"), str) or not isinstance(record.get("note"), str)):
                    raise BridgeError("SNAPSHOT_CORRUPT", "检查点记录格式不完整", path=path.name)
                records.append({key: record[key] for key in ("snapshot_id", "revision", "note", "created_at", "model_hash")})
            records.sort(key=lambda item: (item["created_at"], item["snapshot_id"]), reverse=True)
            if object_id:
                selected = next((record for record in records if record["snapshot_id"] == object_id), None)
                if selected is None:
                    raise BridgeError("SNAPSHOT_NOT_FOUND", "检查点不存在", snapshot_id=object_id)
                return {"ok": True, "revision": project.revision, "object": selected}
            return {"ok": True, "revision": project.revision, "total": len(records), "offset": offset,
                    "items": records[offset:offset + limit]}
        if section not in ["acts", "frames", "characters", "assets", "variables", "minigames", "full"]:
            raise BridgeError("INVALID_SECTION", "不支持的查询范围", section=section)
        if section == "full":
            return {"ok": True, "project": project.model_dump(mode="json"), "runnable_revision": head["runnable_revision"]}
        collection = getattr(project, section)
        if object_id:
            if object_id not in collection:
                raise BridgeError("OBJECT_NOT_FOUND", "指定对象不存在", object_id=object_id)
            return {"ok": True, "revision": project.revision, "object": collection[object_id].model_dump(mode="json")}
        if offset < 0 or not 1 <= limit <= 500:
            raise BridgeError("INVALID_PAGE", "分页范围不合法")
        values = list(collection.values())[offset:offset + limit]
        return {"ok": True, "revision": project.revision, "total": len(collection), "offset": offset,
                "items": [value.model_dump(mode="json") for value in values]}

    def mutate(self, project_id: str, expected_revision: int, operation_id: str,
               payload, change: Callable[[dict], dict | None]) -> dict:
        if not operation_id:
            raise BridgeError("INVALID_OPERATION_ID", "修改需要非空operation_id")
        directory = self.directory(project_id)
        with FileLock(str(directory / ".write.lock")):
            head = self.head(project_id)
            payload_hash = digest(payload)
            if operation_id in head["operations"]:
                old = head["operations"][operation_id]
                if old["digest"] != payload_hash:
                    raise BridgeError("IDEMPOTENCY_CONFLICT", "同一操作编号不能用于另一项修改")
                return old["result"]
            if head["revision"] != expected_revision:
                raise BridgeError("VERSION_CONFLICT", "项目已有更新，请重新读取后修改",
                                  expected_revision=expected_revision, actual_revision=head["revision"])
            model = self.load(project_id).model_dump(mode="json")
            extra = change(model) or {}
            model["revision"] = expected_revision + 1
            project = validate_project(model)
            self.verify_minigames(project)
            model_path = f"history/revisions/{project.revision:08d}.json"
            atomic_json(directory / model_path, project.model_dump(mode="json"))
            result = {"ok": True, "project_id": project_id, "revision": project.revision, **extra}
            head.update(schema_version=project.schema_version, revision=project.revision, model_path=model_path, updated_at=now())
            head["operations"][operation_id] = {"digest": payload_hash, "result": result}
            # This one atomic pointer is the commit point. Unreferenced staged files are harmless.
            atomic_json(directory / "model/head.json", head)
            return result

    def verify_minigames(self, project: Project) -> None:
        from .minigame_packages import validate_directory
        for game in project.minigames.values():
            self._require_formal_minigame(game.manifest)
            info = validate_directory(self.directory(project.project_id) / game.path)
            if info.digest != game.package_digest or info.manifest != game.manifest:
                raise BridgeError("MINIGAME_PACKAGE_HASH_MISMATCH", "项目小游戏内容版本或清单与登记不同", game_id=game.game_id)

    @staticmethod
    def _require_minigame_schema(model):
        if model.get("schema_version") != "1.1":
            raise BridgeError("MINIGAME_MIGRATION_REQUIRED", "先显式规划并迁移到 schema 1.1，再登记小游戏或变量")

    @staticmethod
    def _require_formal_minigame(manifest):
        if manifest.get("prototype", False):
            raise BridgeError("MINIGAME_PROTOTYPE_ONLY", "正式工程写入不能使用原型包；原型解析与实证须在独立验证模式执行")

    def _import_minigame(self, project_id, source_path, expected_digest=None, allow_prototype=False, provenance=None):
        from .minigame_packages import import_package
        if allow_prototype:
            raise BridgeError("MINIGAME_PROTOTYPE_ONLY", "正式工程写入不能启用原型包模式")
        directory = self.directory(project_id)
        info = import_package(Path(source_path).expanduser(), directory / "minigames", expected_digest=expected_digest, allow_prototype=allow_prototype)
        return MinigameDefinition(game_id=info.manifest["game_id"], package_digest=info.digest,
                                 package_version=info.manifest["version"], path="minigames/" + info.digest,
                                 manifest=info.manifest, provenance=provenance or {}).model_dump(mode="json")

    def register_minigame_package(self, project_id, expected_revision, operation_id, source_path,
                                  expected_digest=None, allow_prototype=False, provenance=None):
        payload = {"operation": "register_minigame_package", "source_path": str(Path(source_path).expanduser().absolute()),
                   "expected_digest": expected_digest, "allow_prototype": allow_prototype, "provenance": provenance or {}}
        def change(model):
            self._require_minigame_schema(model)
            game = self._import_minigame(project_id, source_path, expected_digest, allow_prototype, provenance)
            model["minigames"][game["game_id"]] = game
            return {"minigame": game}
        return self.mutate(project_id, expected_revision, operation_id, payload, change)

    def define_variables(self, project_id, expected_revision, operation_id, variables, remove_ids=None):
        payload = {"operation": "define_variables", "variables": variables, "remove_ids": remove_ids or []}
        def change(model):
            self._require_minigame_schema(model)
            try:
                definitions = [VariableDefinition.model_validate(value) for value in variables]
            except ValidationError as exc:
                raise BridgeError("INVALID_MODEL", "变量定义不合法", diagnostics=exc.errors(include_context=False, include_input=False)) from exc
            ids = [value.variable_id for value in definitions]
            if len(set(ids)) != len(ids) or len(set(remove_ids or [])) != len(remove_ids or []) or set(ids) & set(remove_ids or []):
                raise BridgeError("DUPLICATE_ID", "变量操作包含重复或同时新增删除的身份")
            for variable_id in remove_ids or []:
                if variable_id not in model["variables"]:
                    raise BridgeError("OBJECT_NOT_FOUND", "要删除的变量不存在", variable_id=variable_id)
                del model["variables"][variable_id]
            for value in definitions:
                model["variables"][value.variable_id] = value.model_dump(mode="json")
            return {"changed_variables": ids, "removed_variables": remove_ids or []}
        return self.mutate(project_id, expected_revision, operation_id, payload, change)

    @staticmethod
    def reindex(model: dict):
        for act in model["acts"].values():
            act["frame_ids"] = []
        frames = sorted(model["frames"].values(), key=lambda frame: frame.get("order", 0))
        for frame in frames:
            if frame["act_id"] in model["acts"]:
                model["acts"][frame["act_id"]]["frame_ids"].append(frame["frame_id"])

    def apply_batch(self, project_id: str, expected_revision: int, operation_id: str, frames: list[dict],
                    acts: list[dict] | None = None, characters: list[dict] | None = None,
                    entry_frame_id: str | None = None, variables: list[dict] | None = None,
                    minigames: list[dict] | None = None) -> dict:
        payload = {"frames": frames, "acts": acts or [], "characters": characters or [], "entry": entry_frame_id}
        if variables is not None or minigames is not None:
            payload.update(variables=variables or [], minigames=minigames or [])

        def change(model):
            collections = [("acts", "act_id"), ("characters", "character_id"), ("frames", "frame_id")]
            if "variables" in payload:
                self._require_minigame_schema(model)
                collections += [("variables", "variable_id"), ("minigames", "game_id")]
            for field, id_field in collections:
                values = payload[field]
                identities = [v[id_field] for v in values]
                if len(identities) != len(set(identities)):
                    raise BridgeError("DUPLICATE_ID", "同一批次包含重复身份", collection=field)
                for value in values:
                    model[field][value[id_field]] = value
            if entry_frame_id:
                model["entry_frame_id"] = entry_frame_id
            elif not model["entry_frame_id"] and frames:
                model["entry_frame_id"] = frames[0]["frame_id"]
            self.reindex(model)
            baselines = model["metadata"].setdefault("source_baselines", {})
            for frame in frames:
                source_id = frame.get("provenance", {}).get("source_id")
                if source_id:
                    baseline = baselines.setdefault(source_id, {"frames": {}})
                    baseline["frames"][frame["frame_id"]] = Frame.model_validate(frame).model_dump(mode="json")
            for collection, identity in [("variables", "variable_id"), ("minigames", "game_id")]:
                for value in payload.get(collection, []):
                    source_id = value.get("provenance", {}).get("source_id")
                    if source_id:
                        baseline = baselines.setdefault(source_id, {"frames": {}})
                        baseline.setdefault(collection, {})[value[identity]] = deepcopy(value)
            return {"changed_frames": [frame["frame_id"] for frame in frames]}
        return self.mutate(project_id, expected_revision, operation_id, payload, change)

    def register_assets(self, project_id: str, expected_revision: int, operation_id: str, assets: list[dict]) -> dict:
        directory = self.directory(project_id)
        imported = []
        ids = [item["asset_id"] for item in assets]
        if len(ids) != len(set(ids)):
            raise BridgeError("DUPLICATE_ID", "一次素材导入出现重复asset_id")
        for item in assets:
            source = Path(item["source_path"]).expanduser().resolve()
            if not source.is_file():
                raise BridgeError("MISSING_ASSET", "素材文件不存在", source_path=str(source))
            kind = item["kind"]
            suffix = source.suffix.lower()
            if suffix not in ALLOWED_EXTENSIONS.get(kind, ()):
                raise BridgeError("INVALID_ASSET_FORMAT", "素材种类或扩展名不支持", path=str(source), kind=kind)
            sha256 = file_hash(source)
            relative = f"assets/{sha256}{suffix}"
            target = contained(directory, relative)
            if not target.exists():
                temp = target.with_suffix(target.suffix + "." + uuid.uuid4().hex + ".tmp")
                shutil.copyfile(source, temp)
                if file_hash(temp) != sha256:
                    temp.unlink()
                    raise BridgeError("ASSET_CHANGED", "素材在复制时发生变化")
                os.replace(temp, target)
            if not target.is_file() or file_hash(target) != sha256:
                raise BridgeError("ASSET_HASH_MISMATCH", "已有内容文件与登记哈希不一致，不能静默复用或覆盖", path=relative)
            metadata = validate_media(kind, target, item.get("metadata", {}))
            imported.append({"asset_id": item["asset_id"], "kind": kind, "path": relative, "sha256": sha256,
                             "name": item.get("name", source.name), "metadata": metadata,
                             "provenance": {"source_path": str(source), **item.get("provenance", {})}})

        def change(model):
            for asset in imported:
                previous = model["assets"].get(asset["asset_id"])
                asset["version"] = previous["version"] + (previous["sha256"] != asset["sha256"]) if previous else 1
                model["assets"][asset["asset_id"]] = asset
            return {"assets": imported}
        return self.mutate(project_id, expected_revision, operation_id, {"operation": "register_assets", "assets": imported}, change)

    def patch_frame(self, project_id, frame_id, patch, expected_revision, operation_id):
        def change(model):
            if frame_id not in model["frames"]:
                raise BridgeError("FRAME_NOT_FOUND", "帧不存在", frame_id=frame_id)
            try:
                model["frames"][frame_id] = apply_frame_patch(Frame.model_validate(model["frames"][frame_id]), patch).model_dump(mode="json")
            except ValidationError as exc:
                raise BridgeError("INVALID_PATCH", "局部修改不合法", diagnostics=exc.errors(include_context=False, include_input=False)) from exc
            self.reindex(model)
            return {"changed_frames": [frame_id]}
        return self.mutate(project_id, expected_revision, operation_id, {"frame": frame_id, "patch": patch}, change)

    def layout(self, project_id, scope, layout, expected_revision, operation_id, frame_ids=None):
        def change(model):
            if scope == "project":
                model["layout"].update(layout)
                # Project slots are a bulk authoring decision, materialized
                # into current components so generation has no hidden state.
                if "character_slots" in layout:
                    for frame in model["frames"].values():
                        for component in frame["characters"]:
                            slot = layout["character_slots"].get(component["character_id"])
                            if slot is not None:
                                component["layout"].update(slot)
                return {"changed_frames": list(model["frames"])}
            targets = frame_ids or []
            for frame_id in targets:
                if frame_id not in model["frames"]:
                    raise BridgeError("FRAME_NOT_FOUND", "排版目标不存在", frame_id=frame_id)
                frame = model["frames"][frame_id]
                components = ([frame["bg"]] if frame.get("bg") else []) + frame.get("characters", []) + frame.get("decorations", [])
                for component_id, value in layout.items():
                    component = next((item for item in components if item["component_id"] == component_id), None)
                    if component is None:
                        raise BridgeError("COMPONENT_NOT_FOUND", "排版组件不存在", frame_id=frame_id, component_id=component_id)
                    component["layout"].update(value)
            return {"changed_frames": targets}
        return self.mutate(project_id, expected_revision, operation_id, {"scope": scope, "layout": layout, "frames": frame_ids}, change)

    def checkpoint(self, project_id: str, note: str) -> dict:
        project = self.load(project_id)
        self.verify_minigames(project)
        snapshot_id = "snap_" + uuid.uuid4().hex[:16]
        record = {"snapshot_id": snapshot_id, "revision": project.revision, "note": note, "created_at": now(),
                  "model_hash": digest(project.model_dump(mode="json"))}
        atomic_json(self.directory(project_id) / "checkpoints" / f"{snapshot_id}.json", record)
        return {"ok": True, **record}

    def restore(self, project_id, snapshot_id, expected_revision, operation_id):
        if not re.fullmatch(r"snap_[0-9a-f]{16}", snapshot_id):
            raise BridgeError("INVALID_ID", "检查点编号不合法")
        path = self.directory(project_id) / "checkpoints" / f"{snapshot_id}.json"
        if not path.exists():
            raise BridgeError("SNAPSHOT_NOT_FOUND", "检查点不存在")
        snapshot = read_json(path)
        previous = self.load(project_id, snapshot["revision"]).model_dump(mode="json")
        if digest(previous) != snapshot.get("model_hash"):
            raise BridgeError("CHECKPOINT_HASH_MISMATCH", "检查点历史内容与保存时的哈希不一致，恢复未提交", snapshot_id=snapshot_id)
        self.verify_minigames(validate_project(previous))
        for asset in previous["assets"].values():
            media = contained(self.directory(project_id), asset["path"])
            if not media.is_file() or file_hash(media) != asset["sha256"]:
                raise BridgeError("CHECKPOINT_ASSET_MISMATCH", "检查点素材缺失或内容哈希不一致，恢复未提交", asset_id=asset["asset_id"], path=asset["path"])

        def change(model):
            schema = model["schema_version"]
            model.clear()
            model.update(previous)
            if schema == "1.1" and model["schema_version"] == "1.0":
                from .migrations import migrate_model
                upgraded = migrate_model(model, "1.1")
                model.clear()
                model.update(upgraded)
            return {"restored_snapshot": snapshot_id}
        return self.mutate(project_id, expected_revision, operation_id, {"restore": snapshot_id}, change)

    def begin_import(self, project_id: str, expected_revision: int, frame_ids: list[str], operation_id: str,
                     game_ids: list[str] | None = None, variable_ids: list[str] | None = None) -> dict:
        session_id = "import_" + digest({"project": project_id, "operation": operation_id})[:20]
        path = self.directory(project_id) / "imports" / f"{session_id}.json"
        with FileLock(str(path) + ".lock"):
            request = {"revision": expected_revision, "frame_ids": frame_ids}
            if game_ids is not None or variable_ids is not None:
                request.update(game_ids=game_ids or [], variable_ids=variable_ids or [])
            request_digest = digest(request)
            if path.exists():
                session = read_json(path)
                if session["request_digest"] != request_digest:
                    raise BridgeError("IDEMPOTENCY_CONFLICT", "导入编号已用于另一份清单")
            else:
                model = self.load(project_id).model_dump(mode="json")
                if model["revision"] != expected_revision:
                    raise BridgeError("VERSION_CONFLICT", "建立候选前项目已有更新")
                if len(frame_ids) != len(set(frame_ids)):
                    raise BridgeError("DUPLICATE_ID", "导入身份清单重复")
                if "game_ids" in request:
                    self._require_minigame_schema(model)
                    for field in ("game_ids", "variable_ids"):
                        identities = request[field]
                        if len(identities) != len(set(identities)) or any(type(value) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value) for value in identities):
                            raise BridgeError("DUPLICATE_ID", "新对象身份清单不合法或重复", collection=field)
                session = {"session_id": session_id, "state": "staged", "expected_revision": expected_revision,
                           "frame_ids": frame_ids, "staged_frame_ids": [], "request_digest": request_digest, "model": model, "batches": {}, "created_at": now()}
                if "game_ids" in request:
                    session.update(game_ids=request["game_ids"], variable_ids=request["variable_ids"], staged_game_ids=[], staged_variable_ids=[])
                atomic_json(path, session)
        return {"ok": True, "import_session_id": session_id, "frame_ids": frame_ids, "state": session["state"]}

    def import_path(self, project_id, session_id):
        if not re.fullmatch(r"import_[0-9a-f]{20}", session_id):
            raise BridgeError("INVALID_ID", "导入会话编号不合法")
        path = self.directory(project_id) / "imports" / f"{session_id}.json"
        if not path.exists():
            raise BridgeError("IMPORT_NOT_FOUND", "导入候选不存在")
        return path

    def stage_import(self, project_id, session_id, batch_id, frames, acts=None, characters=None, entry_frame_id=None,
                     variables=None, minigames=None):
        path = self.import_path(project_id, session_id)
        payload = {"frames": frames, "acts": acts or [], "characters": characters or [], "entry": entry_frame_id}
        if variables is not None or minigames is not None:
            payload.update(variables=variables or [], minigames=minigames or [])
        with FileLock(str(path) + ".lock"):
            session = read_json(path)
            if session["state"] != "staged":
                raise BridgeError("IMPORT_CLOSED", "候选已经提交或取消")
            if batch_id in session["batches"]:
                if session["batches"][batch_id] != digest(payload):
                    raise BridgeError("IDEMPOTENCY_CONFLICT", "批次ID对应不同内容")
            else:
                collections = [("frames", "frame_id"), ("acts", "act_id"), ("characters", "character_id")]
                if "variables" in payload:
                    self._require_minigame_schema(session["model"])
                    collections += [("variables", "variable_id"), ("minigames", "game_id")]
                    for collection, identity, cls, declared in [("variables", "variable_id", VariableDefinition, "variable_ids"),
                                                                ("minigames", "game_id", MinigameDefinition, "game_ids")]:
                        for value in payload[collection]:
                            try:
                                value = cls.model_validate(value).model_dump(mode="json")
                            except ValidationError as exc:
                                raise BridgeError("INVALID_MODEL", "候选新对象格式不合法", diagnostics=exc.errors(include_context=False, include_input=False)) from exc
                            if collection == "minigames":
                                self._require_formal_minigame(value["manifest"])
                            if value[identity] not in session.get(declared, []):
                                raise BridgeError("INVALID_IMPORT_ID", "新对象未在候选身份清单声明", collection=collection, object_id=value[identity])
                for field, identifier in collections:
                    ids = [value[identifier] for value in payload[field]]
                    if len(ids) != len(set(ids)):
                        raise BridgeError("DUPLICATE_ID", "同一候选批次包含重复身份", collection=field)
                for frame in frames:
                    if frame["frame_id"] not in session["frame_ids"]:
                        raise BridgeError("INVALID_IMPORT_ID", "该帧不在导入身份清单", frame_id=frame["frame_id"])
                    try:
                        Frame.model_validate(frame)
                    except ValidationError as exc:
                        raise BridgeError("INVALID_MODEL", "候选帧格式不合法", diagnostics=exc.errors(include_context=False, include_input=False)) from exc
                for field, identifier in collections:
                    for value in payload[field]:
                        session["model"][field][value[identifier]] = value
                if entry_frame_id:
                    session["model"]["entry_frame_id"] = entry_frame_id
                session["batches"][batch_id] = digest(payload)
                session["staged_frame_ids"] = sorted(set(session.get("staged_frame_ids", [])) | {frame["frame_id"] for frame in frames})
                if "variables" in payload:
                    session["staged_variable_ids"] = sorted(set(session.get("staged_variable_ids", [])) | {value["variable_id"] for value in payload["variables"]})
                    session["staged_game_ids"] = sorted(set(session.get("staged_game_ids", [])) | {value["game_id"] for value in payload["minigames"]})
                atomic_json(path, session)
            staged = set(session.get("staged_frame_ids", []))
            return {"ok": True, "state": "staged", "staged_count": len(staged), "expected_count": len(session["frame_ids"]),
                    "is_committed": False}

    def stage_minigame_package(self, project_id, session_id, batch_id, source_path, expected_digest=None,
                               allow_prototype=False, provenance=None):
        path = self.import_path(project_id, session_id)
        payload = {"operation": "stage_minigame_package", "source_path": str(Path(source_path).expanduser().absolute()),
                   "expected_digest": expected_digest, "allow_prototype": allow_prototype, "provenance": provenance or {}}
        with FileLock(str(path) + ".lock"):
            session = read_json(path)
            if session["state"] != "staged":
                raise BridgeError("IMPORT_CLOSED", "候选已经提交或取消")
            self._require_minigame_schema(session["model"])
            if batch_id in session["batches"]:
                if session["batches"][batch_id] != digest(payload):
                    raise BridgeError("IDEMPOTENCY_CONFLICT", "小游戏批次编号对应不同请求")
                return session["package_batch_results"][batch_id]
            game = self._import_minigame(project_id, source_path, expected_digest, allow_prototype, provenance)
            if game["game_id"] not in session.get("game_ids", []):
                raise BridgeError("INVALID_IMPORT_ID", "小游戏没有在候选身份清单声明", game_id=game["game_id"])
            session["model"]["minigames"][game["game_id"]] = game
            session["staged_game_ids"] = sorted(set(session.get("staged_game_ids", [])) | {game["game_id"]})
            session["batches"][batch_id] = digest(payload)
            result = {"ok": True, "state": "staged", "is_committed": False, "minigame": game}
            session.setdefault("package_batch_results", {})[batch_id] = result
            atomic_json(path, session)
            return result

    def commit_import(self, project_id, session_id, expected_revision, operation_id):
        path = self.import_path(project_id, session_id)
        with FileLock(str(path) + ".lock"):
            session = read_json(path)
            if session["state"] == "committed":
                return session["result"]
            if session["state"] != "staged":
                raise BridgeError("IMPORT_CLOSED", "候选已取消")
            if session["expected_revision"] != expected_revision:
                raise BridgeError("VERSION_CONFLICT", "候选起始版本与期望版本不同")
            missing = set(session["frame_ids"]) - set(session.get("staged_frame_ids", []))
            if missing:
                raise BridgeError("IMPORT_INCOMPLETE", "仍有帧未暂存", missing_frame_ids=sorted(missing))
            for declared, staged in [("game_ids", "staged_game_ids"), ("variable_ids", "staged_variable_ids")]:
                missing = set(session.get(declared, [])) - set(session.get(staged, []))
                if missing:
                    raise BridgeError("IMPORT_INCOMPLETE", "仍有新对象未暂存", collection=declared, missing_ids=sorted(missing))

            def change(model):
                model.clear()
                model.update(session["model"])
                if not model["entry_frame_id"] and session["frame_ids"]:
                    model["entry_frame_id"] = session["frame_ids"][0]
                self.reindex(model)
                baselines = model["metadata"].setdefault("source_baselines", {})
                for frame_id in session["frame_ids"]:
                    frame = model["frames"][frame_id]
                    source_id = frame.get("provenance", {}).get("source_id")
                    if source_id:
                        baseline = baselines.setdefault(source_id, {"frames": {}})
                        baseline["frames"][frame_id] = Frame.model_validate(frame).model_dump(mode="json")
                for collection, identities in [("minigames", session.get("game_ids", [])), ("variables", session.get("variable_ids", []))]:
                    for identity in identities:
                        value = model[collection][identity]
                        source_id = value.get("provenance", {}).get("source_id")
                        if source_id:
                            baseline = baselines.setdefault(source_id, {"frames": {}})
                            baseline.setdefault(collection, {})[identity] = deepcopy(value)
                return {"import_session_id": session_id, "imported_frame_count": len(session["frame_ids"])}
            result = self.mutate(project_id, expected_revision, operation_id, {"session": session_id, "model_hash": digest(session["model"])}, change)
            session.update(state="committed", result=result)
            atomic_json(path, session)
            return result

    def discard_import(self, project_id, session_id):
        path = self.import_path(project_id, session_id)
        with FileLock(str(path) + ".lock"):
            session = read_json(path)
            if session["state"] == "committed":
                raise BridgeError("IMPORT_CLOSED", "已提交候选不能取消，请恢复检查点")
            session["state"] = "cancelled"
            atomic_json(path, session)
        return {"ok": True, "state": "cancelled"}

    def mark_runnable(self, project_id, revision, project_path=None):
        directory = self.directory(project_id)
        with FileLock(str(directory / ".write.lock")):
            head = self.head(project_id)
            if revision > head["revision"]:
                raise BridgeError("VERSION_CONFLICT", "不存在的版本不能验证")
            if head["runnable_revision"] is None or revision >= head["runnable_revision"]:
                head["runnable_revision"] = revision
                if project_path is not None:
                    artifact = Path(project_path).resolve()
                    if not artifact.is_relative_to(directory) or not artifact.is_dir():
                        raise BridgeError("INVALID_PATH", "可运行工程必须在本项目生成目录内")
                    head["runnable_project_path"] = artifact.relative_to(directory).as_posix()
                atomic_json(directory / "model/head.json", head)

    def extract_source(self, project_id, source_path, source_id=None, offset=0, limit=200):
        from .sources import extract_file, SourceError
        source = Path(source_path).expanduser().resolve()
        self.head(project_id)
        if offset < 0 or not 1 <= limit <= 1000:
            raise BridgeError("INVALID_PAGE", "提取结果分页范围不合法")
        source_id = source_id or "source_" + hashlib.sha256(str(source).casefold().encode()).hexdigest()[:20]
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", source_id):
            raise BridgeError("INVALID_ID", "材料来源编号不合法")
        directory = self.directory(project_id) / "source_inputs"
        snapshot = directory / (".snapshot-" + uuid.uuid4().hex + source.suffix.lower())
        extracted = False
        try:
            shutil.copyfile(source, snapshot)
            result = extract_file(snapshot)
            if file_hash(snapshot) != result["hash"]:
                raise BridgeError("SOURCE_CHANGED", "材料快照在提取时发生变化")
            extracted = True
        except SourceError as exc:
            raise BridgeError(exc.code, str(exc), source_path=str(source)) from exc
        except OSError as exc:
            raise BridgeError("SOURCE_READ_FAILED", "无法保存材料快照", source_path=str(source)) from exc
        finally:
            if not extracted and snapshot.exists():
                snapshot.unlink()
        result["source_path"] = str(source)
        original = directory / (result["hash"] + source.suffix.lower())
        if not original.exists():
            os.replace(snapshot, original)
        elif snapshot.exists():
            snapshot.unlink()
        record_path = directory / (source_id + "-" + result["hash"] + ".json")
        result["source_id"] = source_id
        atomic_json(record_path, result)
        records = result.pop("records")
        return {"ok": True, **result, "records": records[offset:offset+limit], "total_records": len(records),
                "offset": offset, "record_path": str(record_path)}

    def plan_reimport(self, project_id, source_id, incoming_frames, incoming_variables=None, incoming_minigames=None):
        from .reimport import compare_import
        project = self.load(project_id)
        baseline = project.metadata.get("source_baselines", {}).get(source_id)
        if baseline is None:
            raise BridgeError("IMPORT_BASELINE_NOT_FOUND", "没有该来源的导入基线，请首次导入时保存source_id")
        current = {"frames": {frame_id: frame.model_dump(mode="json") for frame_id, frame in project.frames.items()
                              if frame_id in baseline["frames"] or frame.provenance.get("source_id") == source_id}}
        identities = [frame.get("frame_id") for frame in incoming_frames]
        if len(identities) != len(set(identities)):
            raise BridgeError("DUPLICATE_ID", "重新导入包含重复帧身份")
        collisions = set(identities).intersection(set(project.frames) - set(current["frames"]))
        if collisions:
            raise BridgeError("IDENTITY_CONFLICT", "来源帧编号与其他内容冲突", frame_ids=sorted(collisions))
        normalized = {}
        for frame in incoming_frames:
            value = Frame.model_validate(frame).model_dump(mode="json")
            if "provenance" not in frame and frame["frame_id"] in baseline["frames"]:
                value["provenance"] = deepcopy(baseline["frames"][frame["frame_id"]].get("provenance", {}))
            value["provenance"]["source_id"] = source_id
            # Source omission retains its previous baseline decision. This
            # lets the three-way merger recognize a chat deletion or layout edit.
            raw_components = {item["component_id"]: item for item in
                              ([frame["bg"]] if frame.get("bg") else []) + frame.get("characters", []) + frame.get("decorations", [])}
            base_frame = baseline["frames"].get(value["frame_id"], {})
            base_components = {item["component_id"]: item for item in
                               ([base_frame["bg"]] if base_frame.get("bg") else []) + base_frame.get("characters", []) + base_frame.get("decorations", [])}
            for component in ([value["bg"]] if value.get("bg") else []) + value["characters"] + value["decorations"]:
                if "layout" not in raw_components[component["component_id"]] and component["component_id"] in base_components:
                    component["layout"] = deepcopy(base_components[component["component_id"]]["layout"])
            normalized[value["frame_id"]] = value
        incoming = {"frames": normalized}
        for collection, values, identity, cls in [("variables", incoming_variables, "variable_id", VariableDefinition),
                                                 ("minigames", incoming_minigames, "game_id", MinigameDefinition)]:
            if values is None and collection not in baseline:
                continue
            self._require_minigame_schema(project.model_dump(mode="json"))
            objects = getattr(project, collection)
            owned = {key: value.model_dump(mode="json") for key, value in objects.items()
                     if key in baseline.get(collection, {}) or value.provenance.get("source_id") == source_id}
            current[collection] = owned
            if values is None:
                incoming[collection] = deepcopy(baseline.get(collection, {}))
                continue
            normalized_objects = {}
            for raw in values:
                value = deepcopy(raw)
                if collection == "minigames" and "source_path" in value:
                    if set(value) - {"source_path", "expected_digest", "allow_prototype", "provenance", "game_id"}:
                        raise BridgeError("INVALID_MODEL", "小游戏导入请求含未知字段")
                    value = self._import_minigame(project_id, value["source_path"], value.get("expected_digest"),
                                                 value.get("allow_prototype", False), value.get("provenance"))
                    if raw.get("game_id") is not None and raw["game_id"] != value["game_id"]:
                        raise BridgeError("IDENTITY_CONFLICT", "小游戏导入身份与包不符")
                try:
                    item = cls.model_validate(value).model_dump(mode="json")
                except ValidationError as exc:
                    raise BridgeError("INVALID_MODEL", "重导入新对象不合法", diagnostics=exc.errors(include_context=False, include_input=False)) from exc
                if collection == "minigames":
                    self._require_formal_minigame(item["manifest"])
                key = item[identity]
                if key in normalized_objects:
                    raise BridgeError("DUPLICATE_ID", "重导入对象身份重复", collection=collection)
                if key in objects and key not in owned:
                    raise BridgeError("IDENTITY_CONFLICT", "重导入不能覆盖其他来源的对象", collection=collection, object_id=key)
                item["provenance"]["source_id"] = source_id
                normalized_objects[key] = item
            incoming[collection] = normalized_objects
        # Old baselines may have only frames; include new empty collections as
        # the common base so their first addition can merge field by field.
        comparison_base = deepcopy(baseline)
        for collection in ("variables", "minigames"):
            if collection in incoming:
                comparison_base.setdefault(collection, {})
        plan = compare_import(comparison_base, current, incoming)
        plan_id = "reimport_" + uuid.uuid4().hex[:20]
        record = {"plan_id": plan_id, "source_id": source_id, "expected_revision": project.revision,
                  "incoming": incoming, "plan": plan, "created_at": now()}
        atomic_json(self.directory(project_id)/"imports"/f"{plan_id}.json", record)
        return {"ok": True, "plan_id": plan_id, "expected_revision": project.revision,
                "changes": plan["changes"], "conflicts": plan["conflicts"]}

    def apply_reimport(self, project_id, plan_id, resolutions, expected_revision, operation_id):
        from .reimport import resolve_import
        if not re.fullmatch(r"reimport_[0-9a-f]{20}", plan_id):
            raise BridgeError("INVALID_ID", "导入计划编号不合法")
        path = self.directory(project_id)/"imports"/f"{plan_id}.json"
        if not path.exists():
            raise BridgeError("IMPORT_NOT_FOUND", "重新导入计划不存在")
        record = read_json(path)
        if record["expected_revision"] != expected_revision:
            raise BridgeError("VERSION_CONFLICT", "计划对应的版本与请求不同")
        plan = resolve_import(record["plan"], resolutions)
        if plan["conflicts"]:
            raise BridgeError("IMPORT_CONFLICT", "还有内容冲突需要具体选择", conflicts=plan["conflicts"])

        def change(model):
            baseline = model["metadata"]["source_baselines"][record["source_id"]]
            owned = set(baseline["frames"]) | {fid for fid, frame in model["frames"].items()
                     if frame.get("provenance", {}).get("source_id") == record["source_id"]}
            for frame_id in owned:
                model["frames"].pop(frame_id, None)
            model["frames"].update(plan["merged"]["frames"])
            for collection in ("variables", "minigames"):
                if collection in record["incoming"]:
                    owned_objects = set(baseline.get(collection, {})) | {key for key, value in model[collection].items()
                                     if value.get("provenance", {}).get("source_id") == record["source_id"]}
                    for key in owned_objects:
                        model[collection].pop(key, None)
                    model[collection].update(plan["merged"][collection])
            model["metadata"]["source_baselines"][record["source_id"]] = record["incoming"]
            self.reindex(model)
            return {"applied_plan": plan_id, "changes": plan["changes"]}
        return self.mutate(project_id, expected_revision, operation_id, {"plan": plan_id, "resolutions": resolutions}, change)
