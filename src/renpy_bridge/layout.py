"""Predict geometry and font risks; only real engine images verify appearance.

Image scaling follows the SDK's contain/cover transform semantics. Transparent
margins are removed only from diagnostic bounds, never from the authored asset.
Text wrapping is a conservative FreeType estimate, not a Ren'Py renderer result.
"""

from __future__ import annotations

from math import ceil, isfinite
from pathlib import Path
import unicodedata

from fontTools.ttLib import TTFont
from PIL import Image, ImageFont

from .models import Component, Project


def _intersect(left, right):
    width = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    return width * height


def _area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _image_shape(asset, root: Path):
    metadata = asset.metadata
    width, height = metadata.get("width"), metadata.get("height")
    valid_dimensions = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                           and isfinite(value) and value > 0 for value in (width, height))
    alpha = metadata.get("has_alpha") is True
    bbox = metadata.get("visible_bbox") if "visible_bbox" in metadata else (0, 0, width, height)
    if not valid_dimensions:
        source = (root / asset.path).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            return None
        try:
            with Image.open(source) as image:
                width, height = image.size
                alpha = "A" in image.getbands() or "transparency" in image.info
                bbox = image.convert("RGBA").getchannel("A").getbbox() if alpha else (0, 0, width, height)
        except (OSError, ValueError):
            return None
    if alpha and bbox is None:
        return float(width), float(height), None
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        bbox = (0, 0, width, height)
    elif not all(isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) for value in bbox):
        bbox = (0, 0, width, height)
    elif not (0 <= bbox[0] <= bbox[2] <= width and 0 <= bbox[1] <= bbox[3] <= height):
        bbox = (0, 0, width, height)
    return float(width), float(height), tuple(float(value) for value in bbox)


def _image_box(component: Component, project: Project, shape):
    width, height, visible = shape
    if visible is None:
        return None
    layout = component.layout
    scales = []
    if layout.width is not None:
        scales.append(round(project.canvas.width * layout.width) / width)
    if layout.height is not None:
        scales.append(round(project.canvas.height * layout.height) / height)
    scale = (min(scales) if layout.fit == "contain" else max(scales)) if scales else 1.0
    left = project.canvas.width * layout.x - width * scale * layout.anchor_x
    top = project.canvas.height * layout.y - height * scale * layout.anchor_y
    return (left + visible[0] * scale, top + visible[1] * scale,
            left + visible[2] * scale, top + visible[3] * scale)


def _measure_text(text: str, font, max_width: float):
    """Estimate wrapped line count/width without changing text or font size."""
    line_height = sum(font.getmetrics())
    if not text:
        return 0.0, 0.0, 0
    rows = 0
    widest = 0.0
    for paragraph in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        advances = [float(font.getlength(character)) if character != "\t" else float(font.getlength("    ")) for character in paragraph]
        row_width = 0.0
        for advance in advances:
            if row_width > 0 and row_width + advance > max_width:
                widest = max(widest, row_width)
                rows += 1
                row_width = 0.0
            row_width += advance
        widest = max(widest, row_width)
        rows += 1
    return widest, float(rows * line_height), rows


