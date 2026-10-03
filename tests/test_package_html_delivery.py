"""HTML source archive fidelity/refusal tests, never runtime acceptance.

ZIPs and package trees are real. All executables, SDK/window results and license
metadata below are explicitly inert test fixtures and are never executed.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from renpy_bridge.minigame_packages import validate_directory

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("html_delivery_tool", ROOT / "tools/package_html_delivery.py")
delivery = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = delivery
SPEC.loader.exec_module(delivery)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode() if isinstance(value, dict)
                     else value.encode() if isinstance(value, str) else value)
    return path


def record(path):
    return {"sha256": delivery.file_hash(path), "bytes": path.stat().st_size}


@pytest.fixture
def fixture(tmp_path):
    root, home = tmp_path / "source", tmp_path / "managed"
    for name in delivery.SOURCE_FILES:
        put(root / name, "fixture dependency source\n")
    put(root / "requirements.lock", "fixture-package==1.0\n")
    for name in delivery.SOURCE_TREES:
        (root / name).mkdir(parents=True, exist_ok=True)
    put(root / "src/renpy_bridge/__init__.py", '__version__ = "0.1.3"\n')
    put(root / "src/renpy_bridge/generator.py", 'GENERATOR_VERSION = "0.1.3"\n')
    put(root / "src/renpy_bridge/minigame_runtime.py", "# immutable source fixture\n")
    shutil.copytree(root / "src", home / "src")
    put(root / "tools/package_html_delivery.py", "# recoverable new delivery tool fixture\n")
    put(root / "docs/HTML_MINIGAME_TASKS.json", {"done": False, "scope": "fixture"})
    put(root / "docs/TASKS.json", {"old_46_tasks": "preserved fixture"})
    for name in ("puzzle", "timed", "checkpoint"):
        shutil.copytree(ROOT / "examples/html_minigames/production/v1.0.1" / name,
                        root / "examples/html_minigames/production/v1.0.1" / name)
    native = root / "native/html_minigame_embedded_host"
    for name in ("Program.cs", "NativeParent.cs", "HtmlMinigameEmbeddedHost.csproj", "packages.lock.json", "app.manifest"):
        put(native / name, "authoritative native build source fixture: " + name)
    native_files = {path.name: record(path) for path in native.iterdir()}
    native_record = {"root": "native/html_minigame_embedded_host", "files": native_files,
                     "tree_sha256": delivery.digest(native_files)}
    site = root / ".venv/Lib/site-packages/fixture_package-1.0.dist-info"
    put(site / "METADATA", "Metadata-Version: 2.4\nName: fixture-package\nVersion: 1.0\nLicense-File: LICENSE\n\n")
    license_path = put(site / "licenses/LICENSE", "Unit test license fixture; no redistributed real dependency.\n")
    sdk_lock = {"version": "8.5.3.26051504", "sha256": "f" * 64}
    put(home / "runtimes/sdk-lock.json", sdk_lock)
    put(home / "runtimes/renpy-8.5.3/LICENSE.txt", "SDK license fixture\n")
    dependencies = home / "runtimes/html-host-dependencies/nuget/microsoft.web.webview2/1.0.4258.31"
    put(dependencies / "LICENSE.txt", "WebView2 SDK license fixture\n")
    put(dependencies / "NOTICE.txt", "WebView2 SDK notice fixture\n")
    put(dependencies / "fixture.nuspec", "<package />")
    host_root = home / "runtimes/html-minigame-embedded-host/win-x64"
    host_exe = put(host_root / "HtmlMinigameEmbeddedHost.exe", b"INERT UNIT FIXTURE; NEVER EXECUTED")
    host_build = {"native_source": native_record, "build": {"exit_code": 0, "self_contained": True,
        "files": [{"path": host_exe.name, **record(host_exe)}]}}
    host_report = put(host_root / "build-report.json", host_build)
    host = {"relative_executable": "html-runtime/win-x64/" + host_exe.name, "sha256": record(host_exe)["sha256"],
            "files": {path.name: record(path) for path in host_root.iterdir()}, "window_mode": "embedded_same_top_level_window", "footer_fraction": 0.1}
    browser_version = "154.0.4258.53"
    browser_root = home / ("runtimes/webview2-fixed-" + browser_version) / "FixedVersionRuntime"
    browser_exe = put(browser_root / "msedgewebview2.exe", b"INERT BROWSER FIXTURE; NEVER EXECUTED")
    put(browser_root / "WidevineCdm/LICENSE", "Browser component license fixture\n")
    browser_lock = {"version": browser_version, "signature_status": "Valid", "signer": "CN=Microsoft Corporation, UNIT FIXTURE",
                    "exe_sha256": delivery.file_hash(browser_exe), "relative_runtime_path": browser_root.relative_to(home).as_posix()}
    browser_lock_path = put(browser_root.parent / "runtime-lock.json", browser_lock)
    fixed = {"version": browser_version, "relative_root": "html-runtime/webview2-fixed",
             "files": {path.relative_to(browser_root).as_posix(): record(path) for path in browser_root.rglob("*") if path.is_file()}}
    pid = "project_html"
    project = home / "projects" / pid
    old_info = validate_directory(ROOT / "examples/html_minigames/production/timed")
    info = validate_directory(ROOT / "examples/html_minigames/production/v1.0.1/timed")
    for package in (old_info, info):
        shutil.copytree(package.root, project / "minigames" / package.digest)
    game = {"game_id": "prod_timed", "package_digest": info.digest, "package_version": "1.0.1",
            "path": "minigames/" + info.digest, "manifest": info.manifest}
    previous_game = {**game, "package_digest": old_info.digest, "package_version": "1.0.0",
                     "path": "minigames/" + old_info.digest, "manifest": old_info.manifest}
    flow = {"kind": "minigame", "game_id": "prod_timed", "instance_id": "signal",
            "result_branches": [{"branch_id": outcome, "outcome": outcome, "target_frame_id": outcome} for outcome in ("win", "lose")],
            "cancel_target_frame_id": "cancel", "error_target_frame_id": "error", "fallback_target_frame_id": "fallback"}
    model = {"schema_version": "1.1", "project_id": pid, "revision": 1, "save_namespace": pid,
             "frames": {"entry": {"frame_id": "entry", "flow": flow}}, "minigames": {"prod_timed": game}, "assets": {}}
    previous_model = {**deepcopy(model), "revision": 0, "minigames": {"prod_timed": previous_game}}
    put(project / "history/revisions/00000000.json", previous_model)
    put(project / "history/revisions/00000001.json", model)
    put(project / "checkpoints/snap_test.json", {"revision": 0, "model_hash": delivery.digest(previous_model)})
    generated = project / "generated/r00000001/final"

    def installed(package):
        return {"game_id": package.manifest["game_id"], "package_digest": package.digest,
                "package_version": package.manifest["version"], "package_root": "game/bridge_minigames/" + package.digest,
                "manifest": package.manifest, "files": {item.path: {"sha256": item.sha256, "bytes": item.bytes} for item in package.files}}

    current_installed, old_installed = installed(info), installed(old_info)
    for package in (info, old_info):
        shutil.copytree(package.root, generated / "game/bridge_minigames" / package.digest)
    shutil.copytree(host_root, generated / "html-runtime/win-x64")
    shutil.copytree(browser_root, generated / "html-runtime/webview2-fixed")
    config = {"schema_version": "1.0", "project_id": pid, "revision": 1, "developer": False, "save_namespace": pid,
              "host": host, "fixed_runtime": fixed, "minigames": {"prod_timed": current_installed},
              "retained_games": {old_info.digest: old_installed}}
    put(generated / "game/bridge-minigames.json", config)
    put(generated / "bridge-manifest.json", {"minigames": {"prod_timed": current_installed}})
    put(generated / "game/bridge_story.rpy", "# inert generated source fixture\n")
    head_path = put(project / "model/head.json", {"schema_version": "1.1", "revision": 1,
        "model_path": "history/revisions/00000001.json", "runnable_revision": 1, "runnable_project_path": generated.relative_to(project).as_posix()})
    put(home / "projects/index.json", {"projects": {pid: {"name": "selected HTML fixture"}, "other": {"name": "private"}}})
    independent = project / "releases/final/independent/html-fixture-pc"
    shutil.copytree(generated, independent)
    exe = put(independent / "html-fixture.exe", b"INERT PLAYER FIXTURE; NEVER EXECUTED")
    package = independent.parent.parent / "html-fixture-pc.zip"
    with zipfile.ZipFile(package, "x") as archive:
        for path in independent.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(independent.parent).as_posix())
    cases = []
    for outcome in ("win", "lose", "cancel", "error"):
        scenario = {"frame_id": "entry", "expected_outcome": outcome, "expected_target_frame_id": outcome, "actions": []}
        if outcome == "error":
            scenario["fault_plan"] = {"type": "terminate_own_host", "after": "running"}
        screenshots = [put(home / "jobs/final/evidence" / outcome / name, b"INERT SCREENSHOT FIXTURE") for name in ("waiting.png", "return.png")]
        cases.append({"passed": True, "requested_testcase_passed": True, "injected_results": False,
                      "same_window_verification": {"passed": True, "scope": "UNIT FIXTURE; NEVER WINDOW EVIDENCE"},
                      "screenshots": [{"path": str(path), "sha256": delivery.file_hash(path)} for path in screenshots], "scenario": scenario})
    suite = {"passed": True, "requested": 4, "executed": 4, "reports": cases, "scope": "UNIT FIXTURE; NEVER RUNTIME EVIDENCE"}
    report = {"kind": "build", "ok": True, "status": "passed", "independent_release_verified": True,
        "project_id": pid, "revision": 1, "model_hash": delivery.digest(model), "code_digest": delivery.legacy.core_hashes(root)[1],
        "sdk_version": sdk_lock["version"], "generator_version": "0.1.3", "generation": {"project_path": str(generated)},
        "execution": {"tested_and_published_identical": True, "package_members_unchanged": True},
        "minigame_package_members": {"all_declared_files_included": True},
        "minimum_minigame_release_coverage": {"minimum_outcomes_verified_by_declared_scenarios": True},
        "minigame_verification": deepcopy(suite), "minigame_package_verification": deepcopy(suite),
        "release": {"path": str(package), **record(package), "executable": str(exe), "executable_sha256": delivery.file_hash(exe),
                    "entries": len(zipfile.ZipFile(package).infolist()), "independent_directory": str(independent)}}
    report_path = put(home / "jobs/final/report.json", report)
    return {"root": root, "home": home, "pid": pid, "project": project, "head_path": head_path,
        "model": model, "report": report, "report_path": report_path, "generated": generated, "independent": independent,
        "package": package, "output": tmp_path / "delivery", "old": old_info, "current": info,
        "native": native, "host_report": host_report, "browser_lock": browser_lock_path, "license": license_path}


def run(f, **kwargs):
    return delivery.package_html_delivery(f["home"], f["pid"], f["report_path"], f["output"], f["root"], **kwargs)


def test_complete_source_snapshot_preserves_old_and_new_packages_native_source_licenses_and_exact_player(fixture):
    f = fixture
    before = {str(path): delivery.file_hash(path) for root in (f["root"], f["home"]) for path in root.rglob("*") if path.is_file()}
    assert run(f)["status"] == "complete"
    manifest = delivery.read_json(f["output"] / "manifest.json")
    assert manifest["schema_version"] == "1.1" and manifest["archive_is_runtime_acceptance"] is False
    files = {item["path"] for item in manifest["files"]}
    for item in manifest["files"]:
        assert record(f["output"] / item["path"]) == {key: item[key] for key in ("sha256", "bytes")}
    for package in (f["old"], f["current"]):
        copied = f["output"] / "managed-home/projects" / f["pid"] / "minigames" / package.digest
        assert validate_directory(copied).digest == package.digest
    assert "bridge-source/native/html_minigame_embedded_host/NativeParent.cs" in files
    assert "bridge-source/native/html_minigame_embedded_host/packages.lock.json" in files
    assert "metadata/fixed-browser-signature-lock.json" in files
    assert any(name.endswith("fixture_package-1.0.dist-info/licenses/LICENSE") for name in files)
    assert delivery.file_hash(f["output"] / "release" / f["package"].name) == delivery.file_hash(f["package"])
    assert set(delivery.read_json(f["output"] / "managed-home/projects/index.json")["projects"]) == {f["pid"]}
    assert not any(".venv/" in name or "/saves/" in name for name in files)
    assert before == {str(path): delivery.file_hash(path) for root in (f["root"], f["home"]) for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("field", ["revision", "same_window", "concrete_case", "missing_case", "native_source", "historical_package", "license", "browser_signature"])
def test_stale_or_incomplete_bindings_reject_before_any_output_is_published(fixture, field):
    f = fixture
    if field == "revision":
        f["report"]["revision"] = 0
    elif field == "same_window":
        f["report"]["minigame_package_verification"]["reports"][0]["same_window_verification"]["passed"] = False
    elif field == "concrete_case":
        f["report"]["minigame_verification"]["reports"][0]["requested_testcase_passed"] = False
    elif field == "missing_case":
        suite = f["report"]["minigame_package_verification"]
        suite["reports"].pop()
        suite["requested"] = suite["executed"] = 3
    elif field == "native_source":
        put(f["native"] / "NativeParent.cs", "changed source after compilation")
    elif field == "historical_package":
        (f["project"] / "minigames" / f["old"].digest / "LICENSE.txt").unlink()
    elif field == "license":
        f["license"].unlink()
    else:
        lock = delivery.read_json(f["browser_lock"])
        lock["signature_status"] = "UnknownError"
        put(f["browser_lock"], lock)
    put(f["report_path"], f["report"])
    with pytest.raises(Exception):
        run(f)
    assert not f["output"].exists()
    assert not list(f["output"].parent.glob(".delivery.stage-*"))


def test_dry_run_and_exclusive_output_policy(fixture):
    f = fixture
    assert run(f, dry_run=True)["status"] == "dry_run"
    assert not f["output"].exists()
    marker = put(f["output"] / "keep.txt", b"preserve previous delivery")
    with pytest.raises(delivery.DeliveryError, match="already exists"):
        run(f)
    assert marker.read_bytes() == b"preserve previous delivery"


def directory_link(link, target):
    """Use a real Windows junction where directory symlinks need privileges."""
    if os.name == "nt":
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        assert created.returncode == 0, created.stderr
        assert link.is_junction()
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("name", ["node_modules", "NODE_MODULES", "__pycache__", "bin", "obj"])
def test_explicitly_excluded_directory_link_is_pruned_before_archive_scan(fixture, tmp_path, name):
    f = fixture
    external = tmp_path / "outside-source"
    forbidden = put(external / "unmanaged-private.txt", b"must not inspect or archive dependency contents")
    directory_link(f["root"] / "tools" / name, external)
    included = put(f["root"] / "tools/needed.mjs", b"required ordinary source survives pruning")
    assert delivery.source_files(f["root"] / "tools") == sorted([included, f["root"] / "tools/package_html_delivery.py"])
    plan = delivery.prepare_plan(f["home"], f["pid"], f["report_path"], f["output"], f["root"])
    assert any(item.source == included for item in plan["items"])
    assert not any(item.source == forbidden or name.casefold() in item.destination.casefold().split("/") for item in plan["items"])
    assert forbidden.read_bytes() == b"must not inspect or archive dependency contents"


def test_included_source_directory_link_is_rejected_before_reading_its_files(fixture, tmp_path):
    f = fixture
    external = tmp_path / "outside-src"
    forbidden = put(external / "linked.py", b"unmanaged source must not become part of recoverable snapshot")
    directory_link(f["root"] / "src/linked-source", external)
    with pytest.raises(delivery.DeliveryError, match="Links/reparse"):
        run(f, dry_run=True)
    assert forbidden.read_bytes() == b"unmanaged source must not become part of recoverable snapshot"
    assert not f["output"].exists()


def test_pruning_preserves_native_lock_manifests_and_formal_noncode_assets(fixture):
    f = fixture
    plan = delivery.prepare_plan(f["home"], f["pid"], f["report_path"], f["output"], f["root"])
    names = {item.destination for item in plan["items"]}
    assert {
        "bridge-source/native/html_minigame_embedded_host/Program.cs",
        "bridge-source/native/html_minigame_embedded_host/HtmlMinigameEmbeddedHost.csproj",
        "bridge-source/native/html_minigame_embedded_host/packages.lock.json",
        "bridge-source/native/html_minigame_embedded_host/app.manifest",
        "bridge-source/examples/html_minigames/production/v1.0.1/timed/LICENSE.txt",
        "bridge-source/examples/html_minigames/production/v1.0.1/timed/instructions.txt",
        "bridge-source/examples/html_minigames/production/v1.0.1/timed/tap.wav",
    } <= names


def test_required_source_file_hardlink_is_rejected(tmp_path):
    source = tmp_path / "source"
    original = put(source / "original.py", b"required source")
    os.link(original, source / "alias.py")
    with pytest.raises(delivery.DeliveryError, match="regular unlinked"):
        delivery.source_files(source)


def removed_staging_record(f):
    """Explicit historical fixture, with no real SDK or temporary ZIP run."""
    original = f["output"].parent / "removed-owned-staging"
    record = {"path": str(original / "o" / f["package"].name), **record_from_release(f)}
    f["report"]["release_staging"] = {"requested_directory": str(original), "canonical_directory": str(original),
        "output_directory": str(original / "o"), "staged_package": record,
        "cleanup": {"status": "removed", "directory_exists": False, "ownership_verified": True}}
    return record


def record_from_release(f):
    return {key: f["report"]["release"][key] for key in ("sha256", "bytes")}


def test_only_deleted_owned_staging_zip_with_exact_final_bytes_is_provenance(fixture):
    f = fixture
    staged = removed_staging_record(f)
    put(f["report_path"], f["report"])
    assert not Path(staged["path"]).exists()
    plan = delivery.prepare_plan(f["home"], f["pid"], f["report_path"], f["output"], f["root"])
    assert any(item.source == f["package"] for item in plan["items"])
    assert not any(item.source == Path(staged["path"]) for item in plan["items"])
    assert not f["output"].exists()


@pytest.mark.parametrize("field", ["sha256", "bytes", "path", "ownership", "cleanup_directory"])
def test_removed_staging_provenance_must_match_exact_final_zip_and_owned_output(fixture, field):
    f = fixture
    staged = removed_staging_record(f)
    if field == "sha256":
        staged[field] = "f" * 64
    elif field == "bytes":
        staged[field] += 1
    elif field == "path":
        staged[field] = str(f["output"].parent / "unrelated-missing.zip")
    elif field == "ownership":
        f["report"]["release_staging"]["cleanup"]["ownership_verified"] = False
    else:
        f["report"]["release_staging"]["cleanup"]["directory"] = str(f["output"].parent / "unrelated-stage")
    put(f["report_path"], f["report"])
    with pytest.raises(delivery.DeliveryError, match="staged ZIP"):
        run(f, dry_run=True)
    assert not f["output"].exists()


@pytest.mark.parametrize("same_as_staged", [False, True])
def test_other_missing_hash_reference_cannot_use_staging_provenance_exception(fixture, same_as_staged):
    f = fixture
    staged = removed_staging_record(f)
    f["report"]["other_raw_evidence"] = dict(staged) if same_as_staged else {
        "path": str(f["home"] / "jobs/final/window-records/missing-host.log"), "sha256": "f" * 64}
    put(f["report_path"], f["report"])
    with pytest.raises(delivery.DeliveryError):
        run(f, dry_run=True)
    assert not f["output"].exists()


def test_copy_race_preserves_failed_staging_without_publishing_complete_output(fixture, monkeypatch):
    f = fixture
    copy = delivery.legacy.copy_verified
    changed = False

    def race(item, output):
        nonlocal changed
        if not changed:
            changed = True
            item.source.write_bytes(b"changed after preflight")
        return copy(item, output)

    monkeypatch.setattr(delivery.legacy, "copy_verified", race)
    with pytest.raises(delivery.DeliveryError):
        run(f)
    assert not f["output"].exists()
    stages = list(f["output"].parent.glob(".delivery.stage-*"))
    assert len(stages) == 1
    assert delivery.read_json(stages[0] / "FAILED.json")["status"] == "incomplete"
    assert not (stages[0] / "manifest.json").exists()


def test_player_zip_cannot_omit_retained_license_even_when_report_claims_all_members(fixture):
    f = fixture
    omitted = f"game/bridge_minigames/{f['old'].digest}/LICENSE.txt"
    replacement = f["package"].with_suffix(".new.zip")
    with zipfile.ZipFile(f["package"]) as original, zipfile.ZipFile(replacement, "x") as output:
        for item in original.infolist():
            if item.filename != f["independent"].name + "/" + omitted:
                output.writestr(item, original.read(item))
    replacement.replace(f["package"])
    f["report"]["release"].update(record(f["package"]))
    with zipfile.ZipFile(f["package"]) as archive:
        f["report"]["release"]["entries"] = len(archive.infolist())
    put(f["report_path"], f["report"])
    with pytest.raises(delivery.DeliveryError, match="omits a declared"):
        run(f)
    assert not f["output"].exists()
