"""Archive fidelity/refusal tests; fake binaries here are never runtime evidence."""
from pathlib import Path
import importlib.util
import io
import json
import shutil
import sys
import zipfile
import uuid
import wave
from copy import deepcopy

import pytest
from PIL import Image
from renpy_bridge.models import Project
from renpy_bridge.storage import Store, atomic_json
from renpy_bridge.visual_regression import register_visual_baseline, compare_preview


SPEC = importlib.util.spec_from_file_location("package_delivery_tool", Path(__file__).resolve().parents[1] / "tools/package_delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = delivery
SPEC.loader.exec_module(delivery)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, dict):
        value = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    elif isinstance(value, str):
        value = value.encode("utf-8")
    path.write_bytes(value)
    return path


@pytest.fixture
def archive_fixture(tmp_path, monkeypatch):
    root, home = tmp_path / "源码 中文", tmp_path / "个人 安装"
    for name in delivery.SOURCE_FILES:
        put(root / name, '[project]\nversion="0.1.3"\n' if name == "pyproject.toml" else "source fixture\n")
    # Archive fidelity tests use their own deterministic PCM sample, without
    # reading a developer's historical speech recording or production evidence.
    sample_bytes = io.BytesIO()
    with wave.open(sample_bytes, "wb") as sample:
        sample.setnchannels(1)
        sample.setsampwidth(2)
        sample.setframerate(8000)
        sample.writeframes(bytes.fromhex("1000f0ff") * 80)
    sample_path = "tests/fixtures/audio/archive-tone.wav"
    original = put(root / sample_path, sample_bytes.getvalue())
    expected = "59383001bf9fc12603907bf1044b883e81fe415aad0ee31e535584eb86c2eeec"
    assert delivery.file_hash(original) == expected
    monkeypatch.setattr(delivery, "SOURCE_TEST_FIXTURES", {sample_path: expected})
    put(root / "src/renpy_bridge/__init__.py", '__version__ = "0.1.3"\n')
    put(root / "src/renpy_bridge/generator.py", 'GENERATOR_VERSION = "0.1.3"\n')
    put(root / "src/renpy_bridge/server.py", "# frozen source fixture\n")
    put(root / "tests/test_fixture.py", "# copied regression source\n")
    shutil.copytree(root / "src", home / "src")
    put(home / "plugin/renpy-bridge/.codex-plugin/plugin.json", {"name": "renpy-bridge", "version": "0.1.3"})
    put(home / "plugin/renpy-bridge/.mcp.json", {"mcpServers": {"renpy_bridge": {"command": str(home / ".runtime/python.exe")}}})
    put(home / "plugin/.agents/plugins/marketplace.json", {"name": "renpy-bridge-personal", "plugins": []})
    put(home / "plugin/renpy-bridge/skills/renpy-author/SKILL.md", "fixture skill\n")
    put(home / "runtimes/sdk-lock.json", {"version": "8.5.3.26051504", "sha256": "f" * 64, "relative_path": "runtimes/renpy-8.5.3"})
    pid = "project_test"
    project = home / "projects" / pid
    for name in delivery.PROJECT_TREES:
        (project / name).mkdir(parents=True, exist_ok=True)
    new_media = put(project / "assets/new.png", b"immutable current media")
    old_media = put(project / "assets/retired.png", b"immutable checkpoint media")
    old_model = {"project_id": pid, "schema_version": "1.0", "revision": 0, "assets": {"retired": {"path": "assets/retired.png", "sha256": delivery.file_hash(old_media)}}}
    model = {"project_id": pid, "schema_version": "1.0", "revision": 1, "assets": {"current": {"path": "assets/new.png", "sha256": delivery.file_hash(new_media)}}}
    put(project / "history/revisions/00000000.json", old_model)
    put(project / "history/revisions/00000001.json", model)
    put(project / "checkpoints/snap_0123456789abcdef.json", {"revision": 0, "model_hash": delivery.digest(old_model)})
    generated = project / "generated/r00000001/job_final"
    put(generated / "game/bridge_story.rpy", "# generated current native story\n")
    put(generated / "old-game/retained.rpyc", b"previous compiled statements")
    put(project / "generated/r00000000/job_previous/game/old.rpyc", b"runnable previous baseline")
    put(project / "generated/r00000001/.rb-sdk-state-job_final/saves/private.save", b"private SDK state")
    put(generated / "game/saves/private.save", b"private player save")
    put(project / "imports/import_fixture.json", {"staged": ["frame_future"]})
    put(project / "source_inputs/original.xlsx", b"original workbook snapshot")
    head = {"schema_version": "1.0", "revision": 1, "model_path": "history/revisions/00000001.json", "runnable_revision": 1, "runnable_project_path": "generated/r00000001/job_final"}
    put(project / "model/head.json", head)
    put(home / "projects/index.json", {"projects": {pid: {"name": "selected"}, "other_project": {"name": "private other"}}, "operations": {"create_selected": {"result": {"project_id": pid}}, "other": {"result": {"project_id": "other_project"}}}})
    independent = project / "releases/job_final/independent/rbgame_fixture-1.0-pc"
    exe = put(independent / "rbgame_fixture.exe", b"TEST FIXTURE ONLY; not executable")
    image = put(independent / "game/bridge_assets/abcdef.png", b"exact released media")
    put(independent / "game/saves/private.save", b"generated while testing; not in ZIP")
    package = project / "releases/job_final/rbgame_fixture-1.0-pc.zip"
    with zipfile.ZipFile(package, "x") as archive:
        for path in [exe, image]:
            archive.write(path, path.relative_to(independent.parent).as_posix())
    shot = put(root / "evidence/representative.png", b"hashed screenshot fixture")
    dev_shot = put(root / "projects/fixture_old/generated/job_example/tests/screenshots/old.png", b"indexed historical native screenshot")
    indexed = put(root / "evidence/historical/report.json", {
        "screenshots": [{"path": str(shot), "sha256": delivery.file_hash(shot)}, {"path": str(dev_shot), "sha256": delivery.file_hash(dev_shot)}],
        "old_asset": {"path": "assets/retired.png", "sha256": delivery.file_hash(old_media)}, "scope": "fixture"})
    put(root / "evidence/historical/summary.json", {"scope": "companion summary"})
    put(root / "docs/EVIDENCE.json", {"implementation_runs": [{"report_path": "evidence/historical/report.json", "report_sha256": delivery.file_hash(indexed)}]})
    report = {"kind": "build", "ok": True, "status": "passed", "independent_release_verified": True,
              "project_id": pid, "revision": 1, "model_hash": delivery.digest(model),
              "code_digest": delivery.core_hashes(root)[1], "generator_version": "0.1.3",
              "sdk_version": "8.5.3.26051504", "generation": {"project_path": str(generated)},
              "release": {"path": str(package), "sha256": delivery.file_hash(package), "bytes": package.stat().st_size,
                          "entries": 2, "executable": str(exe), "executable_sha256": delivery.file_hash(exe),
                          "independent_directory": str(independent), "qa_harness_included": False},
              "visual_review": "not_run", "audio_output_verification": "not_run"}
    report_path = put(home / "jobs/job_final/report.json", report)
    return {"root": root, "home": home, "project": project, "pid": pid, "report": report,
            "report_path": report_path, "output": tmp_path / "交付 新目录", "old_media": old_media,
            "package": package, "independent": independent, "exe": exe}


