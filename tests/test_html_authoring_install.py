"""Verify installed authoring assets and narrow, recoverable publication."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("html_authoring_source_installer_test", ROOT / "scripts/install_local.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def minimal_source(source):
    values = {"pyproject.toml": "[project]\nname='fixture'\n", "requirements.lock": "setuptools==80.9.0\n",
              "src/renpy_bridge/server.py": "# fixture\n", "src/renpy_bridge/worker.py": "# fixture\n",
              "src/renpy_bridge/minigame_qa.py": "# fixture\n", "tools/probe_html_cdp.mjs": "// driver\n",
              "plugin/renpy-bridge/.codex-plugin/plugin.json": json.dumps({"name": "renpy-bridge", "mcpServers": "./.mcp.json", "skills": "./skills/"}),
              "plugin/.agents/plugins/marketplace.json": json.dumps({"plugins": [{"name": "renpy-bridge", "source": {"source": "local", "path": "./renpy-bridge"}}]})}
    for name, value in values.items():
        write(source / name, value)
    return source


def add_authoring(source):
    shutil.copytree(ROOT / installer.AUTHORING_TREE, source / installer.AUTHORING_TREE)
    shutil.copy2(ROOT / installer.AUTHORING_GUIDE, source / installer.AUTHORING_GUIDE)


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_canonical_sdk_and_complete_actual_templates_stage_with_original_bytes(tmp_path):
    source = minimal_source(tmp_path / "source")
    add_authoring(source)
    write(source / "examples/private-game/index.html", "user example")
    write(source / "examples/html_minigames/production/v1.0.0/keep.js", "old package")
    write(source / "docs/outside-curated-authoring.js", "ignored extra extension")
    files = installer.source_files(source)
    hashes = {path.relative_to(source).as_posix(): installer.sha256(path) for path in files}
    stage = tmp_path / "stage"
    stage.mkdir()
    names = installer.stage_code(source, files, stage, tmp_path / "home", hashes)
    assert installer.AUTHORING_TREE in names and installer.AUTHORING_GUIDE in names
    assert "examples" not in names and "examples/html_minigames/production" not in names
    assert snapshot(stage / installer.AUTHORING_TREE) == snapshot(ROOT / installer.AUTHORING_TREE)
    assert (stage / installer.AUTHORING_GUIDE).read_bytes() == (ROOT / installer.AUTHORING_GUIDE).read_bytes()
    assert not (stage / "examples/private-game").exists()
    assert not (stage / "examples/html_minigames/production/v1.0.0").exists()
    assert not (stage / "docs/outside-curated-authoring.js").exists()
    canonical = (stage / installer.AUTHORING_TREE / "sdk/bridge-sdk.js").read_bytes()
    for package in ("puzzle", "timed", "checkpoint"):
        root = stage / installer.AUTHORING_TREE / package
        assert (root / "bridge-sdk.js").read_bytes() == canonical
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        for member in manifest["files"]:
            path = root / member["path"]
            assert path.stat().st_size == member["bytes"]
            assert installer.sha256(path) == member["sha256"]
    assert (stage / installer.AUTHORING_TREE / "timed/tap.wav").is_file()


@pytest.mark.parametrize("member", ["sdk/bridge-sdk.js", "puzzle/instructions.txt", "timed/tap.wav", "checkpoint/manifest.json"])
def test_incomplete_authoring_bundle_rejected_before_stage(tmp_path, member):
    source = minimal_source(tmp_path / "source")
    add_authoring(source)
    (source / installer.AUTHORING_TREE / member).unlink()
    with pytest.raises(installer.InstallError, match="authoring SDK/templates are incomplete"):
        installer.source_files(source)


def test_unknown_authoring_resource_is_rejected_instead_of_silently_dropped(tmp_path):
    source = minimal_source(tmp_path / "source")
    add_authoring(source)
    write(source / installer.AUTHORING_TREE / "timed/unlisted.exe", "unexpected executable")
    with pytest.raises(installer.InstallError, match="Unsupported file"):
        installer.source_files(source)


@pytest.mark.parametrize("previous", [True, False])
def test_version_and_guide_publication_and_rollback_preserve_sibling_user_files(tmp_path, previous):
    home = tmp_path / "home"
    run = home / ".installer/runs/authoring"
    siblings = {"examples/private-game/index.html": "private game", "examples/html_minigames/production/v1.0.0/keep.js": "legacy immutable package",
                "examples/html_minigames/production/user-notes.md": "author notes", "tools/private-helper.py": "private tool"}
    for path, content in siblings.items():
        write(home / path, content)
    if previous:
        write(home / installer.AUTHORING_TREE / "sdk/bridge-sdk.js", "previous curated SDK")
        write(home / installer.AUTHORING_GUIDE, "previous guide")
    before = snapshot(home / "examples")
    shutil.copytree(ROOT / installer.AUTHORING_TREE, run / "code" / installer.AUTHORING_TREE)
    shutil.copy2(ROOT / installer.AUTHORING_GUIDE, run / "code" / installer.AUTHORING_GUIDE)
    names = [installer.AUTHORING_TREE, installer.AUTHORING_GUIDE]
    backup = home / "backups" / ("install-" + run.name)
    backup.mkdir(parents=True)
    installer.copy_backup(home, backup, names)
    journal = installer.publish_code(home, run, names)
    assert snapshot(home / installer.AUTHORING_TREE) == snapshot(ROOT / installer.AUTHORING_TREE)
    for path, content in siblings.items():
        assert (home / path).read_text(encoding="utf-8") == content
    installer.rollback_publication(home, run, journal)
    assert snapshot(home / "examples") == before
    for path, content in siblings.items():
        assert (home / path).read_text(encoding="utf-8") == content


def test_partial_authoring_publication_can_restore_old_version_and_guide(tmp_path, monkeypatch):
    home = tmp_path / "home"
    run = home / ".installer/runs/authoring-failure"
    write(home / installer.AUTHORING_TREE / "sdk/bridge-sdk.js", "original SDK")
    write(home / installer.AUTHORING_GUIDE, "original guide")
    write(home / "examples/html_minigames/production/notes.txt", "user-owned notes")
    before = snapshot(home / "examples")
    write(run / "code" / installer.AUTHORING_TREE / "sdk/bridge-sdk.js", "new SDK")
    write(run / "code" / installer.AUTHORING_GUIDE, "new guide")
    replace = installer.os.replace
    def fail_guide_publication(original, target):
        if Path(original) == run / "code" / installer.AUTHORING_GUIDE:
            raise OSError("injected guide publish failure")
        return replace(original, target)
    monkeypatch.setattr(installer.os, "replace", fail_guide_publication)
    with pytest.raises(OSError, match="injected guide"):
        installer.publish_code(home, run, [installer.AUTHORING_TREE, installer.AUTHORING_GUIDE])
    journal = installer.read_json(run / "publication.json")
    installer.rollback_publication(home, run, journal)
    assert snapshot(home / "examples") == before


def test_template_source_change_during_staging_is_detected(tmp_path):
    source = minimal_source(tmp_path / "source")
    add_authoring(source)
    files = installer.source_files(source)
    hashes = {path.relative_to(source).as_posix(): installer.sha256(path) for path in files}
    write(source / installer.AUTHORING_TREE / "sdk/bridge-sdk.js", "changed after plan")
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(installer.InstallError, match="source changed while staging"):
        installer.stage_code(source, files, stage, tmp_path / "home", hashes)


def test_root_readme_is_copied_and_publication_rollback_restores_its_original_bytes(tmp_path):
    source = minimal_source(tmp_path / "source")
    add_authoring(source)
    write(source / "README.md", "# Install guide\n[Usage](docs/USAGE.md)\n")
    write(source / "docs/USAGE.md", "[Start](../README.md)\n")
    files = installer.source_files(source)
    assert source / "README.md" in files
    home = tmp_path / "home"
    home.mkdir()
    previous = b"# previous user install guide\r\n"
    (home / "README.md").write_bytes(previous)
    run = home / ".installer/runs/readme"
    stage = run / "code"
    stage.mkdir(parents=True)
    hashes = {path.relative_to(source).as_posix(): installer.sha256(path) for path in files}
    names = installer.stage_code(source, files, stage, home, hashes)
    assert "README.md" in names
    backup = home / "backups" / ("install-" + run.name)
    backup.mkdir(parents=True)
    installer.copy_backup(home, backup, names)
    journal = installer.publish_code(home, run, names)
    assert (home / "README.md").read_bytes() == (source / "README.md").read_bytes()
    assert (home / "docs/USAGE.md").read_bytes() == (source / "docs/USAGE.md").read_bytes()
    installer.rollback_publication(home, run, journal)
    assert (home / "README.md").read_bytes() == previous
    assert not (home / "docs").exists()
