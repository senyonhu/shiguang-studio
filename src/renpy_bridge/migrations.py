"""Explicit data-only migration of a declared internal 0.9 fixture format.

0.9 was never an externally released bridge format. This fixture uses `screen`
instead of `canvas`, and `entry_frame` instead of `entry_frame_id`; all indexed
objects already have the stable identities and full-state semantics of 1.0.
No unsupported schema is guessed, and migration makes no save/SDK promise.
The Store must back up original bytes before applying a migration candidate.
"""

from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError

from .errors import BridgeError
from .models import Project


CURRENT_SCHEMA = "1.0"
MINIGAME_SCHEMA = "1.1"
INTERNAL_LEGACY_SCHEMA = "0.9"
_REQUIRED_LEGACY = {"project_id", "save_namespace", "revision", "screen", "entry_frame", "acts", "frames", "characters", "assets"}


def _validate(raw):
    try:
        return Project.model_validate(raw).model_dump(mode="json")
    except ValidationError as error:
        raise BridgeError("INVALID_MIGRATION_MODEL", "迁移候选未通过完整身份、类型与引用校验",
                          diagnostics=error.errors(include_context=False, include_input=False)) from error


def plan_migration(raw: dict, target_version: str = CURRENT_SCHEMA) -> dict:
    """Validate a migration candidate without editing input or filesystem.

    The response's requires_backup flag is an application precondition. An
    applied Store migration must create a new revision and retain original
    schema bytes, its hash and the previous SDK/adapter version records.
    """
    if target_version not in {CURRENT_SCHEMA, MINIGAME_SCHEMA}:
        raise BridgeError("UNSUPPORTED_SCHEMA", "不支持此迁移目标格式", target_schema=target_version)
    if not isinstance(raw, dict):
        raise BridgeError("INVALID_MIGRATION_MODEL", "迁移输入必须为项目JSON对象")
    source_schema = raw.get("schema_version")
    if not isinstance(source_schema, str) or source_schema not in {CURRENT_SCHEMA, MINIGAME_SCHEMA, INTERNAL_LEGACY_SCHEMA}:
        raise BridgeError("UNSUPPORTED_SCHEMA", "未知项目格式不能自动迁移", source_schema=source_schema)
    if source_schema == MINIGAME_SCHEMA and target_version == CURRENT_SCHEMA:
        raise BridgeError("UNSUPPORTED_SCHEMA", "不能隐式降级小游戏格式并丢弃新对象")
    candidate = deepcopy(raw)
    changes = []
    if source_schema == INTERNAL_LEGACY_SCHEMA:
        missing = sorted(_REQUIRED_LEGACY - set(candidate))
        if missing:
            raise BridgeError("MIGRATION_IDENTITY_REQUIRED", "内部0.9夹具必须提供原有身份、存档命名空间与结构字段；不能推测或新建身份", fields=missing)
        if "canvas" in candidate or "entry_frame_id" in candidate:
            raise BridgeError("AMBIGUOUS_MIGRATION_FIELDS", "内部0.9夹具混用了新旧字段，不能确定哪个值为准")
        candidate["canvas"] = candidate.pop("screen")
        candidate["entry_frame_id"] = candidate.pop("entry_frame")
        candidate["schema_version"] = CURRENT_SCHEMA
        changes = [
            {"field": "/schema_version", "before": INTERNAL_LEGACY_SCHEMA, "after": CURRENT_SCHEMA},
            {"from": "/screen", "to": "/canvas", "action": "rename"},
            {"from": "/entry_frame", "to": "/entry_frame_id", "action": "rename"},
        ]
    if target_version == MINIGAME_SCHEMA and candidate["schema_version"] == CURRENT_SCHEMA:
        candidate["schema_version"] = MINIGAME_SCHEMA
        candidate["variables"] = {}
        candidate["minigames"] = {}
        changes += [{"field": "/schema_version", "before": CURRENT_SCHEMA, "after": MINIGAME_SCHEMA},
                    {"field": "/variables", "action": "add", "after": {}},
                    {"field": "/minigames", "action": "add", "after": {}}]
    validated = _validate(candidate)
    return {
        "source_schema": source_schema, "target_schema": target_version,
        "requires_backup": source_schema != target_version, "candidate": validated,
        "changes": changes, "dry_run": True,
        "scope": ("declared internal 0.9 fixture only; this is not an externally released bridge format"
                  if source_schema == INTERNAL_LEGACY_SCHEMA or target_version == CURRENT_SCHEMA
                  else "explicit 1.0 to 1.1 data extension; runtime and player save compatibility require separate evidence"),
        "save_compatibility_verified": False,
        "sdk_compatibility_verified": False,
    }


def migrate_model(raw: dict, target_version: str = CURRENT_SCHEMA) -> dict:
    """Return a validated candidate only; persistence and backups belong to Store."""
    return plan_migration(raw, target_version)["candidate"]