def run(f, **kwargs):
    return delivery.package_delivery(f["home"], f["pid"], f["report_path"], f["output"], f["root"], **kwargs)


def test_archive_preserves_history_baselines_release_and_hashes_without_private_state(archive_fixture):
    f = archive_fixture
    originals = {str(p): delivery.file_hash(p) for base in [f["home"], f["root"]] for p in base.rglob("*") if p.is_file()}
    result = run(f)
    assert result["status"] == "complete"
    manifest = delivery.read_json(f["output"] / "manifest.json")
    for item in manifest["files"]:
        copied = f["output"] / item["path"]
        assert delivery.file_hash(copied) == item["sha256"]
        assert copied.stat().st_size == item["bytes"]
    files = {item["path"] for item in manifest["files"]}
    assert any(name.endswith("old-game/retained.rpyc") for name in files)
    assert any(name.endswith("job_previous/game/old.rpyc") for name in files)
    assert any(name.endswith("assets/retired.png") for name in files)
    assert any(name.endswith("imports/import_fixture.json") for name in files)
    assert any(name.endswith("source_inputs/original.xlsx") for name in files)
    assert "bridge-source/evidence/historical/summary.json" in files
    assert len([name for name in files if name.startswith("evidence/key-screenshots/")]) == 2
    assert not any("private.save" in name or ".rb-sdk-state-" in name for name in files)
    assert not any(".runtime" in name or name.startswith("runtimes/") for name in files)
    selected = delivery.read_json(f["output"] / "managed-home/projects/index.json")
    assert set(selected["projects"]) == {f["pid"]} and set(selected["operations"]) == {"create_selected"}
    assert delivery.file_hash(f["output"] / "release" / f["package"].name) == delivery.file_hash(f["package"])
    assert manifest["archive_is_runtime_acceptance"] is False
    assert manifest["native_acceptance_preserved_from_report"]["audio_output_verification"] == "not_run"
    assert originals == {str(p): delivery.file_hash(p) for base in [f["home"], f["root"]] for p in base.rglob("*") if p.is_file()}


