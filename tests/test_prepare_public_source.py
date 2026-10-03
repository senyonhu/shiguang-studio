"""Public source preparation is allowlisted, private-data refusing and byte exact."""
import importlib.util
import json
import os
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_source_preparation_test", ROOT / "tools/prepare_public_source.py")
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


def write(path, value=b"# source\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else value.encode())


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "workspace"
    for name in public.TOP_FILES:
        write(root / name)
    for name in public.PUBLIC_DOCS:
        write(root / "docs" / name)
    for name in public.PUBLIC_METADATA:
        write(root / "metadata" / name, b'{"runtimeVersion":"154.0.4258.53"}\n')
    for tree in public.SOURCE_TREES:
        (root / tree).mkdir(parents=True, exist_ok=True)
    write(root / "src/module.py", b"# CRLF bytes\r\nvalue=1\r\n")
    write(root / "tests/test_example.py")
    write(root / "native/host/Host.cs")
    write(root / "examples/html_minigames/timed/tap.wav", b"RIFF\x00audio\x00")
    write(root / "plugin/.agents/plugins/marketplace.json", b"{}\n")
    write(root / "plugin/game/.mcp.json", b"{}\n")
    write(root / "README.md", "[Usage](docs/USAGE.md)\n")
    return root


def test_allowlist_excludes_internal_evidence_user_data_profiles_and_build_outputs(source):
    hidden = [
        "evidence/private.json", "outputs/public-source/private.txt", "projects/model.json",
        "jobs/report.json", "runtimes/sdk.exe", "upstream/editor.py",
        "docs/HTML_MINIGAME_FIRST_RELEASE_RECORD.json", "docs/STATUS.md",
        "tools/.env", "tools/.env.local", "tests/saves/1-1.save",
        "src/profiles/preferences.json", "native/host/bin/runtime.dll",
        "native/host/obj/project.json", "tools/node_modules/pkg/index.js",
        "src/pkg/__pycache__/module.pyc", "src/package.egg-info/PKG-INFO",
        "examples/html_minigames/prototypes/index.html",
    ]
    for relative in hidden:
        write(source / relative, "private file must be excluded")
    report = public.prepare(source, source / "outputs/public-source/new")
    assert report["status"] == "prepared" and report["source_copy_and_zip_verified"]
    target = Path(report["source_root"])
    for relative in hidden:
        assert not (target / relative).exists()
    assert (target / "plugin/game/.mcp.json").is_file()
    metadata = json.loads((target / public.METADATA_NAME).read_text())
    assert metadata["project_license"] is None and not report["project_license_inferred"]
    assert metadata["sdk_and_production_evidence_included"] is False


def test_snapshot_manifest_and_zip_preserve_crlf_binary_and_all_source_hashes(source):
    before = public.manifest(public.collect(source))
    report = public.prepare(source, source / "outputs/public-source/complete")
    assert report["status"] == "prepared" and report["files"] == len(before)
    target = Path(report["source_root"])
    meta = json.loads((target / public.METADATA_NAME).read_text())
    assert meta["files"] == before
    assert (target / "src/module.py").read_bytes() == b"# CRLF bytes\r\nvalue=1\r\n"
    with zipfile.ZipFile(report["public_zip"]) as archive:
        for relative, record in before.items():
            value = archive.read("source-root/" + relative)
            assert public.digest(value) == record["sha256"] and len(value) == record["bytes"]
        assert archive.read("source-root/" + public.METADATA_NAME) == (target / public.METADATA_NAME).read_bytes()
    assert report["public_zip_sha256"] == public.digest(Path(report["public_zip"]).read_bytes())


def test_only_complete_fixed_browser_pin_metadata_is_copied(source):
    pin = "metadata/webview2-runtime.lock.json"
    write(source / "metadata/private-extra.json", "unknown internal payload")
    write(source / "metadata/nested/user.json", "private nested payload")
    report = public.prepare(source, source / "outputs/public-source/browser-pin")
    assert report["status"] == "prepared"
    target = Path(report["source_root"])
    assert (target / pin).read_bytes() == (source / pin).read_bytes()
    manifest = json.loads((target / public.METADATA_NAME).read_text())["files"]
    assert pin in manifest
    assert not (target / "metadata/private-extra.json").exists()
    assert not (target / "metadata/nested").exists()
    (source / pin).unlink()
    report = public.prepare(source, source / "outputs/public-source/missing-browser-pin")
    assert report["status"] == "failed" and not (source / "outputs/public-source/missing-browser-pin/public-source.zip").exists()


def test_existing_output_and_external_or_source_tree_output_are_never_overwritten(source, tmp_path):
    output = source / "outputs/public-source/already"
    write(output / "keep", "must remain")
    for path in (output, tmp_path / "external", source, source / "tools/generated"):
        with pytest.raises(public.PreparationError):
            public.prepare(source, path)
    assert (output / "keep").read_text() == "must remain"
    assert not (tmp_path / "external").exists()


@pytest.mark.parametrize("relative", [".git/new-output", ".codex/new-output", "jobs/new-output",
                                     "runtimes/new-output", "profiles/new-output", "outputs"])
def test_output_cannot_touch_git_configuration_or_other_protected_roots(source, relative):
    with pytest.raises(public.PreparationError, match="outputs/public-source"):
        public.prepare(source, source / relative)
    assert not (source / relative).exists()


def test_hard_link_to_external_private_file_is_refused(source, tmp_path):
    private = tmp_path / "private-user-file.py"
    write(private, "ordinary private content without credential patterns")
    target = source / "src/private-copy.py"
    try:
        os.link(private, target)
    except OSError:
        pytest.skip("Filesystem does not support hard links")
    assert target.stat().st_nlink > 1
    report = public.prepare(source, source / "outputs/public-source/hard-link")
    assert report["status"] == "failed" and "Hard-linked source" in report["error"]["message"]
    assert private.read_text() == "ordinary private content without credential patterns"
    assert not (source / "outputs/public-source/hard-link/source-root").exists()


@pytest.mark.parametrize("relative", ["README.md", "docs/VALIDATION.md", "native"])
def test_missing_required_public_sources_fail_closed(source, relative):
    path = source / relative
    if path.is_dir():
        # This native fixture directory has one child, removed explicitly.
        (path / "host/Host.cs").unlink()
        (path / "host").rmdir()
        path.rmdir()
    else:
        path.unlink()
    report = public.prepare(source, source / "outputs/public-source/missing")
    assert report["status"] == "failed" and not (source / "outputs/public-source/missing/public-source.zip").exists()


@pytest.mark.parametrize("rule,value", [
    ("github_token", "ghp_" + "aB3dE5fG7hI9jK1mN3pQ5rS7tU9vW1xY3zA5"),
    ("openai_key", "sk-proj-" + "aB3dE5fG7hI9jK1mN3pQ5rS7tU9vW1xY3zA5"),
    ("aws_access_key", "AKIA" + "1234ABCD5678EFGH"),
    ("private_key", "-----BEGIN " + "PRIVATE KEY-----\n" + "aB3dE5fG7hI9jK1mN3pQ5rS7tU9vW1xY3zA5"*3 + "\n-----END " + "PRIVATE KEY-----"),
])
def test_high_confidence_credentials_fail_without_echoing_secret_or_producing_zip(source, rule, value):
    write(source / "scripts/private.py", "credential=" + value)
    report = public.prepare(source, source / "outputs/public-source/blocked")
    assert report["status"] == "failed"
    privacy = json.loads((source / "outputs/public-source/blocked/privacy-report.json").read_text())
    assert any(item["rule"] == rule for item in privacy["findings"])
    assert value not in json.dumps(privacy)
    assert not (source / "outputs/public-source/blocked/public-source.zip").exists()
    assert not (source / "outputs/public-source/blocked/source-root").exists()


def test_documented_test_tokens_are_not_generic_secret_false_positives(source):
    samples = ["AKIAIOSFODNN7EXAMPLE", "ghp_" + "dummy_" + "a"*36,
               "sk-" + "test-" + "aB3dE5fG7hI9jK1mN3pQ5rS7tU9vW1xY3zA5"]
    write(source / "tests/test_tokens.py", "\n".join(samples))
    assert public.prepare(source, source / "outputs/public-source/examples")["status"] == "prepared"


def test_personal_profile_paths_fail_but_placeholder_fixture_user_is_allowed(source):
    profile_path = "C:" + "/Users/" + "Alice/Documents/private"
    write(source / "tools/helper.py", profile_path)
    assert public.prepare(source, source / "outputs/public-source/personal")["status"] == "failed"
    write(source / "tools/helper.py", "C:" + "/Users/" + "FixtureUser/example")
    assert public.prepare(source, source / "outputs/public-source/placeholder")["status"] == "prepared"


@pytest.mark.parametrize("target", ["../evidence/report.json", "file:" + "///private/report.json",
                                   "/" + "private/absolute.md", "docs/MISSING.md"])
def test_markdown_local_private_and_missing_links_are_blocked(source, target):
    write(source / "README.md", "[private reference](" + target + ")")
    report = public.prepare(source, source / "outputs/public-source/dead-link")
    assert report["status"] == "failed"
    assert not (source / "outputs/public-source/dead-link/public-source.zip").exists()


@pytest.mark.parametrize("relative", ["src/module.py", "src"])
def test_links_and_junctions_are_rejected_without_reading_external_data(source, monkeypatch, relative):
    selected = source / relative
    original = public.Path.lstat
    def linked(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == selected:
            class FakeInfo:
                st_mode = result.st_mode
                st_file_attributes = 0x400
            return FakeInfo()
        return result
    monkeypatch.setattr(public.Path, "lstat", linked)
    report = public.prepare(source, source / "outputs/public-source/links")
    assert report["status"] == "failed" and "Links/junctions" in report["error"]["message"]
    assert (source / "src/module.py").read_bytes() == b"# CRLF bytes\r\nvalue=1\r\n"


def test_windows_case_collisions_are_rejected_independently_of_host_filesystem(source, monkeypatch):
    original = public.os.walk
    def duplicate(root, **kwargs):
        for directory, dirs, files in original(root, **kwargs):
            if Path(directory) == source / "src":
                files = [*files, "MODULE.py"]
            yield directory, dirs, files
    monkeypatch.setattr(public.os, "walk", duplicate)
    report = public.prepare(source, source / "outputs/public-source/case-collision")
    assert report["status"] == "failed" and "case-colliding" in report["error"]["message"]


@pytest.mark.parametrize("relative", ["tools/CON.py", "tools/private.exe"])
def test_reserved_windows_names_and_unknown_binary_code_are_rejected(source, relative, monkeypatch):
    if "CON" in relative:
        original = public.os.walk
        def injected(root, **kwargs):
            for directory, dirs, files in original(root, **kwargs):
                if Path(directory) == source / "tools":
                    files = [*files, "CON.py"]
                yield directory, dirs, files
        monkeypatch.setattr(public.os, "walk", injected)
    else:
        write(source / relative, "unknown binary")
    assert public.prepare(source, source / "outputs/public-source/unsafe-name")["status"] == "failed"


def test_file_and_total_byte_limits_fail_before_source_publication(source, monkeypatch):
    monkeypatch.setattr(public, "MAX_FILE_BYTES", 20)
    write(source / "tools/large.py", b"x"*21)
    report = public.prepare(source, source / "outputs/public-source/file-limit")
    assert report["status"] == "failed" and "5 MiB" in report["error"]["message"]
    (source / "tools/large.py").unlink()
    monkeypatch.setattr(public, "MAX_FILE_BYTES", 5*1024*1024)
    monkeypatch.setattr(public, "MAX_TOTAL_BYTES", 1)
    report = public.prepare(source, source / "outputs/public-source/total-limit")
    assert report["status"] == "failed" and "50 MiB" in report["error"]["message"]


def test_hash_race_after_copy_is_rejected_without_a_public_zip(source, monkeypatch):
    original = public.collect
    calls = []
    def changed(root):
        calls.append(root)
        if len(calls) == 2:
            write(source / "src/module.py", "source changed during copy")
        return original(root)
    monkeypatch.setattr(public, "collect", changed)
    report = public.prepare(source, source / "outputs/public-source/hash-race")
    assert report["status"] == "failed" and "Source changed" in report["error"]["message"]
    assert not (source / "outputs/public-source/hash-race/public-source.zip").exists()


def test_file_changes_during_read_are_refused(source, monkeypatch):
    original = public.Path.read_bytes
    selected = source / "src/module.py"
    def changed(path):
        data = original(path)
        if path == selected:
            write(selected, b"changed size during the read")
        return data
    monkeypatch.setattr(public.Path, "read_bytes", changed)
    report = public.prepare(source, source / "outputs/public-source/read-race")
    assert report["status"] == "failed" and "Source changed while reading" in report["error"]["message"]
    assert not (source / "outputs/public-source/read-race/public-source.zip").exists()


def test_hash_race_during_zip_quarantines_unverified_archive(source, monkeypatch):
    original = public.collect
    calls = []
    def changed(root):
        calls.append(root)
        if len(calls) == 3:
            write(source / "src/module.py", "source changed during ZIP")
        return original(root)
    monkeypatch.setattr(public, "collect", changed)
    report = public.prepare(source, source / "outputs/public-source/zip-race")
    assert report["status"] == "failed"
    assert not (source / "outputs/public-source/zip-race/public-source.zip").exists()
    assert (source / "outputs/public-source/zip-race/failed-unverified-source.zip").exists()


def test_cli_has_fixed_script_root_and_no_network_commit_push_flags(source, monkeypatch, capsys):
    monkeypatch.setattr(public, "ROOT", source)
    assert public.main(["--output", str(source / "outputs/public-source/cli")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert not any(report[key] for key in ("network_accessed", "installed", "committed", "pushed"))
    with pytest.raises(SystemExit):
        public.main(["--source", str(source.parent)])
