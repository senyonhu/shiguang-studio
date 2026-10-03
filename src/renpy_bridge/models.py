"""Versioned, data-only content models for the Ren'Py bridge.

Frames describe complete story-layer states. Patches must be merged into a
complete project and validated again; these models never interpret omitted
fields as inheritance from the preceding frame.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)


Identifier = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Color = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")]
NonEmptyText = Annotated[str, StringConstraints(min_length=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


def validate_managed_path(value: str) -> str:
    """Validate portable project-relative syntax, not filesystem containment.

    The storage layer must additionally resolve paths beneath the project root
    and reject symlinks/reparse points that lead outside it.
    """
    if not value or "\\" in value or "\x00" in value or ":" in value:
        raise ValueError("素材路径必须是使用 / 的项目内相对路径")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError("素材路径不能为绝对路径")
    parts = value.split("/")
    for part in parts:
        if part in ("", ".", "..") or part[-1:] in (" ", "."):
            raise ValueError("素材路径不能包含空段、.、.. 或末尾空格/点")
        if any(ord(character) < 32 or character in '<>"|?*' for character in part):
            raise ValueError("素材路径包含 Windows 不支持的字符")
        stem = part.split(".", 1)[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
            raise ValueError("素材路径包含 Windows 保留设备名称")
    return value


class Canvas(StrictModel):
    width: int = Field(default=1280, ge=320, le=7680)
    height: int = Field(default=720, ge=240, le=4320)


class Layout(StrictModel):
    """Coordinates/sizes are fractions of the canvas; anchors of the component."""

    x: float = Field(default=0.5, ge=-2.0, le=3.0)
    y: float = Field(default=1.0, ge=-2.0, le=3.0)
    anchor_x: float = Field(default=0.5, ge=0.0, le=1.0)
    anchor_y: float = Field(default=1.0, ge=0.0, le=1.0)
    width: float | None = Field(default=None, gt=0.0, le=4.0)
    height: float | None = Field(default=None, gt=0.0, le=4.0)
    fit: Literal["contain", "cover"] = "contain"


class LayoutProfile(StrictModel):
    safe_margin: float = Field(default=0.04, ge=0.0, le=0.25)
    dialogue_height: float = Field(default=0.24, gt=0.0, le=0.6)
    font_size: int = Field(default=32, ge=12, le=128)
    min_font_size: int = Field(default=20, ge=12, le=128)
    character_slots: dict[Identifier, Layout] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_font_bounds(self) -> LayoutProfile:
        if self.min_font_size > self.font_size:
            raise ValueError("最小字号不能大于默认字号")
        return self


class Character(StrictModel):
    character_id: Identifier
    name: NonEmptyText
    color: Color = "#ffffff"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class Asset(StrictModel):
    asset_id: Identifier
    kind: Literal["image", "audio", "font"]
    path: str
    sha256: Sha256
    version: int = Field(default=1, ge=1)
    name: str = ""
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        return validate_managed_path(value)


class Component(StrictModel):
    component_id: Identifier
    kind: Literal["background", "character", "image", "text"]
    asset_id: Identifier | None = None
    text: str | None = None
    character_id: Identifier | None = None
    layout: Layout = Field(default_factory=Layout)
    zorder: int = Field(default=0, ge=-1000, le=1000)
    font_size: int = Field(default=32, ge=12, le=128)
    color: Color = "#ffffff"
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def default_layout_for_kind(cls, value: Any) -> Any:
        if isinstance(value, dict) and "layout" not in value:
            value = dict(value)
            if value.get("kind") == "background":
                value["layout"] = {
                    "x": 0.5, "y": 0.5, "anchor_x": 0.5, "anchor_y": 0.5,
                    "width": 1.0, "height": 1.0, "fit": "cover",
                }
            elif value.get("kind") == "character":
                value["layout"] = {"height": 0.85}
            elif value.get("kind") == "text":
                value["layout"] = {"y": 0.1, "anchor_y": 0.0, "width": 0.9}
        return value

    @model_validator(mode="after")
    def check_content(self) -> Component:
        if self.kind == "text":
            if self.text is None or self.asset_id is not None or self.character_id is not None:
                raise ValueError("文字组件需要 text，不能绑定图片或角色")
        elif self.asset_id is None or self.text is not None:
            raise ValueError("图片组件需要 asset_id，不能包含 text")
        if self.kind == "character":
            if self.character_id is None:
                raise ValueError("人物组件需要稳定 character_id")
        elif self.character_id is not None:
            raise ValueError("只有人物组件可以指定 character_id")
        return self


class Dialogue(StrictModel):
    speaker_id: Identifier | None = None
    text: str
    provenance: dict[str, JsonValue] = Field(default_factory=dict)


class Choice(StrictModel):
    choice_id: Identifier
    text: NonEmptyText
    target_frame_id: Identifier
    provenance: dict[str, JsonValue] = Field(default_factory=dict)


class VariableDefinition(StrictModel):
    variable_id: Identifier
    type: Literal["int", "float", "bool", "str"]
    default: JsonValue
    minimum: int | float | None = None
    maximum: int | float | None = None
    allowed_values: list[JsonValue] | None = None
    scope: Literal["save"] = "save"
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    def value_schema(self) -> dict:
        schema = {"type": {"int": "integer", "float": "number", "bool": "boolean", "str": "string"}[self.type]}
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.allowed_values is not None:
            schema["enum"] = self.allowed_values
        return schema

    @model_validator(mode="after")
    def check_default(self):
        from .minigame_packages import validate_typed_value
        from .errors import BridgeError
        try:
            validate_typed_value(self.value_schema(), self.default)
        except BridgeError as exc:
            raise ValueError(exc.message) from exc
        return self


class MinigameDefinition(StrictModel):
    game_id: Identifier
    package_digest: Sha256
    package_version: NonEmptyText
    path: str
    manifest: dict[str, JsonValue]
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_package_identity(self):
        from .minigame_packages import validate_manifest
        from .errors import BridgeError
        if self.path != "minigames/" + self.package_digest:
            raise ValueError("小游戏路径必须由完整内容哈希决定")
        try:
            manifest = validate_manifest(self.manifest, allow_prototype=True)
        except BridgeError as exc:
            raise ValueError(exc.message) from exc
        if manifest["game_id"] != self.game_id or manifest["version"] != self.package_version:
            raise ValueError("小游戏注册身份与包清单不一致")
        return self


class ValueBinding(StrictModel):
    kind: Literal["literal", "variable", "result"]
    value: JsonValue = None
    variable_id: Identifier | None = None
    field: Identifier | None = None

    @model_validator(mode="after")
    def check_variant(self):
        if self.kind == "literal":
            if "value" not in self.model_fields_set or self.variable_id is not None or self.field is not None:
                raise ValueError("常量绑定需要 value，不能包含引用")
        elif self.kind == "variable":
            if self.variable_id is None or self.field is not None or "value" in self.model_fields_set:
                raise ValueError("变量绑定只接受 variable_id")
        elif self.field is None or self.variable_id is not None or "value" in self.model_fields_set:
            raise ValueError("结果绑定只接受 field")
        return self

    @model_serializer(mode="wrap")
    def serialize_variant(self, handler):
        data = handler(self)
        keep = {"kind", {"literal": "value", "variable": "variable_id", "result": "field"}[self.kind]}
        return {key: value for key, value in data.items() if key in keep}


class Predicate(StrictModel):
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte", "all", "any", "not"]
    left: ValueBinding | None = None
    right: ValueBinding | None = None
    conditions: list[Predicate] = Field(default_factory=list, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def bound_tree(cls, raw):
        count = 0
        def visit(value, depth):
            nonlocal count
            count += 1
            if count > 256:
                raise ValueError("条件树总操作数超过限制")
            if depth > 32:
                raise ValueError("条件树超过深度限制")
            if isinstance(value, dict):
                children = value.get("conditions", [])
                if isinstance(children, list):
                    if len(children) > 128:
                        raise ValueError("条件数量超过限制")
                    for child in children:
                        visit(child, depth + 1)
        visit(raw, 0)
        return raw

    @model_validator(mode="after")
    def check_variant(self):
        if self.op in {"all", "any", "not"}:
            if self.left is not None or self.right is not None or not self.conditions or self.op == "not" and len(self.conditions) != 1:
                raise ValueError("逻辑条件需要明确子条件，not 仅接受一个")
        elif self.left is None or self.right is None or self.conditions:
            raise ValueError("比较条件需要左右有类型的操作数")
        return self


class VariableEffect(StrictModel):
    effect_id: Identifier
    op: Literal["set", "add"]
    variable_id: Identifier
    value: ValueBinding


class ResultBranch(StrictModel):
    branch_id: Identifier
    outcome: Identifier
    predicate: Predicate | None = None
    effects: list[VariableEffect] = Field(default_factory=list, max_length=256)
    target_frame_id: Identifier


class Flow(StrictModel):
    kind: Literal["next", "choices", "end", "minigame"]
    target_frame_id: Identifier | None = None
    choices: list[Choice] = Field(default_factory=list)
    game_id: Identifier | None = None
    instance_id: Identifier | None = None
    inputs: dict[Identifier, ValueBinding] = Field(default_factory=dict)
    result_branches: list[ResultBranch] = Field(default_factory=list, max_length=256)
    cancel_target_frame_id: Identifier | None = None
    error_target_frame_id: Identifier | None = None
    fallback_target_frame_id: Identifier | None = None
    save_mode: Literal["entry_restart", "checkpoint"] = "entry_restart"

    @model_validator(mode="after")
    def check_variant(self) -> Flow:
        if self.kind == "minigame":
            if (self.target_frame_id is not None or self.choices or self.game_id is None or self.instance_id is None
                    or not self.result_branches or self.cancel_target_frame_id is None
                    or self.error_target_frame_id is None or self.fallback_target_frame_id is None):
                raise ValueError("小游戏需要身份、结果规则及独立取消、异常、兜底目标")
            ids = [branch.branch_id for branch in self.result_branches]
            effect_ids = [effect.effect_id for branch in self.result_branches for effect in branch.effects]
            if len(ids) != len(set(ids)) or len(effect_ids) != len(set(effect_ids)):
                raise ValueError("小游戏分支或效果身份重复")
            if any(binding.kind == "result" for binding in self.inputs.values()):
                raise ValueError("进入小游戏的输入不能读取尚未产生的结果")
            return self
        if (self.game_id is not None or self.instance_id is not None or self.inputs or self.result_branches
                or self.cancel_target_frame_id is not None or self.error_target_frame_id is not None
                or self.fallback_target_frame_id is not None or self.save_mode != "entry_restart"):
            raise ValueError("普通剧情流程不能包含小游戏配置")
        if self.kind == "next":
            if self.target_frame_id is None or self.choices:
                raise ValueError("next 需要一个 target_frame_id，不能包含 choices")
        elif self.kind == "choices":
            if self.target_frame_id is not None or not self.choices:
                raise ValueError("choices 需要至少一个选项，不能指定单一 target_frame_id")
            ids = [choice.choice_id for choice in self.choices]
            if len(ids) != len(set(ids)):
                raise ValueError("同一菜单中的 choice_id 不能重复")
        elif self.target_frame_id is not None or self.choices:
            raise ValueError("end 不能包含目标或选项")
        return self

    @model_serializer(mode="wrap")
    def serialize_variant(self, handler):
        data = handler(self)
        if self.kind != "minigame":
            return {key: value for key, value in data.items() if key in {"kind", "target_frame_id", "choices"}}
        return {key: value for key, value in data.items() if key not in {"target_frame_id", "choices"}}


class Transition(StrictModel):
    kind: Literal["none", "dissolve", "fade"] = "none"
    duration: float = Field(default=0.3, ge=0.0, le=10.0)


class Frame(StrictModel):
    frame_id: Identifier
    act_id: Identifier
    source_label: str | None = None
    order: int = Field(default=0, ge=0)
    bg: Component | None = None
    characters: list[Component] = Field(default_factory=list)
    decorations: list[Component] = Field(default_factory=list)
    dialogue: Dialogue | None = None
    voice: Identifier | None = None
    bgm: Identifier | None = None
    sfx: list[Identifier] = Field(default_factory=list)
    transition: Transition = Field(default_factory=Transition)
    flow: Flow
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_components(self) -> Frame:
        if self.bg is not None and self.bg.kind != "background":
            raise ValueError("bg 只接受 background 组件")
        if any(component.kind != "character" for component in self.characters):
            raise ValueError("characters 只接受 character 组件")
        if any(component.kind not in ("image", "text") for component in self.decorations):
            raise ValueError("decorations 只接受 image/text 组件")
        components = self.all_components()
        ids = [component.component_id for component in components]
        if len(ids) != len(set(ids)):
            raise ValueError("同一帧中的 component_id 不能重复")
        return self

    def all_components(self) -> list[Component]:
        return ([self.bg] if self.bg is not None else []) + self.characters + self.decorations


class Act(StrictModel):
    act_id: Identifier
    name: str = "第一幕"
    order: int = Field(default=0, ge=0)
    frame_ids: list[Identifier] = Field(default_factory=list)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("frame_ids")
    @classmethod
    def unique_frames(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("幕中的 frame_ids 不能重复")
        return value


class FramePatch(StrictModel):
    """Use model_dump(exclude_unset=True): omission is not a clearing action.

    Stable identities/membership cannot be changed through a local patch.
    Collections replace the complete collection; [] explicitly clears it.
    """

    source_label: str | None = None
    bg: Component | None = None
    characters: list[Component] | None = None
    decorations: list[Component] | None = None
    dialogue: Dialogue | None = None
    voice: Identifier | None = None
    bgm: Identifier | None = None
    sfx: list[Identifier] | None = None
    transition: Transition | None = None
    flow: Flow | None = None
    provenance: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def forbid_required_nulls(self) -> FramePatch:
        for name in ("characters", "decorations", "sfx", "transition", "flow", "provenance"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"PATCH 的 {name} 不能为 null；清空列表请使用 []")
        return self


def apply_frame_patch(frame: Frame, patch: FramePatch | dict[str, Any]) -> Frame:
    """Merge a local patch; the storage layer must revalidate the full Project."""
    if isinstance(patch, dict):
        patch = FramePatch.model_validate(patch)
    merged = frame.model_dump(mode="python")
    merged.update(patch.model_dump(mode="python", exclude_unset=True))
    return Frame.model_validate(merged)


class Project(StrictModel):
    schema_version: Literal["1.0", "1.1"] = "1.0"
    project_id: Identifier
    name: NonEmptyText = "新作品"
    revision: int = Field(default=0, ge=0)
    canvas: Canvas = Field(default_factory=Canvas)
    language: Literal["schinese"] = "schinese"
    entry_frame_id: Identifier | None = None
    save_namespace: Identifier
    acts: dict[Identifier, Act] = Field(default_factory=dict)
    frames: dict[Identifier, Frame] = Field(default_factory=dict)
    characters: dict[Identifier, Character] = Field(default_factory=dict)
    assets: dict[Identifier, Asset] = Field(default_factory=dict)
    variables: dict[Identifier, VariableDefinition] = Field(default_factory=dict)
    minigames: dict[Identifier, MinigameDefinition] = Field(default_factory=dict)
    layout: LayoutProfile = Field(default_factory=LayoutProfile)
    sdk_version: str | None = None
    adapter_version: str = "0.1.0"
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    provenance: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_references(self) -> Project:
        if self.schema_version == "1.0" and (self.variables or self.minigames or any(frame.flow.kind == "minigame" for frame in self.frames.values())):
            raise ValueError("小游戏和变量需要显式迁移到 schema 1.1")
        for collection, field_name in (
            (self.acts, "act_id"), (self.frames, "frame_id"),
            (self.characters, "character_id"), (self.assets, "asset_id"),
            (self.variables, "variable_id"), (self.minigames, "game_id"),
        ):
            for key, item in collection.items():
                if key != getattr(item, field_name):
                    raise ValueError(f"索引 {key} 与对象 {field_name} 不一致")
        if self.frames and self.entry_frame_id is None:
            raise ValueError("非空项目必须指定 entry_frame_id")
        if self.entry_frame_id is not None and self.entry_frame_id not in self.frames:
            raise ValueError(f"入口帧不存在: {self.entry_frame_id}")

        membership: dict[str, str] = {}
        for act in self.acts.values():
            for frame_id in act.frame_ids:
                if frame_id not in self.frames:
                    raise ValueError(f"幕 {act.act_id} 引用了不存在的帧 {frame_id}")
                if frame_id in membership:
                    raise ValueError(f"帧 {frame_id} 不能同时属于多个幕")
                membership[frame_id] = act.act_id

        for frame in self.frames.values():
            if frame.act_id not in self.acts:
                raise ValueError(f"帧 {frame.frame_id} 引用了不存在的幕 {frame.act_id}")
            if membership.get(frame.frame_id) != frame.act_id:
                raise ValueError(f"帧 {frame.frame_id} 必须登记在所属幕的 frame_ids 中")
            if frame.dialogue and frame.dialogue.speaker_id is not None:
                if frame.dialogue.speaker_id not in self.characters:
                    raise ValueError(f"帧 {frame.frame_id} 的说话角色不存在")
            for component in frame.all_components():
                if component.character_id and component.character_id not in self.characters:
                    raise ValueError(f"组件 {component.component_id} 的角色不存在")
                if component.asset_id:
                    self.require_asset(component.asset_id, "image", frame.frame_id)
            for asset_id in [frame.voice, frame.bgm, *frame.sfx]:
                if asset_id:
                    self.require_asset(asset_id, "audio", frame.frame_id)
            for target in flow_targets(frame):
                if target not in self.frames:
                    raise ValueError(f"帧 {frame.frame_id} 的目标帧不存在: {target}")
            if frame.flow.kind == "minigame":
                self.check_minigame(frame)
        instances = [frame.flow.instance_id for frame in self.frames.values() if frame.flow.kind == "minigame"]
        if len(instances) != len(set(instances)):
            raise ValueError("不同剧情调用不能共用 instance_id")

        for character_id in self.layout.character_slots:
            if character_id not in self.characters:
                raise ValueError(f"布局槽位引用了不存在的角色 {character_id}")
        paths: dict[str, str] = {}
        for asset in self.assets.values():
            key = asset.path.casefold()
            if key in paths and paths[key] != asset.sha256:
                raise ValueError(f"同一路径不能绑定不同内容版本: {asset.path}")
            paths[key] = asset.sha256
        return self

    @model_serializer(mode="wrap")
    def serialize_schema(self, handler):
        data = handler(self)
        if self.schema_version == "1.0":
            data.pop("variables", None)
            data.pop("minigames", None)
        return data

    def check_minigame(self, frame: Frame):
        from .minigame_packages import validate_typed_value
        from .errors import BridgeError
        flow = frame.flow
        game = self.minigames.get(flow.game_id)
        if game is None:
            raise ValueError(f"帧 {frame.frame_id} 引用了不存在的小游戏")
        if flow.save_mode == "checkpoint" and not game.manifest["save_capabilities"]["checkpoint"]:
            raise ValueError("入口重开包不能声明检查点续玩")
        inputs = game.manifest["inputs"]
        if set(flow.inputs) - set(inputs):
            raise ValueError("小游戏输入包含未声明字段")
        def kinds(schema):
            value = schema["type"]
            return set(value if isinstance(value, list) else [value])
        def schema_for(binding):
            if binding.kind == "variable":
                variable = self.variables.get(binding.variable_id)
                if variable is None:
                    raise ValueError("绑定引用了不存在的变量")
                return variable.value_schema()
            if binding.kind == "result":
                schema = game.manifest["results"]["fields"].get(binding.field)
                if schema is None:
                    raise ValueError("绑定引用了未声明的小游戏结果字段")
                return schema
            value = binding.value
            return {"type": "null" if value is None else "boolean" if type(value) is bool else "integer" if type(value) is int
                    else "number" if type(value) is float else "string" if type(value) is str else "array" if type(value) is list else "object"}
        def compatible(source, target):
            allowed = kinds(target)
            if "number" in allowed:
                allowed.add("integer")
            return kinds(source).issubset(allowed)
        def check_assignment(binding, target):
            if not compatible(schema_for(binding), target):
                raise ValueError("输入或效果的绑定类型不一致")
            if binding.kind == "literal":
                try:
                    validate_typed_value(target, binding.value)
                except BridgeError as exc:
                    raise ValueError(exc.message) from exc
        def check_predicate(predicate):
            if predicate.conditions:
                for child in predicate.conditions:
                    check_predicate(child)
            else:
                left, right = schema_for(predicate.left), schema_for(predicate.right)
                numeric = kinds(left) <= {"integer", "number"} and kinds(right) <= {"integer", "number"}
                if predicate.op in {"lt", "lte", "gt", "gte"} and not numeric:
                    raise ValueError("大小比较只接受有限数值")
                if not numeric and not (compatible(left, right) or compatible(right, left)):
                    raise ValueError("条件比较的类型不一致")
        for name, binding in flow.inputs.items():
            check_assignment(binding, inputs[name])
        for branch in flow.result_branches:
            if branch.outcome not in game.manifest["results"]["outcomes"]:
                raise ValueError("结果分支引用了未声明的完成结果")
            if branch.predicate:
                check_predicate(branch.predicate)
            for effect in branch.effects:
                variable = self.variables.get(effect.variable_id)
                if variable is None:
                    raise ValueError("结果效果引用了不存在的变量")
                if effect.op == "add" and variable.type not in {"int", "float"}:
                    raise ValueError("增减效果只能作用于数值变量")
                # An add operand may be negative or outside the target's range;
                # runtime validates the complete candidate value after all effects.
                target_schema = {"type": "integer" if variable.type == "int" else "number"} if effect.op == "add" else variable.value_schema()
                check_assignment(effect.value, target_schema)

    def require_asset(self, asset_id: str, kind: str, frame_id: str) -> None:
        asset = self.assets.get(asset_id)
        if asset is None:
            raise ValueError(f"帧 {frame_id} 的素材不存在: {asset_id}")
        if asset.kind != kind:
            raise ValueError(f"帧 {frame_id} 的素材 {asset_id} 应为 {kind} 类型")


def flow_targets(frame: Frame) -> tuple[str, ...]:
    if frame.flow.kind == "next":
        assert frame.flow.target_frame_id is not None
        return (frame.flow.target_frame_id,)
    if frame.flow.kind == "choices":
        return tuple(choice.target_frame_id for choice in frame.flow.choices)
    if frame.flow.kind == "minigame":
        return tuple(dict.fromkeys([branch.target_frame_id for branch in frame.flow.result_branches]
                                  + [frame.flow.cancel_target_frame_id, frame.flow.error_target_frame_id, frame.flow.fallback_target_frame_id]))
    return ()


def analyze_flow(project: Project) -> dict[str, Any]:
    """Diagnose reachability/exits/cycles without recursion or changing content.

    Structurally valid draft content can contain unreachable frames or cycles.
    Publication/check tools must surface these diagnostics and apply their
    declared acceptance policy rather than silently repairing the story.
    """
    graph = {frame_id: flow_targets(frame) for frame_id, frame in project.frames.items()}
    reachable: set[str] = set()
    pending = [project.entry_frame_id] if project.entry_frame_id else []
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(graph[current])

    reverse: dict[str, list[str]] = {frame_id: [] for frame_id in graph}
    for frame_id, targets in graph.items():
        for target in targets:
            reverse[target].append(frame_id)
    endings = {frame_id for frame_id, targets in graph.items() if not targets}
    can_end = set(endings)
    queue = deque(endings)
    while queue:
        for predecessor in reverse[queue.popleft()]:
            if predecessor not in can_end:
                can_end.add(predecessor)
                queue.append(predecessor)

    colors: dict[str, int] = {}
    cycles: list[list[str]] = []
    for start in graph:
        if colors.get(start, 0):
            continue
        path = [start]
        positions = {start: 0}
        colors[start] = 1
        stack = [(start, iter(graph[start]))]
        while stack:
            current, targets = stack[-1]
            target = next(targets, None)
            if target is None:
                stack.pop()
                colors[current] = 2
                positions.pop(current)
                path.pop()
            elif colors.get(target, 0) == 0:
                colors[target] = 1
                positions[target] = len(path)
                path.append(target)
                stack.append((target, iter(graph[target])))
            elif colors[target] == 1:
                cycles.append(path[positions[target]:] + [target])

    return {
        "reachable": sorted(reachable),
        "unreachable": sorted(set(graph) - reachable),
        "ending_frames": sorted(endings),
        "without_exit": sorted(reachable - can_end),
        "cycles": cycles,
    }