def test_dry_run_validates_but_creates_nothing(archive_fixture):
    f = archive_fixture
    assert run(f, dry_run=True)["status"] == "dry_run"
    assert not f["output"].exists()


def test_existing_output_is_never_overwritten(archive_fixture):
    f = archive_fixture
    marker = put(f["output"] / "keep.txt", "keep exact bytes")
    with pytest.raises(delivery.DeliveryError, match="already exists"):
        run(f)
    assert marker.read_text() == "keep exact bytes"
    assert list(f["output"].iterdir()) == [marker]


@pytest.mark.parametrize("case", ["revision", "model_hash", "code_digest", "installed_code", "release_zip", "executable", "extracted_media", "evidence_hash"])
def test_stale_or_changed_inputs_fail_before_output_publication(archive_fixture, case):
    f = archive_fixture
    if case in {"revision", "model_hash", "code_digest"}:
        f["report"][case] = 0 if case == "revision" else "0" * 64
        put(f["report_path"], f["report"])
    elif case == "installed_code":
        put(f["home"] / "src/renpy_bridge/server.py", "# different production version\n")
    elif case == "release_zip":
        with f["package"].open("ab") as stream:
            stream.write(b"different release")
    elif case == "executable":
        put(f["exe"], b"changed exe")
    elif case == "extracted_media":
        put(f["independent"] / "game/bridge_assets/abcdef.png", b"changed released media")
    else:
        put(f["root"] / "evidence/historical/report.json", {"changed": True})
    with pytest.raises(delivery.DeliveryError):
        run(f)
    assert not f["output"].exists()


def test_retired_checkpoint_asset_cannot_be_silently_lost(archive_fixture):
    f = archive_fixture
    f["old_media"].unlink()
    with pytest.raises(delivery.DeliveryError, match="Historical managed media"):
        run(f)
    assert not f["output"].exists()


def test_checkpoint_tamper_rejected_without_output(archive_fixture):
    f = archive_fixture
    previous = delivery.read_json(f["project"] / "history/revisions/00000000.json")
    previous["extra_changed_story"] = True
    put(f["project"] / "history/revisions/00000000.json", previous)
    with pytest.raises(delivery.DeliveryError, match="Checkpoint model hash"):
        run(f)
    assert not f["output"].exists()


@pytest.mark.parametrize("field", ["model_path", "runnable_project_path"])
def test_relative_traversal_is_rejected(archive_fixture, field):
    f = archive_fixture
    head = delivery.read_json(f["project"] / "model/head.json")
    head[field] = "../../outside.json"
    put(f["project"] / "model/head.json", head)
    with pytest.raises(delivery.DeliveryError, match="relative path"):
        run(f)
    assert not f["output"].exists()


