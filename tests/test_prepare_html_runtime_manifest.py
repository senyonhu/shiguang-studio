"""Runtime-only manifest safety; inert fixtures do not certify native gameplay."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_manifest_test", ROOT / "tools/prepare_html_runtime_manifest.py")
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    root, home = tmp_path / "clone", tmp_path / "managed"
    root.mkdir(); home.mkdir()
    for name in ("Program.cs", "NativeParent.cs", "HtmlMinigameEmbeddedHost.csproj", "app.manifest", "packages.lock.json"):
        write(root / manifest.runtime.NATIVE / name, "inert native source fixture " + name)
    host = root / "runtimes" / manifest.runtime.HOST
    for name in (manifest.runtime.EXE, "HtmlMinigameEmbeddedHost.dll", "WebView2Loader.dll"):
        write(host / name, "inert output fixture " + name)
    native = manifest.native_inventory(root)
    outputs = manifest.browser.inventory(host)
    report = {"build": {"exit_code": 0, "self_contained": True, "files": [{"path": name, **record} for name, record in outputs.items()]},
              "native_source": {"root": manifest.runtime.NATIVE, "files": native, "tree_sha256": manifest.runtime.canonical_digest(native)}}
    manifest.browser.write_json(host / "build-report.json", report)
    wrapper = root / "runtimes" / manifest.browser.WRAPPER_NAME
    browser = wrapper / manifest.browser.RUNTIME_NAME
    write(browser / "msedgewebview2.exe", "inert browser fixture")
    write(browser / "LICENSE.txt", "fixture license")
    browser_files = manifest.browser.inventory(browser)
    pin = {"version": manifest.browser.VERSION, "relative_runtime_path": browser.relative_to(root).as_posix(), "files": browser_files}
    manifest.browser.write_json(wrapper / "runtime-lock.json", {"version": pin["version"], "architecture": "x64", "mode": "fixed",
        "relative_runtime_path": pin["relative_runtime_path"], "exe_sha256": browser_files["msedgewebview2.exe"]["sha256"],
        "signature_status": "Valid", "signer": "O=Microsoft Corporation"})
    proof = {"files": browser_files, "fresh_signature_verified": True, "historical_signature_claim_used_as_proof": False,
             "evergreen_used": False, "authenticode": {"status": "Valid", "signer": "O=Microsoft Corporation"},
             "authenticode_checked_at_utc": "fixture not native evidence"}
    monkeypatch.setattr(manifest.browser, "load_pin", lambda _: (pin, "fixture metadata SHA"))
    monkeypatch.setattr(manifest.browser, "verify_existing", lambda *_: proof)
    monkeypatch.setattr(manifest.browser, "require_windows", lambda: None)
    manifest.runtime.installer.atomic_json(home / ".installer/installed.json", {"schema_version": "1.0", "destination": str(home), "managed_entries": ["src", "plugin"]})
    write(home / "projects/original/keep.txt", "original project")
    return root, home, host, wrapper, report, proof


def test_runtime_only_manifest_is_accepted_by_actual_installer_plan_without_game_project(inputs):
    root, home, host, wrapper, *_ = inputs
    original = manifest.runtime.inventory(home)
    host_before, browser_before = manifest.browser.inventory(host), manifest.browser.inventory(wrapper)
    result = manifest.create_manifest(root)
    path = Path(result["configuration"])
    cfg = manifest.browser.read_json(path)
    assert set(cfg) == {"host", "fixed_runtime"}
    plan = manifest.runtime.make_plan(root, home, path)
    assert plan["extension"] == "same_window_html_minigames" and len(plan["trees"]) == 2
    assert manifest.runtime.inventory(home) == original
    assert manifest.browser.inventory(host) == host_before and manifest.browser.inventory(wrapper) == browser_before
    assert not (root / "projects").exists() and result["private_game_project_required"] is False
    assert result["runtime_acceptance_verified"] is result["gameplay_verified"] is False
    assert result["managed_installation_modified"] is result["plugin_registration_modified"] is False


@pytest.mark.parametrize("change", ["self_contained", "exit", "dll", "host_extra", "duplicate", "self_hash", "native_changed", "source_missing", "host_missing"])
def test_failed_build_source_and_inventory_gates_produce_no_manifest(inputs, change):
    root, _, host, _, report, _ = inputs
    if change == "self_contained": report["build"]["self_contained"] = False
    elif change == "exit": report["build"]["exit_code"] = 1
    elif change == "dll": (host / "HtmlMinigameEmbeddedHost.dll").write_text("changed", encoding="utf-8")
    elif change == "host_extra": write(host / "extra.dll", "unfrozen")
    elif change == "duplicate": report["build"]["files"].append(report["build"]["files"][0])
    elif change == "self_hash": report["build"]["files"].append({"path": "build-report.json", "bytes": 0, "sha256": "0" * 64})
    elif change == "native_changed": write(root / manifest.runtime.NATIVE / "Program.cs", "source changed after build")
    elif change == "source_missing": (root / manifest.runtime.NATIVE / "NativeParent.cs").unlink()
    else: (host / "HtmlMinigameEmbeddedHost.dll").unlink()
    (host / "build-report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises((manifest.runtime.InstallError, manifest.browser.BrowserPreparationError)):
        manifest.create_manifest(root)
    assert not (root / "outputs").exists()


@pytest.mark.parametrize("change", ["fresh", "historical", "evergreen", "files"])
def test_browser_requires_fresh_full_fixed_verification(inputs, change):
    root, *_, proof = inputs
    if change == "fresh": proof["fresh_signature_verified"] = False
    elif change == "historical": proof["historical_signature_claim_used_as_proof"] = True
    elif change == "evergreen": proof["evergreen_used"] = True
    else: proof["files"] = {}
    with pytest.raises(manifest.runtime.InstallError, match="fresh complete"):
        manifest.create_manifest(root)
    assert not (root / "outputs").exists()


def test_runtime_mutation_during_browser_verification_is_rejected(inputs, monkeypatch):
    root, _, host, _, _, proof = inputs
    def verify(*_):
        (host / "HtmlMinigameEmbeddedHost.dll").write_text("changed concurrently", encoding="utf-8")
        return proof
    monkeypatch.setattr(manifest.browser, "verify_existing", verify)
    with pytest.raises(manifest.runtime.InstallError, match="inventory differs"):
        manifest.create_manifest(root)
    assert not (root / "outputs").exists()


def test_existing_unique_output_is_never_overwritten(inputs, monkeypatch):
    root, *_ = inputs
    monkeypatch.setattr(manifest.uuid, "uuid4", lambda: type("Id", (), {"hex": "same-output-id"})())
    # Both calls use the same timestamp; supply a deterministic run clock.
    class Clock:
        @staticmethod
        def now(_):
            return type("Date", (), {"strftime": lambda self, _: "same-time-"})()
    monkeypatch.setattr(manifest, "datetime", Clock)
    first = manifest.create_manifest(root)
    before = Path(first["configuration"]).read_bytes()
    with pytest.raises(FileExistsError): manifest.create_manifest(root)
    assert Path(first["configuration"]).read_bytes() == before


def test_native_hardlink_is_rejected(inputs):
    root, *_ = inputs
    os.link(root / manifest.runtime.NATIVE / "Program.cs", root / "linked-source")
    with pytest.raises(manifest.runtime.InstallError, match="regular unlinked"):
        manifest.create_manifest(root)
    assert not (root / "outputs").exists()


def test_output_symlink_is_rejected_without_writing_outside(inputs):
    root, *_ = inputs
    outside = root.parent / "outside"
    outside.mkdir()
    try: (root / "outputs").symlink_to(outside, target_is_directory=True)
    except OSError as exc: pytest.skip("Windows symlink permission unavailable: " + str(exc))
    with pytest.raises(manifest.browser.BrowserPreparationError, match="Links"):
        manifest.create_manifest(root)
    assert list(outside.iterdir()) == []


def test_cli_does_not_expose_arbitrary_output_or_private_configuration(inputs, monkeypatch, capsys):
    monkeypatch.setattr(manifest, "ROOT", inputs[0])
    assert manifest.main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert Path(result["configuration"]).is_relative_to(inputs[0] / "outputs/html-runtime")
    with pytest.raises(SystemExit) as exc: manifest.main(["--configuration", "private-project.json"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc: manifest.main(["--output", "outside"])
    assert exc.value.code == 2
