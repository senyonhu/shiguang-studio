"""Predict user-visible bad compositions without substituting for screenshots."""

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
import pytest

from renpy_bridge.layout import check_layout
from renpy_bridge.models import Project


@pytest.fixture
def font_path(tmp_path):
    """A tiny real font with known Unicode coverage and deterministic metrics."""
    characters = [chr(value) for value in range(32, 127)] + list("你好人物原文结尾第一幕小明继续选择结束标题")
    characters = list(dict.fromkeys(characters))
    names = {character: f"uni{ord(character):04X}" for character in characters}
    order = [".notdef", *names.values()]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    glyphs = {}
    metrics = {}
    for name in order:
        advance = 1000 if name.startswith("uni") and int(name[3:], 16) > 127 else 500
        pen = TTGlyphPen(None)
        if name != names[" "]:
            pen.moveTo((0, 0))
            pen.lineTo((advance - 50, 0))
            pen.lineTo((advance - 50, 700))
            pen.lineTo((0, 700))
            pen.closePath()
        glyphs[name] = pen.glyph()
        metrics[name] = (advance, 0)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupCharacterMap({ord(character): name for character, name in names.items()})
    builder.setupNameTable({"familyName": "Layout Fixture", "styleName": "Regular", "uniqueFontIdentifier": "layout-fixture", "fullName": "Layout Fixture Regular", "psName": "LayoutFixture-Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    path = tmp_path / "检查 字体.ttf"
    builder.save(path)
    return path


def project(*, characters=None, dialogue=None, bg=None, decorations=None, metadata=None):
    characters = characters or []
    return Project.model_validate({
        "project_id": "layout_project", "save_namespace": "layout_project", "name": "Test",
        "canvas": {"width": 1000, "height": 600}, "entry_frame_id": "frame_one",
        "characters": {"hero": {"character_id": "hero", "name": "小明"}},
        "assets": {"sprite": {
            "asset_id": "sprite", "kind": "image", "path": "assets/sprite.png", "sha256": "a" * 64,
            "metadata": metadata or {"width": 300, "height": 600, "has_alpha": False},
        }},
        "acts": {"act_one": {"act_id": "act_one", "frame_ids": ["frame_one"]}},
        "frames": {"frame_one": {
            "frame_id": "frame_one", "act_id": "act_one", "flow": {"kind": "end"},
            "bg": bg, "characters": characters, "decorations": decorations or [], "dialogue": dialogue,
            "provenance": {"source_id": "source_sheet", "sheet": "第一幕", "cell": "B2"},
        }},
    })


def sprite(component_id="hero_sprite", **layout):
    return {"component_id": component_id, "kind": "character", "character_id": "hero", "asset_id": "sprite",
            "layout": layout or {"height": 0.85}, "provenance": {"cell": "C2"}}


def codes(report):
    return {item["code"] for item in report["diagnostics"]}


def test_short_chinese_plain_text_can_pass_predictions_without_claiming_visual_verification(tmp_path, font_path):
    model = project(dialogue={"speaker_id": "hero", "text": "你好 [x] {b} 100%"})
    report = check_layout(model, tmp_path, font_path)
    assert report["ok"] and report["diagnostics"] == []
    assert report["visual_verified"] is False


def test_transparent_sprite_padding_does_not_create_false_crop_or_overlap(tmp_path, font_path):
    model = project(
        metadata={"width": 1000, "height": 1000, "has_alpha": True, "visible_bbox": [450, 0, 550, 1000]},
        characters=[sprite("left", x=0.01, y=0.5, height=0.2), sprite("right", x=0.09, y=0.5, height=0.2)],
    )
    report = check_layout(model, tmp_path, font_path)
    assert report["ok"]
    assert "COMPONENT_OUTSIDE_CANVAS" not in codes(report)
    assert "CHARACTER_OVERLAP" not in codes(report)


def test_cover_background_crop_is_intentional_but_same_cover_character_is_warned(tmp_path, font_path):
    metadata = {"width": 1000, "height": 100, "has_alpha": False}
    background = {"component_id": "background", "kind": "background", "asset_id": "sprite"}
    contained = project(metadata=metadata, bg=background,
                        characters=[sprite(x=0.5, y=0.5, anchor_y=0.5, width=0.3, height=0.5, fit="contain")])
    assert "COMPONENT_OUTSIDE_CANVAS" not in codes(check_layout(contained, tmp_path, font_path))
    covering = project(metadata=metadata, bg=background,
                       characters=[sprite(x=0.5, y=0.5, anchor_y=0.5, width=0.3, height=0.5, fit="cover")])
    report = check_layout(covering, tmp_path, font_path)
    crops = [item for item in report["diagnostics"] if item["code"] == "COMPONENT_OUTSIDE_CANVAS"]
    assert len(crops) == 1 and crops[0]["component_id"] == "hero_sprite"
    assert crops[0]["severity"] == "warning" and report["ok"]


def test_people_overlap_and_dialogue_obstruction_have_frame_component_and_source(tmp_path, font_path):
    model = project(characters=[sprite("left", x=0.45, height=0.85), sprite("right", x=0.55, height=0.85)],
                    dialogue={"text": "你好"})
    report = check_layout(model, tmp_path, font_path)
    assert {"CHARACTER_OVERLAP", "DIALOGUE_COVERS_CHARACTER"} <= codes(report)
    overlap = next(item for item in report["diagnostics"] if item["code"] == "CHARACTER_OVERLAP")
    assert overlap["frame_id"] == "frame_one" and overlap["component_id"] == "left"
    assert overlap["details"]["other_component_id"] == "right"
    assert overlap["provenance"] == {"source_id": "source_sheet", "sheet": "第一幕", "cell": "C2"}
    assert report["ok"] and report["visual_verified"] is False


def test_long_dialogue_and_small_decorative_box_warn_without_rewriting_text(tmp_path, font_path):
    dialogue = "你好" * 200
    title = "标题" * 40
    model = project(dialogue={"text": dialogue, "provenance": {"cell": "D2"}}, decorations=[{
        "component_id": "title", "kind": "text", "text": title,
        "layout": {"x": 0.5, "y": 0.1, "anchor_y": 0.0, "width": 0.1, "height": 0.1},
    }])
    original = model.model_dump(mode="json")
    report = check_layout(model, tmp_path, font_path)
    assert {"DIALOGUE_OVERFLOW_RISK", "TEXT_OVERFLOW_RISK"} <= codes(report)
    warning = next(item for item in report["diagnostics"] if item["code"] == "DIALOGUE_OVERFLOW_RISK")
    assert warning["provenance"]["cell"] == "D2" and warning["details"]["line_count"] > 5
    assert model.model_dump(mode="json") == original
    assert report["ok"]


def test_missing_chinese_glyph_is_an_error_at_the_authored_location(tmp_path, font_path):
    model = project(dialogue={"text": "你好龘", "provenance": {"cell": "D2"}})
    report = check_layout(model, tmp_path, font_path)
    assert not report["ok"]
    missing = next(item for item in report["diagnostics"] if item["code"] == "MISSING_GLYPH")
    assert missing["severity"] == "error" and missing["frame_id"] == "frame_one"
    assert missing["provenance"]["cell"] == "D2"
    assert missing["details"]["characters"] == ["龘"] and missing["details"]["codepoints"] == ["U+9F98"]


def test_invalid_font_and_empty_alpha_are_reported_without_crashing(tmp_path, font_path):
    model = project(metadata={"width": 300, "height": 600, "has_alpha": True, "visible_bbox": None},
                    characters=[sprite()])
    transparent = check_layout(model, tmp_path, font_path)
    assert "INVISIBLE_IMAGE" in codes(transparent) and transparent["ok"]
    missing = check_layout(model, tmp_path, tmp_path / "missing.ttf")
    assert not missing["ok"] and codes(missing) == {"FONT_UNREADABLE"}
    invalid = tmp_path / "invalid.ttf"
    invalid.write_bytes(b"not a font")
    assert codes(check_layout(model, tmp_path, invalid)) == {"FONT_UNREADABLE"}