def test_zip_traversal_never_reaches_output(archive_fixture):
    f = archive_fixture
    f["package"].unlink()
    with zipfile.ZipFile(f["package"], "x") as archive:
        archive.writestr("../escaped.exe", b"bad")
    f["report"]["release"].update(sha256=delivery.file_hash(f["package"]), bytes=f["package"].stat().st_size, entries=1)
    put(f["report_path"], f["report"])
    with pytest.raises(delivery.DeliveryError, match="relative path"):
        run(f)
    assert not f["output"].exists() and not (f["output"].parent / "escaped.exe").exists()


def test_copy_race_keeps_partial_failure_evidence_and_never_claims_complete(archive_fixture, monkeypatch):
    f = archive_fixture
    real_copy = delivery.copy_verified
    fired = False

    def change_source(item, output):
        nonlocal fired
        if not fired:
            fired = True
            item.source.write_bytes(b"changed after preflight")
        return real_copy(item, output)

    monkeypatch.setattr(delivery, "copy_verified", change_source)
    with pytest.raises(delivery.DeliveryError, match="changed before copy"):
        run(f)
    assert delivery.read_json(f["output"] / "FAILED.json")["status"] == "incomplete"
    assert not (f["output"] / "manifest.json").exists()
    with pytest.raises(delivery.DeliveryError, match="already exists"):
        run(f)


def test_linked_managed_tree_is_rejected(archive_fixture, tmp_path):
    import os
    import subprocess

    f = archive_fixture
    outside = tmp_path / "not managed"
    put(outside / "outside.png", b"do not archive")
    linked = f["project"] / "assets" / "linked"
    if os.name == "nt":
        command = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside)], capture_output=True)
        assert command.returncode == 0, command.stderr
    else:
        linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(delivery.DeliveryError, match="reparse points"):
        run(f)
    assert not f["output"].exists()


def test_indexed_screenshot_cannot_read_an_arbitrary_external_file(archive_fixture, tmp_path):
    f = archive_fixture
    external = put(tmp_path / "unrelated.png", b"unrelated private image")
    indexed = put(f["root"] / "evidence/historical/report.json", {"screenshots": [{"path": str(external), "sha256": delivery.file_hash(external)}]})
    put(f["root"] / "docs/EVIDENCE.json", {"implementation_runs": [{"report_path": "evidence/historical/report.json", "report_sha256": delivery.file_hash(indexed)}]})
    with pytest.raises(delivery.DeliveryError, match="Screenshot leaves"):
        run(f)
    assert not f["output"].exists()


@pytest.mark.parametrize("folder,allowed", [("windows-demo", True), ("windows-demo-private", False)])
def test_indexed_chinese_path_package_screenshots_use_only_fixed_output_root(archive_fixture, folder, allowed):
    f = archive_fixture
    screenshot = put(f["root"] / "outputs" / folder / "雨停之前 演示/run/运行 包/tests/screenshots/frame.png",
                     b"hash-preserved Chinese-and-space path native screenshot fixture")
    indexed = put(f["root"] / "evidence/historical/report.json",
                  {"screenshots": [{"path": str(screenshot), "sha256": delivery.file_hash(screenshot)}]})
    put(f["root"] / "docs/EVIDENCE.json",
        {"implementation_runs": [{"report_path": "evidence/historical/report.json", "report_sha256": delivery.file_hash(indexed)}]})
    if not allowed:
        with pytest.raises(delivery.DeliveryError, match="Screenshot leaves"):
            run(f)
        assert not f["output"].exists()
        return
    run(f)
    manifest = delivery.read_json(f["output"] / "manifest.json")
    record = next(item for item in manifest["files"] if item["category"] == "key_screenshot")
    assert record["sha256"] == delivery.file_hash(screenshot)
    assert (f["output"] / record["path"]).read_bytes() == screenshot.read_bytes()


