"""Preparation safety with inert fixtures; mocks do not certify a browser."""
from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare_browser_test", ROOT / "tools/prepare_html_browser.py")
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def clone(tmp_path, monkeypatch):
    root = tmp_path / "clone with spaces"
    root.mkdir()
    content = {"msedgewebview2.exe": b"inert browser fixture", "sub/LICENSE": b"fixture license", "other.dll": b"inert library fixture"}
    records = {name: {"sha256": browser.hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in content.items()}
    cab = b"inert CAB fixture"
    pin = dict(browser.PINNED, cab_sha256=browser.hashlib.sha256(cab).hexdigest(), cab_bytes=len(cab),
               exe_sha256=records["msedgewebview2.exe"]["sha256"], runtime_tree_sha256=browser.canonical_digest(records))
    monkeypatch.setattr(browser, "PINNED", pin)
    monkeypatch.setattr(browser, "ROOT", root)
    monkeypatch.setattr(browser, "require_windows", lambda: None)
    metadata = dict(pin, schema_version="1.0", files=records)
    (root / "metadata").mkdir()
    browser.write_json(root / "metadata/webview2-runtime.lock.json", metadata)
    signed = {"status": "Valid", "signer": "CN=Microsoft Corporation, O=Microsoft Corporation, C=US", "signer_thumbprint": "fixture"}
    monkeypatch.setattr(browser, "authenticode", lambda _: dict(signed))
    def download(_pin, output):
        assert _pin["cab_sha256"] == pin["cab_sha256"]
        output.write_bytes(cab)
    def extract(_cab, output):
        output.mkdir()
        for name, data in content.items():
            write(output / browser.RUNTIME_NAME / name, data)
    monkeypatch.setattr(browser, "download_cab", download)
    monkeypatch.setattr(browser, "extract_cab", extract)
    return root, metadata, content, cab


def installed(clone, with_cab=True):
    root, pin, content, cab = clone
    wrapper = root / "runtimes" / browser.WRAPPER_NAME
    for name, data in content.items():
        write(wrapper / browser.RUNTIME_NAME / name, data)
    browser.write_json(wrapper / "runtime-lock.json", {key: pin[key] for key in browser.PINNED})
    if with_cab:
        write(wrapper / "runtime.cab", cab)
    return wrapper


def test_default_missing_runtime_does_not_download_or_create_runtimes(clone, monkeypatch, capsys):
    root, *_ = clone
    before = snapshot(root)
    monkeypatch.setattr(browser, "download_cab", lambda *_: pytest.fail("Default verification downloaded"))
    assert browser.main([]) == 1
    assert "--prepare" in capsys.readouterr().err
    assert snapshot(root) == before and not (root / "runtimes").exists()


@pytest.mark.parametrize("argv", [[], ["--verify-only"], ["--prepare"]])
def test_existing_runtime_fresh_signature_is_read_only_even_with_prepare(clone, monkeypatch, capsys, argv):
    wrapper = installed(clone)
    root, *_ = clone
    before = snapshot(root)
    calls = []
    monkeypatch.setattr(browser, "authenticode", lambda exe: calls.append(exe) or {"status": "Valid", "signer": "O=Microsoft Corporation"})
    monkeypatch.setattr(browser, "download_cab", lambda *_: pytest.fail("Existing runtime must not download"))
    assert browser.main(argv) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "verified" and report["fresh_signature_verified"] is True
    assert report["historical_signature_claim_used_as_proof"] is False
    assert len(calls) == 1 and calls[0] == wrapper / browser.RUNTIME_NAME / "msedgewebview2.exe"
    assert snapshot(root) == before and report["runtime_modified"] is False


def test_prepare_missing_publishes_verified_full_tree_and_compatible_lock(clone, capsys):
    root, pin, *_ = clone
    assert browser.main(["--prepare"]) == 0
    report = json.loads(capsys.readouterr().out)
    wrapper = root / "runtimes" / browser.WRAPPER_NAME
    locked = browser.read_json(wrapper / "runtime-lock.json")
    assert report["status"] == "prepared" and report["new_runtime_published"] is True
    assert locked["relative_runtime_path"] == pin["relative_runtime_path"]
    assert locked["signature_status"] == "Valid" and locked["files"] == pin["files"]
    assert browser.inventory(wrapper / browser.RUNTIME_NAME) == pin["files"]
    assert (wrapper / "runtime.cab").read_bytes() == clone[3]
    assert not list((root / "runtimes").glob("*.prepare.lock"))


@pytest.mark.parametrize("changed", ["exe", "dll", "extra", "empty_directory", "wrapper_extra", "cab", "identity", "recorded_files"])
def test_existing_mismatch_is_not_repaired_or_replaced(clone, changed, monkeypatch):
    wrapper = installed(clone)
    root, pin, *_ = clone
    runtime = wrapper / browser.RUNTIME_NAME
    if changed == "exe": (runtime / "msedgewebview2.exe").write_bytes(b"modified")
    elif changed == "dll": (runtime / "other.dll").write_bytes(b"modified")
    elif changed == "extra": write(runtime / "unfrozen.txt", b"extra")
    elif changed == "empty_directory": (runtime / "empty").mkdir()
    elif changed == "wrapper_extra": write(wrapper / "unexpected.txt", b"extra")
    elif changed == "cab": (wrapper / "runtime.cab").write_bytes(b"modified")
    else:
        lock = browser.read_json(wrapper / "runtime-lock.json")
        if changed == "identity": lock["version"] = "unfixed"
        else: lock["files"] = {}
        (wrapper / "runtime-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    before = snapshot(root)
    monkeypatch.setattr(browser, "download_cab", lambda *_: pytest.fail("Mismatch must not be repaired by downloading"))
    with pytest.raises(browser.BrowserPreparationError): browser.prepare(root, pin, "fixture metadata")
    assert snapshot(root) == before


def test_full_inventory_is_checked_before_fresh_signature(clone, monkeypatch):
    wrapper = installed(clone)
    (wrapper / browser.RUNTIME_NAME / "other.dll").write_bytes(b"changed")
    monkeypatch.setattr(browser, "authenticode", lambda _: pytest.fail("Unfrozen DLL reached signature verification"))
    with pytest.raises(browser.BrowserPreparationError, match="complete tree"):
        browser.verify_existing(clone[0], clone[1], "fixture")


def test_runtime_change_during_signature_check_is_rejected(clone, monkeypatch):
    wrapper = installed(clone)
    def sign(_):
        (wrapper / browser.RUNTIME_NAME / "other.dll").write_bytes(b"changed during signature")
        return {"status": "Valid", "signer": "O=Microsoft Corporation"}
    monkeypatch.setattr(browser, "authenticode", sign)
    with pytest.raises(browser.BrowserPreparationError, match="changed during"):
        browser.verify_existing(clone[0], clone[1], "fixture")


@pytest.mark.parametrize("failure", ["download", "cab_mismatch", "extract", "layout", "signature", "publication"])
def test_failed_prepare_keeps_staging_and_never_creates_runtime(clone, failure, monkeypatch, capsys):
    root, *_ = clone
    def fail(*_): raise OSError("injected preparation failure")
    if failure == "download": monkeypatch.setattr(browser, "download_cab", fail)
    elif failure == "cab_mismatch": monkeypatch.setattr(browser, "download_cab", lambda pin, p: p.write_bytes(b"not fixed CAB"))
    elif failure == "extract": monkeypatch.setattr(browser, "extract_cab", fail)
    elif failure == "layout": monkeypatch.setattr(browser, "extract_cab", lambda cab, p: p.mkdir())
    elif failure == "signature": monkeypatch.setattr(browser, "authenticode", fail)
    else: monkeypatch.setattr(browser, "publish_missing", fail)
    assert browser.main(["--prepare"]) == 1
    assert "staging retained" in capsys.readouterr().err
    stages = list((root / "runtimes").glob("*.stage-*"))
    assert len(stages) == 1 and (stages[0] / "failure.json").is_file()
    assert not (root / "runtimes" / browser.WRAPPER_NAME).exists()


def test_existing_foreign_lock_is_never_removed(clone):
    root, pin, *_ = clone
    lock = root / "runtimes" / ("." + browser.WRAPPER_NAME + ".prepare.lock")
    write(lock, b"other process")
    with pytest.raises(browser.BrowserPreparationError, match="retained lock"):
        browser.prepare(root, pin, "fixture")
    assert lock.read_bytes() == b"other process"
    assert not list(lock.parent.glob("*.stage-*"))


@pytest.mark.parametrize("change", ["version", "source_url", "cab_sha256", "inventory"])
def test_mutated_public_pin_is_rejected(clone, change):
    root, pin, *_ = clone
    if change == "inventory": pin["files"]["other.dll"]["bytes"] += 1
    else: pin[change] = "untrusted replacement"
    (root / "metadata/webview2-runtime.lock.json").write_text(json.dumps(pin), encoding="utf-8")
    with pytest.raises(browser.BrowserPreparationError): browser.load_pin(root)


def test_non_object_pin_and_runtime_lock_fail_closed(clone):
    wrapper = installed(clone)
    root, pin, *_ = clone
    (wrapper / "runtime-lock.json").write_text("[]", encoding="utf-8")
    with pytest.raises(browser.BrowserPreparationError, match="JSON object"):
        browser.verify_existing(root, pin, "fixture")
    (root / "metadata/webview2-runtime.lock.json").write_text("[]", encoding="utf-8")
    with pytest.raises(browser.BrowserPreparationError, match="fixed pin"):
        browser.load_pin(root)


@pytest.mark.parametrize("name", ["../outside", "C:/outside", "x/../file", "a//b", "CON/file", "a./b", "a\\b"])
def test_unsafe_runtime_member_names_are_rejected(name):
    with pytest.raises(browser.BrowserPreparationError): browser.relative(name)


def test_output_path_cannot_leave_workspace_or_traverse(clone):
    root, *_ = clone
    for path in (root.parent / "outside", root / "runtimes/../elsewhere"):
        with pytest.raises(browser.BrowserPreparationError): browser.safe_child(root, path)


def test_hardlinked_runtime_file_is_rejected(clone):
    wrapper = installed(clone)
    original = wrapper / browser.RUNTIME_NAME / "other.dll"
    os.link(original, clone[0] / "linked-library")
    with pytest.raises(browser.BrowserPreparationError, match="Linked"):
        browser.verify_existing(clone[0], clone[1], "fixture")


def test_symlinked_runtimes_directory_is_rejected_without_touching_target(clone):
    root, pin, *_ = clone
    outside = root.parent / "outside"
    outside.mkdir()
    try: (root / "runtimes").symlink_to(outside, target_is_directory=True)
    except OSError as exc: pytest.skip("Windows symlink permission unavailable: " + str(exc))
    with pytest.raises(browser.BrowserPreparationError, match="Links"):
        browser.prepare(root, pin, "fixture")
    assert list(outside.iterdir()) == []


def test_windows_reparse_attribute_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / "junction"
    monkeypatch.setattr(Path, "lstat", lambda _: SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_nlink=1))
    assert browser.linked(path)


@pytest.mark.parametrize("existing", ["file", "directory"])
def test_publication_never_replaces_existing_destination(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(browser, "require_windows", lambda: None)
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    if existing == "file": destination.write_bytes(b"keep")
    else: destination.mkdir()
    monkeypatch.setattr(browser.os, "rename", lambda *_: pytest.fail("Existing target reached rename"))
    with pytest.raises(browser.BrowserPreparationError, match="Refusing"):
        browser.publish_missing(source, destination)


@pytest.mark.parametrize("status,subject", [("NotSigned", "O=Microsoft Corporation"), ("HashMismatch", "O=Microsoft Corporation"), ("Valid", "O=Another Company")])
def test_authenticode_requires_fresh_valid_microsoft_publisher(tmp_path, monkeypatch, status, subject):
    system = tmp_path / "Windows"
    write(system / "System32/WindowsPowerShell/v1.0/powershell.exe", b"inert executable fixture")
    monkeypatch.setenv("SystemRoot", str(system))
    monkeypatch.setattr(browser.shutil, "which", lambda _: None)
    monkeypatch.setattr(browser, "require_windows", lambda: None)
    result = SimpleNamespace(returncode=0, stdout=json.dumps({"status": status, "signer": subject}), stderr="")
    monkeypatch.setattr(browser.subprocess, "run", lambda *_, **__: result)
    with pytest.raises(browser.BrowserPreparationError, match="fresh Valid Microsoft"):
        browser.authenticode(tmp_path / "msedgewebview2.exe")


def test_authenticode_uses_encoded_literal_path_without_shell(tmp_path, monkeypatch):
    system = tmp_path / "Windows"
    write(system / "System32/WindowsPowerShell/v1.0/powershell.exe", b"inert executable fixture")
    monkeypatch.setenv("SystemRoot", str(system))
    monkeypatch.setattr(browser.shutil, "which", lambda _: None)
    monkeypatch.setattr(browser, "require_windows", lambda: None)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status": "Valid", "signer": "CN=Microsoft Corporation, O=Microsoft Corporation, C=US"}), stderr="")
    monkeypatch.setattr(browser.subprocess, "run", run)
    browser.authenticode(tmp_path / "O'Brien" / "msedgewebview2.exe")
    script = browser.base64.b64decode(calls[0][0][-1]).decode("utf-16-le")
    assert "Get-AuthenticodeSignature -LiteralPath" in script and "O''Brien" in script
    assert calls[0][1].get("shell", False) is False
    assert calls[0][1]["env"]["PSModulePath"] == str(system / "System32/WindowsPowerShell/v1.0/Modules")


def test_authenticode_available_powershell7_uses_its_matching_modules(tmp_path, monkeypatch):
    executable = tmp_path / "PowerShell7/pwsh.exe"
    write(executable, b"inert executable fixture")
    monkeypatch.setattr(browser.shutil, "which", lambda _: str(executable))
    monkeypatch.setattr(browser, "require_windows", lambda: None)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status": "Valid", "signer": "O=Microsoft Corporation"}), stderr="")
    monkeypatch.setattr(browser.subprocess, "run", run)
    result = browser.authenticode(tmp_path / "msedgewebview2.exe")
    assert result["verifier_executable"] == str(executable)
    assert calls[0][0][0] == str(executable)
    assert calls[0][1]["env"]["PSModulePath"] == str(executable.parent / "Modules")


@pytest.mark.parametrize("payload,redirect", [(b"wrong CAB", "https://official.example/fixed.cab"), (b"inert CAB fixture", "http://insecure.example/fixed.cab")])
def test_real_download_function_rejects_wrong_hash_and_https_downgrade(clone, tmp_path, monkeypatch, payload, redirect):
    # Exercise the actual downloader with an inert HTTP response, not the fixture
    # preparation helper installed above.
    spec = importlib.util.spec_from_file_location("browser_download_inert", ROOT / "tools/prepare_html_browser.py")
    actual = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(actual)
    class Response(io.BytesIO):
        def geturl(self): return redirect
    monkeypatch.setattr(actual.urllib.request, "urlopen", lambda *_, **__: Response(payload))
    with pytest.raises(actual.BrowserPreparationError): actual.download_cab(clone[1], tmp_path / "download.cab")


def test_cli_modes_are_exclusive_and_arbitrary_output_is_not_exposed():
    with pytest.raises(SystemExit) as exc: browser.main(["--prepare", "--verify-only"])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc: browser.main(["--destination", "outside-home"])
    assert exc.value.code == 2


def test_non_windows_main_fails_without_filesystem_changes(clone, monkeypatch):
    root, *_ = clone
    before = snapshot(root)
    def unsupported(): raise browser.BrowserPreparationError("requires Windows")
    monkeypatch.setattr(browser, "require_windows", unsupported)
    assert browser.main(["--prepare"]) == 1 and snapshot(root) == before


def test_public_metadata_is_full_pin_without_historical_signature_attestation():
    # Public provenance is not a current machine signature validation result.
    value = json.loads((ROOT / "metadata/webview2-runtime.lock.json").read_text(encoding="utf-8"))
    assert value["version"] == "154.0.4258.53" and len(value["files"]) == 257
    assert value["runtime_tree_sha256"] == browser.canonical_digest(value["files"])
    assert "signature_status" not in value and "signer" not in value
