"""Filesystem safety tests; inert payload fixtures do not certify native games."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("html_runtime_install_test", ROOT / "tools/install_html_runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
installer = runtime.installer


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def installation(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "managed home"
    source.mkdir(); destination.mkdir()
    installer.atomic_json(destination / ".installer/installed.json", {
        "schema_version": "1.0", "destination": str(destination), "managed_entries": ["src", "plugin"]})
    for path in ["projects/old/authoring.json", "jobs/old/log.txt", "runtimes/renpy-8.5.3/keep.txt", "saves/old.save"]:
        write(destination / path, "untouched " + path)
    native = source / runtime.NATIVE
    for path in ["Program.cs", "NativeParent.cs", "HtmlMinigameEmbeddedHost.csproj", "app.manifest", "packages.lock.json"]:
        write(native / path, "inert native source fixture " + path)
    native_files = runtime.inventory(native)
    host = source / "runtimes" / runtime.HOST
    for path in [runtime.EXE, "HtmlMinigameEmbeddedHost.dll", "WebView2Loader.dll"]:
        write(host / path, "inert artifact fixture " + path)
    artifacts = runtime.inventory(host)
    installer.atomic_json(host / "build-report.json", {
        "build": {"exit_code": 0, "self_contained": True,
                  "files": [{"path": name, **meta} for name, meta in artifacts.items()]},
        "native_source": {"root": runtime.NATIVE, "files": native_files, "tree_sha256": runtime.canonical_digest(native_files)}})
    version = "154.0.4258.53"
    wrapper = source / "runtimes" / ("webview2-fixed-" + version)
    browser = wrapper / ("Microsoft.WebView2.FixedVersionRuntime." + version + ".x64")
    write(browser / "msedgewebview2.exe", "inert browser fixture")
    write(browser / "LICENSE.txt", "fixture license")
    installer.atomic_json(wrapper / "runtime-lock.json", {
        "version": version, "architecture": "x64", "mode": "fixed", "signature_status": "Valid",
        "signer": "CN=Microsoft Corporation", "exe_sha256": installer.sha256(browser / "msedgewebview2.exe"),
        "relative_runtime_path": browser.relative_to(source).as_posix()})
    cfg = source / "frozen-config.json"
    installer.atomic_json(cfg, {"host": {"window_mode": "embedded_same_top_level_window",
        "relative_executable": "html-runtime/win-x64/" + runtime.EXE, "files": runtime.inventory(host),
        "sha256": installer.sha256(host / runtime.EXE)}, "fixed_runtime": {"version": version, "files": runtime.inventory(browser)}})
    return source, destination, cfg


def snap(root):
    return runtime.inventory(root)


def test_verified_plan_is_read_only_and_installs_only_named_runtime_trees(installation):
    source, destination, cfg = installation
    before = snap(destination)
    plan = runtime.make_plan(source, destination, cfg)
    assert snap(destination) == before
    result = runtime.install_plan(plan)
    assert result["status"] == "installed" and runtime.verify_installed(plan)
    for name, meta in before.items():
        assert installer.sha256(destination / name) == meta["sha256"]
    assert result["plugin_registration_modified"] is False
    assert result["runtime_acceptance_verified"] is False


def test_second_tree_publish_failure_restores_both_old_trees_and_marker(installation, monkeypatch):
    source, destination, cfg = installation
    plan = runtime.make_plan(source, destination, cfg)
    runtime.install_plan(plan)
    old = snap(destination / "runtimes")
    marker = (destination / ".installer/html-runtime/installed.json").read_bytes()
    original = runtime.os.replace
    failed = False
    def replace(src, dst):
        nonlocal failed
        if not failed and "/stage/webview2-fixed-" in Path(src).as_posix() and Path(dst).parent == destination / "runtimes":
            failed = True
            raise OSError("injected publication rename failure")
        return original(src, dst)
    monkeypatch.setattr(runtime.os, "replace", replace)
    with pytest.raises(OSError, match="injected"):
        runtime.install_plan(plan)
    assert snap(destination / "runtimes") == old
    assert (destination / ".installer/html-runtime/installed.json").read_bytes() == marker


def test_source_changes_after_plan_never_publish(installation):
    source, destination, cfg = installation
    plan = runtime.make_plan(source, destination, cfg)
    write(Path(plan["trees"][0]["source"]) / runtime.EXE, "changed after plan")
    old = snap(destination / "runtimes")
    with pytest.raises(runtime.InstallError, match="inventory differs"):
        runtime.install_plan(plan)
    assert snap(destination / "runtimes") == old
    assert not (destination / ".installer/html-runtime/installed.json").exists()


@pytest.mark.parametrize("change", ["host_extra", "host_changed", "native_changed", "report_self", "browser_extra", "lock_identity"])
def test_unfrozen_source_is_rejected_before_destination_changes(installation, change):
    source, destination, cfg = installation
    wrapper = source / "runtimes/webview2-fixed-154.0.4258.53"
    host = source / "runtimes" / runtime.HOST
    if change == "host_extra": write(host / "surprise.dll", "extra")
    elif change == "host_changed": write(host / runtime.EXE, "changed")
    elif change == "native_changed": write(source / runtime.NATIVE / "NativeParent.cs", "changed")
    elif change == "browser_extra": write(wrapper / "surprise.txt", "extra")
    elif change == "lock_identity":
        lock = installer.read_json(wrapper / "runtime-lock.json"); lock["relative_runtime_path"] = "runtimes/renpy-8.5.3"
        installer.atomic_json(wrapper / "runtime-lock.json", lock)
    else:
        build = installer.read_json(host / "build-report.json")
        build["build"]["files"].append({"path": "build-report.json", "bytes": 0, "sha256": "0" * 64})
        installer.atomic_json(host / "build-report.json", build)
        config = installer.read_json(cfg); config["host"]["files"] = runtime.inventory(host); installer.atomic_json(cfg, config)
    before = snap(destination)
    with pytest.raises(runtime.InstallError): runtime.make_plan(source, destination, cfg)
    assert snap(destination) == before


@pytest.mark.parametrize("value", ["../outside", "x/../outside", "C:/outside", "\\\\server\\share", "a//b", "CON/file", "foo./bar"])
def test_runtime_paths_reject_windows_and_traversal_forms(value):
    with pytest.raises(runtime.InstallError): runtime.relative(value)


def test_marker_and_active_jobs_prevent_unmanaged_upgrade(installation):
    source, destination, cfg = installation
    marker = destination / ".installer/installed.json"
    original = installer.read_json(marker)
    original["destination"] = str(destination.parent / "other")
    installer.atomic_json(marker, original)
    with pytest.raises(runtime.InstallError, match="marker"): runtime.make_plan(source, destination, cfg)
    original["destination"] = str(destination); installer.atomic_json(marker, original)
    installer.atomic_json(destination / "jobs/current/status.json", {"state": "running"})
    with pytest.raises(runtime.InstallError, match="active"): runtime.make_plan(source, destination, cfg)


def test_destination_link_is_rejected_without_touching_target(installation, tmp_path):
    source, destination, cfg = installation
    outside = tmp_path / "outside"; outside.mkdir()
    (destination / "runtimes/html-minigame-embedded-host").mkdir()
    link = destination / "runtimes" / runtime.HOST
    try: link.symlink_to(outside, target_is_directory=True)
    except OSError: pytest.skip("Windows symlink privilege is unavailable")
    with pytest.raises(runtime.InstallError, match="links"): runtime.make_plan(source, destination, cfg)
    assert list(outside.iterdir()) == []


def test_malformed_recovery_journal_cannot_move_any_tree(installation):
    _, destination, _ = installation
    run = destination / ".installer/html-runtime/runs/recovery"
    run.mkdir(parents=True)
    before = snap(destination)
    journal = {"schema_version": "1.0", "state": "publishing", "trees": [
        {"relative": runtime.HOST, "had_previous": True}, {"relative": "../projects", "had_previous": True}]}
    with pytest.raises(runtime.InstallError): runtime.rollback(destination, run, journal)
    assert snap(destination) == before


def test_curated_driver_publication_preserves_unrelated_tools(tmp_path):
    destination = tmp_path / "home"; run = destination / ".installer/runs/curated"
    write(destination / "tools/probe_html_cdp.mjs", "old driver")
    write(destination / "tools/private-helper.py", "leave unchanged")
    write(run / "code/tools/probe_html_cdp.mjs", "new driver")
    backup = destination / "backups" / ("install-" + run.name); backup.mkdir(parents=True)
    installer.copy_backup(destination, backup, ["tools/probe_html_cdp.mjs"])
    journal = installer.publish_code(destination, run, ["tools/probe_html_cdp.mjs"])
    assert (destination / "tools/private-helper.py").read_text() == "leave unchanged"
    assert (destination / "tools/probe_html_cdp.mjs").read_text() == "new driver"
    installer.rollback_publication(destination, run, journal)
    assert (destination / "tools/probe_html_cdp.mjs").read_text() == "old driver"
    assert (destination / "tools/private-helper.py").read_text() == "leave unchanged"


def test_final_journal_write_failure_restores_runtime_and_installation_receipt(installation, monkeypatch):
    source, destination, cfg = installation
    plan = runtime.make_plan(source, destination, cfg)
    runtime.install_plan(plan)
    previous = snap(destination / "runtimes")
    marker = (destination / ".installer/html-runtime/installed.json").read_bytes()
    original = installer.atomic_json
    def atomic(path, value):
        if path.name == "publication.json" and value.get("state") == "committed":
            raise OSError("injected final journal failure")
        return original(path, value)
    monkeypatch.setattr(installer, "atomic_json", atomic)
    with pytest.raises(OSError, match="final journal"):
        runtime.install_plan(plan)
    assert snap(destination / "runtimes") == previous
    assert (destination / ".installer/html-runtime/installed.json").read_bytes() == marker


def test_curated_source_copy_requires_driver_and_never_collects_other_tools(tmp_path):
    source = tmp_path / "source"
    values = {"pyproject.toml": "[project]\nname='fixture'\n", "requirements.lock": "setuptools==80.9.0\n",
        "src/renpy_bridge/server.py": "# fixture\n", "src/renpy_bridge/worker.py": "# fixture\n",
        "src/renpy_bridge/minigame_qa.py": "# fixture\n",
        "plugin/renpy-bridge/.codex-plugin/plugin.json": json.dumps({"name": "renpy-bridge", "mcpServers": "./.mcp.json", "skills": "./skills/"}),
        "plugin/.agents/plugins/marketplace.json": json.dumps({"plugins": [{"name": "renpy-bridge", "source": {"source": "local", "path": "./renpy-bridge"}}]})}
    for rel, content in values.items(): write(source / rel, content)
    with pytest.raises(runtime.InstallError, match="curated browser driver"): installer.source_files(source)
    write(source / "tools/probe_html_cdp.mjs", "// curated fixture\n")
    write(source / "tools/other.js", "// development helper\n")
    shutil.copytree(ROOT / installer.AUTHORING_TREE, source / installer.AUTHORING_TREE)
    shutil.copy2(ROOT / installer.AUTHORING_GUIDE, source / installer.AUTHORING_GUIDE)
    files = installer.source_files(source)
    assert "tools/probe_html_cdp.mjs" in {p.relative_to(source).as_posix() for p in files}
    assert "tools/other.js" not in {p.relative_to(source).as_posix() for p in files}
    stage = tmp_path / "stage"; stage.mkdir()
    names = installer.stage_code(source, files, stage, tmp_path / "home", {p.relative_to(source).as_posix(): installer.sha256(p) for p in files})
    assert "tools/probe_html_cdp.mjs" in names and "tools" not in names