def test_indexed_windows_demo_screenshot_rejects_junction_to_external_root(archive_fixture, tmp_path):
    import os
    import subprocess

    f = archive_fixture
    external = tmp_path / "unrelated private screenshots"
    screenshot = put(external / "private.png", b"must not follow allowed-root junction")
    linked = f["root"] / "outputs/windows-demo/linked"
    linked.parent.mkdir(parents=True)
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(external)], capture_output=True)
        assert result.returncode == 0, result.stderr
    else:
        linked.symlink_to(external, target_is_directory=True)
    indexed = put(f["root"] / "evidence/historical/report.json",
                  {"screenshots": [{"path": str(linked / "private.png"), "sha256": delivery.file_hash(screenshot)}]})
    put(f["root"] / "docs/EVIDENCE.json",
        {"implementation_runs": [{"report_path": "evidence/historical/report.json", "report_sha256": delivery.file_hash(indexed)}]})
    with pytest.raises(delivery.DeliveryError, match="reparse points"):
        run(f)
    assert not f["output"].exists()


def reviewed_visual_fixture(f):
    """Real PNG registration using synthetic native-job metadata, not SDK QA."""
    project = Project.model_validate({
        "project_id": f["pid"], "save_namespace": f["pid"], "name": "归档恢复视觉夹具", "revision": 1,
        "canvas": {"width": 320, "height": 240}, "entry_frame_id": "CON",
        "acts": {"act": {"act_id": "act", "name": "第一幕", "frame_ids": ["CON"]}},
        "frames": {"CON": {"frame_id": "CON", "act_id": "act", "order": 0,
                          "dialogue": {"text": "合法 Windows 保留名帧身份"}, "flow": {"kind": "end"}}},
    })
    put(f["project"] / "history/revisions/00000001.json", project.model_dump(mode="json"))
    f["report"]["model_hash"] = delivery.digest(project.model_dump(mode="json"))
    put(f["report_path"], f["report"])
    job_id = "job_" + uuid.uuid4().hex
    target = f["project"] / f"generated/r00000001/{job_id}"
    font = put(target / "game/SourceHanSansLite.ttf", b"fixture font, not native runtime evidence")
    put(target / "bridge-manifest.json", {"project_id": f["pid"], "revision": 1,
        "sdk_version": "8.5.3.26051504", "chinese_font_sha256": delivery.file_hash(font)})
    png = target / "tests/screenshots/native_con.png"
    png.parent.mkdir(parents=True)
    with Image.new("RGB", (280, 210), (45, 90, 120)) as image:
        image.save(png)
    report = {"job_id": job_id, "kind": "preview", "project_id": f["pid"], "revision": 1,
        "model_hash": delivery.digest(project.model_dump(mode="json")), "ok": True, "status": "passed",
        "sdk_version": "8.5.3.26051504", "generator_version": "0.1.3",
        "generation": {"project_path": str(target)},
        "commands": {name: {"exit_code": 0, "timed_out": False, "cleanup_complete": True}
                     for name in ("compile", "lint", "native-tests")},
        "screenshots": [{"frame_id": "CON", "revision": 1, "path": str(png), "sha256": delivery.file_hash(png)}],
        "visual_environment": {"sdk_version": "8.5.3.26051504", "language": "schinese",
            "renderer": {"backend": "gl2", "vendor": "fixture", "renderer": "fixture GPU", "version": "4.6"},
            "resolution": [320, 240], "capture_resolution": [280, 210],
            "font_sha256": delivery.file_hash(font)}}
    put(f["home"] / f"jobs/{job_id}/status.json", {"job_id": job_id, "kind": "preview",
        "project_id": f["pid"], "revision": 1, "state": "succeeded"})
    put(f["home"] / f"jobs/{job_id}/report.json", report)
    result = register_visual_baseline(Store(f["home"]), f["pid"], 1, "review-archive-fixture", job_id,
                                     "已审查本单元测试画面，验证归档恢复时保留登记基准。")
    return project, report, result