def check_layout(project: Project, authoring_root: Path, font_path: Path) -> dict:
    """Return source-addressable risks for the exact persisted layout.

    Errors mean the required font cannot render authored text. Geometry,
    overlap and overflow predictions are warnings; they require an engine
    screenshot before a human or Codex can decide that the composition works.
    """
    authoring_root = Path(authoring_root).resolve()
    font_path = Path(font_path)
    diagnostics = []
    fonts = {}
    shapes = {}

    def emit(code, severity, message, frame=None, component=None, provenance=None, **details):
        source = dict(frame.provenance) if frame is not None else dict(project.provenance)
        if component is not None:
            source.update(component.provenance)
        if provenance:
            source.update(provenance)
        diagnostics.append({
            "code": code, "severity": severity, "message": message,
            "frame_id": frame.frame_id if frame is not None else None,
            "component_id": component.component_id if component is not None else None,
            "provenance": source, "details": details,
        })

    try:
        with TTFont(font_path, lazy=True) as font_file:
            supported = set(font_file.getBestCmap() or {})
        fonts[project.layout.font_size] = ImageFont.truetype(str(font_path), project.layout.font_size)
    except Exception as error:
        emit("FONT_UNREADABLE", "error", "无法读取作品使用的字体，尚不能检查中文与排版", font_path=str(font_path), reason=str(error))
        return {"ok": False, "diagnostics": diagnostics, "visual_verified": False}

    def font(size):
        if size not in fonts:
            fonts[size] = ImageFont.truetype(str(font_path), size)
        return fonts[size]

    def check_text(text, frame=None, component=None, provenance=None, field=None):
        missing = sorted({character for character in text
                          if not character.isspace() and unicodedata.category(character) not in {"Cc", "Cf"}
                          and ord(character) not in supported})
        if missing:
            emit("MISSING_GLYPH", "error", "字体缺少原文中的字符，实际画面可能显示空框", frame, component, provenance,
                 field=field, characters=missing, codepoints=[f"U+{ord(character):04X}" for character in missing])

    check_text(project.name, field="project.name")
    for frame in project.frames.values():
        if frame.dialogue is not None:
            check_text(frame.dialogue.text, frame, provenance=frame.dialogue.provenance, field="dialogue.text")
            if frame.dialogue.speaker_id is not None:
                character = project.characters[frame.dialogue.speaker_id]
                check_text(character.name, frame, field="character.name")
            width = project.canvas.width * (1 - 2 * project.layout.safe_margin)
            _, needed_height, line_count = _measure_text(frame.dialogue.text, font(project.layout.font_size), width)
            # The fixed SDK GUI template uses dialogue_ypos=ceil(50*scale).
            gui_scale = min(project.canvas.width / 1280, project.canvas.height / 720)
            available_height = max(0, round(project.canvas.height * project.layout.dialogue_height) - ceil(50 * gui_scale))
            if needed_height > available_height:
                emit("DIALOGUE_OVERFLOW_RISK", "warning", "对白按当前字号预测超过文字框高度，请查看引擎截图", frame,
                     provenance=frame.dialogue.provenance, line_count=line_count,
                     estimated_height=round(needed_height, 2), available_height=available_height)
        for choice in frame.flow.choices:
            check_text(choice.text, frame, provenance=choice.provenance, field="choice.text")

        boxes = []
        for component in frame.all_components():
            if component.kind == "text":
                check_text(component.text or "", frame, component, field="component.text")
                width = round(project.canvas.width * component.layout.width) if component.layout.width is not None else project.canvas.width
                text_width, text_height, line_count = _measure_text(component.text or "", font(component.font_size), width)
                max_height = round(project.canvas.height * component.layout.height) if component.layout.height is not None else None
                if max_height is not None and text_height > max_height:
                    emit("TEXT_OVERFLOW_RISK", "warning", "装饰文字按当前区域预测溢出，请查看引擎截图", frame, component,
                         line_count=line_count, estimated_height=round(text_height, 2), available_height=max_height)
                rendered_height = min(text_height, max_height) if max_height is not None else text_height
                left = project.canvas.width * component.layout.x - text_width * component.layout.anchor_x
                top = project.canvas.height * component.layout.y - rendered_height * component.layout.anchor_y
                box = (left, top, left + text_width, top + rendered_height)
            else:
                if component.asset_id not in shapes:
                    shapes[component.asset_id] = _image_shape(project.assets[component.asset_id], authoring_root)
                shape = shapes[component.asset_id]
                if shape is None:
                    emit("IMAGE_GEOMETRY_UNKNOWN", "warning", "缺少可验证的图片尺寸，尚不能预测组件边界", frame, component)
                    continue
                box = _image_box(component, project, shape)
                if box is None or _area(box) == 0:
                    emit("INVISIBLE_IMAGE", "warning", "图片没有可见像素，当前组件不会显示内容", frame, component)
                    continue
            if not (component.kind == "background" and component.layout.fit == "cover") and (
                box[0] < -0.5 or box[1] < -0.5 or box[2] > project.canvas.width + 0.5 or box[3] > project.canvas.height + 0.5
            ):
                emit("COMPONENT_OUTSIDE_CANVAS", "warning", "组件的可见范围越出画布，可能被裁切", frame, component,
                     visible_box=[round(value, 2) for value in box])
            boxes.append((component, box))

        characters = [(component, box) for component, box in boxes if component.kind == "character"]
        for index, (component, box) in enumerate(characters):
            for other, other_box in characters[index + 1:]:
                ratio = _intersect(box, other_box) / min(_area(box), _area(other_box))
                if ratio >= 0.15:
                    emit("CHARACTER_OVERLAP", "warning", "人物可见区域明显重叠，请检查脸部与主体是否被遮挡", frame, component,
                         other_component_id=other.component_id, overlap_fraction=round(ratio, 3))
            if frame.dialogue is not None:
                dialogue_box = (0, project.canvas.height * (1 - project.layout.dialogue_height), project.canvas.width, project.canvas.height)
                ratio = _intersect(box, dialogue_box) / _area(box)
                if ratio >= 0.1:
                    emit("DIALOGUE_COVERS_CHARACTER", "warning", "文字框与人物可见区域重叠，请检查关键部位是否被遮挡", frame, component,
                         overlap_fraction=round(ratio, 3))

    return {"ok": not any(item["severity"] == "error" for item in diagnostics),
            "diagnostics": diagnostics, "visual_verified": False}
