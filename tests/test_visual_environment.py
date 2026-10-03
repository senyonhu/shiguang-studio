"""Renderer comparability must come from native output and actual font bytes."""
import json
from pathlib import Path
import pytest
from renpy_bridge.errors import BridgeError
from renpy_bridge.models import Project
from renpy_bridge.pipeline import _visual_environment
from renpy_bridge.storage import file_hash
from PIL import Image


def fixture(tmp_path):
    font = tmp_path / "game/SourceHanSansLite.ttf"
    font.parent.mkdir()
    font.write_bytes(b"fixed test font bytes")
    manifest = tmp_path / "bridge-manifest.json"
    manifest.write_text(json.dumps({"chinese_font_sha256": file_hash(font)}), encoding="utf-8")
    log = tmp_path / "native-tests.engine-log.txt"
    log.write_text("Initializing gl2 renderer:\nVendor: 'NVIDIA Corporation'\nRenderer: 'fixture GPU'\nVersion: 'fixture driver'\n", encoding="utf-8")
    generation = {"project_path": str(tmp_path), "manifest_path": str(manifest), "sdk_version": "8.5.3.26051504"}
    captured = tmp_path / "capture.png"
    Image.new("RGBA", (40, 24), "navy").save(captured)
    screenshots = [{"path": str(captured), "sha256": file_hash(captured)}]
    return Project(project_id="p_fixture", save_namespace="save_fixture"), generation, log, font, screenshots


def test_measured_renderer_and_font_are_bound(tmp_path):
    project, generation, log, font, screenshots = fixture(tmp_path)
    result = _visual_environment(project, generation, log, screenshots)
    assert result["renderer"] == {"backend": "gl2", "vendor": "'NVIDIA Corporation'", "renderer": "'fixture GPU'", "version": "'fixture driver'"}
    assert result["font_sha256"] == file_hash(font)
    assert result["resolution"] == [1280, 720]
    assert result["capture_resolution"] == [40, 24]
    assert result["capture_resolution_verified"] is True
    assert result["sdk_version"] == generation["sdk_version"]


@pytest.mark.parametrize("missing", ["backend", "renderer", "log"])
def test_missing_measured_renderer_is_not_invented(tmp_path, missing):
    project, generation, log, _, screenshots = fixture(tmp_path)
    if missing == "log":
        log.unlink()
    else:
        text = log.read_text(encoding="utf-8")
        line = "Initializing gl2 renderer:\n" if missing == "backend" else "Renderer: 'fixture GPU'\n"
        log.write_text(text.replace(line, ""), encoding="utf-8")
    with pytest.raises(BridgeError) as error:
        _visual_environment(project, generation, log, screenshots)
    assert error.value.code == "VISUAL_ENVIRONMENT_UNAVAILABLE"


def test_font_changed_after_generation_is_rejected(tmp_path):
    project, generation, log, font, screenshots = fixture(tmp_path)
    font.write_bytes(b"changed external font")
    with pytest.raises(BridgeError) as error:
        _visual_environment(project, generation, log, screenshots)
    assert error.value.code == "ASSET_HASH_MISMATCH"


@pytest.mark.parametrize("kind", ["missing", "changed", "mixed", "jpeg"])
def test_actual_capture_size_cannot_be_invented(tmp_path, kind):
    project, generation, log, _, screenshots = fixture(tmp_path)
    if kind == "missing":
        screenshots = []
    elif kind == "changed":
        Path(screenshots[0]["path"]).write_bytes(b"changed PNG bytes")
    else:
        other = tmp_path / "other.png"
        Image.new("RGB", (41, 24)).save(other, format="JPEG" if kind == "jpeg" else "PNG")
        screenshots.append({"path": str(other), "sha256": file_hash(other)})
    with pytest.raises(BridgeError) as error:
        _visual_environment(project, generation, log, screenshots)
    assert error.value.code == ("VISUAL_IMAGE_CHANGED" if kind == "changed" else "VISUAL_ENVIRONMENT_UNAVAILABLE")