def test_reviewed_visual_baseline_restores_comparison_at_new_root_without_original_project(archive_fixture):
    f = archive_fixture
    project, preview_report, accepted = reviewed_visual_fixture(f)
    original_base = f["project"] / "visual_baselines"
    original_bytes = {p.relative_to(original_base).as_posix(): p.read_bytes()
                      for p in original_base.rglob("*") if p.is_file()}
    run(f)
    restored_home = f["output"] / "managed-home"
    restored_project = restored_home / "projects" / f["pid"]
    restored_base = restored_project / "visual_baselines"
    assert {p.relative_to(restored_base).as_posix(): p.read_bytes()
            for p in restored_base.rglob("*") if p.is_file()} == original_bytes
    manifest = delivery.read_json(Path(accepted["manifest_path"]))
    assert Path(manifest["frames"]["CON"]["image_path"]).name.startswith("frame_")
    assert manifest["environment"]["resolution"] == [320, 240]
    assert manifest["environment"]["capture_resolution"] == [280, 210]
    relocated = deepcopy(preview_report)
    relocated["generation"]["project_path"] = str(restored_project / Path(preview_report["generation"]["project_path"]).relative_to(f["project"]))
    for screenshot in relocated["screenshots"]:
        screenshot["path"] = str(restored_project / Path(screenshot["path"]).relative_to(f["project"]))
    moved = f["project"].with_name("original_project_retained")
    assert f["project"].resolve().is_relative_to(f["home"].resolve() / "projects")
    assert moved.resolve().is_relative_to(f["home"].resolve() / "projects")
    f["project"].rename(moved)
    restored_store = Store(restored_home)
    assert restored_store.load(f["pid"]) == project
    assert compare_preview(restored_store, project, relocated)["status"] == "passed"
    assert "visual_baselines/" in (f["output"] / "RESTORATION.md").read_text()


@pytest.mark.parametrize("corruption", ["image", "pointer"])
def test_corrupt_reviewed_baseline_cannot_be_archived_as_restorable(archive_fixture, corruption):
    f = archive_fixture
    _, _, accepted = reviewed_visual_fixture(f)
    base = f["project"] / "visual_baselines"
    if corruption == "image":
        manifest = delivery.read_json(Path(accepted["manifest_path"]))
        put(base / manifest["frames"]["CON"]["image_path"], b"altered baseline PNG")
        message = "cannot be restored"
    else:
        index = delivery.read_json(base / "index.json")
        index["baselines"][accepted["baseline_id"]]["manifest_path"] = "../source_inputs/original.xlsx"
        put(base / "index.json", index)
        message = "leaves its immutable run"
    with pytest.raises(delivery.DeliveryError, match=message):
        run(f)
    assert not f["output"].exists()


def test_zip_member_allowlist_preserves_runtime_bytecode_caches_and_future_test_members(archive_fixture):
    """A source archive filter must never remove files from the tested player."""
    f = archive_fixture
    runtime_members = {
        "renpy/__pycache__/py3analysis.cpython-312.pyc": b"source-less engine bytecode fixture",
        "lib/py3-windows-x86_64/python3.12/collections/__pycache__/__init__.cpython-312.pyc": b"stdlib bytecode fixture",
        "futuretests/__pycache__/controller.cpython-312.pyc": b"future original runtime member",
        "game/cache/opaque_runtime_cache.bin": b"original ZIP-listed runtime bytes",
        "tests/original_member.txt": b"original ZIP member; not a newly written test log",
    }
    with zipfile.ZipFile(f["package"], "a") as archive:
        for relative, data in runtime_members.items():
            path = put(f["independent"] / relative, data)
            archive.write(path, path.relative_to(f["independent"].parent).as_posix())
    with zipfile.ZipFile(f["package"]) as archive:
        expected = {item.filename for item in archive.infolist() if not item.is_dir()}
        f["report"]["release"].update(entries=len(archive.infolist()), bytes=f["package"].stat().st_size,
                                     sha256=delivery.file_hash(f["package"]))
    put(f["report_path"], f["report"])
    # Irrelevant source cache remains excluded by the separate source policy.
    put(f["root"] / "tests/__pycache__/should_not_ship.cpython-312.pyc", b"development-only source cache")
    run(f)
    manifest = delivery.read_json(f["output"] / "manifest.json")
    actual = {item["path"][len("release/independent/"):] for item in manifest["files"]
              if item["category"] == "independent_release"}
    assert actual == expected
    assert not any("should_not_ship" in item["path"] for item in manifest["files"])
    for relative, data in runtime_members.items():
        copied = f["output"] / "release/independent" / f["independent"].name / relative
        assert copied.read_bytes() == data


