from pathlib import Path

from PIL import Image
import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.generator import GenerationError, _asset_game_paths
from renpy_bridge.models import Project
from renpy_bridge.storage import Store


def model(hashes):
    return Project(project_id="path_fixture", name="路径测试", save_namespace="path_fixture",
                   assets={aid: {"asset_id": aid, "kind": "image", "path": f"assets/{aid}.png", "sha256": sha}
                           for aid, sha in hashes.items()})


def test_production_long_home_gets_loadable_short_paths():
    project = model({"background": "a" * 64, "alias": "a" * 64})
    target = Path("C:/Users/Example/AppData/Local/Packages/Example.Codex_0000000000000/LocalCache/Local/RenPyBridge/projects/p_00000000000000000000/generated/r00000006/job_" + "b" * 32)
    paths = _asset_game_paths(project, {aid: Path("asset.png") for aid in project.assets}, target)
    assert paths["background"] == paths["alias"] == "bridge_assets/" + "a" * 24 + ".png"
    assert len(str(target / "game" / paths["background"])) < 260
    assert len(str(target / "game" / ("bridge_assets/" + "a" * 64 + ".png"))) >= 260


def test_prefix_collision_is_rejected_not_silently_overwritten():
    project = model({"one": "a" * 24 + "b" * 40, "two": "a" * 24 + "c" * 40})
    with pytest.raises(GenerationError, match="ASSET_PATH_COLLISION"):
        _asset_game_paths(project, {aid: Path("asset.png") for aid in project.assets}, Path("C:/short"))


def test_still_excessive_path_is_rejected_before_engine_launch():
    project = model({"one": "a" * 64})
    with pytest.raises(GenerationError, match="ENGINE_PATH_TOO_LONG"):
        _asset_game_paths(project, {"one": Path("asset.png")}, Path("C:/" + "x" * 250))


@pytest.mark.parametrize("kind", ["audio", "font", "image"])
def test_script_cannot_enter_asset_library(tmp_path, kind):
    store = Store(tmp_path / "home")
    pid = store.create("白名单", "create")["project_id"]
    script = tmp_path / "malicious.rpy"
    script.write_text("init python:\n    raise RuntimeError('must not run')\n", encoding="utf-8")
    with pytest.raises(BridgeError) as error:
        store.register_assets(pid, 0, "script", [{"asset_id": "script", "kind": kind, "source_path": str(script)}])
    assert error.value.code == "INVALID_ASSET_FORMAT"
    assert store.load(pid).revision == 0
    assert not list((store.directory(pid) / "assets").glob("*.rpy"))


def test_renamed_image_cannot_commit(tmp_path):
    store = Store(tmp_path / "home")
    pid = store.create("实际格式", "create")["project_id"]
    path = tmp_path / "wrong.jpg"
    Image.new("RGB", (4, 4)).save(path, format="PNG")
    with pytest.raises(BridgeError) as error:
        store.register_assets(pid, 0, "renamed", [{"asset_id": "picture", "kind": "image", "source_path": str(path)}])
    assert error.value.code == "INVALID_ASSET_FORMAT"
    assert store.load(pid).revision == 0