def test_archived_source_collects_actual_verifier_and_audio_tests_without_workspace_tool_imports(archive_fixture, tmp_path):
    import os
    import subprocess

    f = archive_fixture
    actual_root = Path(__file__).resolve().parents[1]
    source_tooling = (*delivery.SOURCE_SCRIPT_FILES, ".gitignore", ".gitattributes")
    for name in source_tooling:
        put(f["root"] / name, (actual_root / name).read_bytes())
    for original in (actual_root / "src/renpy_bridge").rglob("*.py"):
        relative = original.relative_to(actual_root)
        put(f["root"] / relative, original.read_bytes())
        put(f["home"] / relative, original.read_bytes())
    test_names = ("test_demo_authoring_verifier.py", "test_demo_audio_probe.py")
    for name in test_names:
        put(f["root"] / "tests" / name, (actual_root / "tests" / name).read_bytes())
    f["report"]["code_digest"] = delivery.core_hashes(f["root"])[1]
    put(f["report_path"], f["report"])

    external = tmp_path / "private unselected dependency"
    put(external / "private.py", b"not in tracked source allowlist")
    linked = f["root"] / "tools/node_modules"
    if os.name == "nt":
        command = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(external)], capture_output=True)
        assert command.returncode == 0, command.stderr
    else:
        linked.symlink_to(external, target_is_directory=True)
    run(f)
    source = f["output"] / "bridge-source"
    manifest = delivery.read_json(f["output"] / "manifest.json")
    assert not any("node_modules" in item["path"] for item in manifest["files"])
    for name in source_tooling:
        assert (source / name).read_bytes() == (actual_root / name).read_bytes()
    with zipfile.ZipFile(f["output"] / "renpy-bridge-source-0.1.3.zip") as archive:
        for name in source_tooling:
            assert archive.read("renpy-bridge-source-0.1.3/" + name) == (actual_root / name).read_bytes()
    for name, expected in delivery.SOURCE_TEST_FIXTURES.items():
        assert delivery.file_hash(source / name) == expected

    env = os.environ.copy()
    env["PYTHONPATH"] = str(source / "src") + os.pathsep + str(source)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Collection loads the real archived helper modules; it launches no SDK,
    # speaker or recorder, and cannot fall back to the workspace's tools folder.
    collected = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                                *["tests/" + name for name in test_names]], cwd=source, env=env,
                               capture_output=True, text=True, timeout=45)
    assert collected.returncode == 0, collected.stdout + collected.stderr
    assert "collected" in collected.stdout and "test_declared_summary_rejects" in collected.stdout
    assert "test_prepare_only" in collected.stdout


def test_explicit_selected_source_script_parent_junction_is_rejected(archive_fixture, tmp_path):
    import os
    import subprocess

    f = archive_fixture
    scripts = f["root"] / "scripts"
    retained = f["root"] / "unlinked-original-scripts"
    assert scripts.resolve().parent == retained.resolve().parent == f["root"].resolve()
    scripts.rename(retained)
    external = tmp_path / "external scripts"
    put(external / "environment_probe.py", b"must not follow selected-source junction")
    if os.name == "nt":
        command = subprocess.run(["cmd", "/c", "mklink", "/J", str(scripts), str(external)], capture_output=True)
        assert command.returncode == 0, command.stderr
    else:
        scripts.symlink_to(external, target_is_directory=True)
    with pytest.raises(delivery.DeliveryError, match="reparse points"):
        run(f)
    assert not f["output"].exists()


@pytest.mark.parametrize("failure", ["missing", "changed"])
def test_sha_pinned_existing_voice_fixture_is_required_before_archive(archive_fixture, failure):
    f = archive_fixture
    name = next(iter(delivery.SOURCE_TEST_FIXTURES))
    original = f["root"] / name
    if failure == "missing":
        original.unlink()
    else:
        original.write_bytes(b"changed original voice sample")
    with pytest.raises(delivery.DeliveryError, match="input missing|hash differs"):
        run(f)
    assert not f["output"].exists()
