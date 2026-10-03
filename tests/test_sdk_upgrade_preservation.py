"""SDK source updates preserve generated caches and user files without executing them."""
from __future__ import annotations

from argparse import Namespace
import importlib.util
import json
from pathlib import Path
import shutil
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sdk_preservation_installer", ROOT / "scripts/install_local.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def sdk(tmp_path):
    files = {
        "renpy.py": "# official entry",
        "lib/py3-windows-x86_64/python.exe": b"official executable",
        "lib/py3-windows-x86_64/runtime.dll": b"official DLL",
        "renpy/source.py": "# immutable authority",
        "renpy/__pycache__/source.cpython-312.pyc": b"archive cache",
        "renpy/legacy.py": "# legacy authority",
        "renpy/legacy.pyc": b"archive legacy cache",
        "renpy/common/00db_ren.py": "# generated but shipped source",
        "renpy/common/00db.rpyc": b"archive cache of shipped _ren.py",
        "renpy/common/__pycache__/00db_ren.cpython-312.pyc": b"archive generated cache",
        "renpy/common/menu.rpy": "label main: pass",
        "renpy/common/menu.rpyc": b"archive RenPy cache",
        "renpy/common/module.rpym": "init python: pass",
        "renpy/common/module.rpymc": b"archive module cache",
        "lib/python3.12/compiled_only.pyc": b"only authoritative compiled bytes",
        "renpy/common/unknown_ren.pyc": b"unknown compiled-only authority",
    }
    archive = tmp_path / installer.SDK_ARCHIVE
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(f"renpy-{installer.SDK_RELEASE}-sdk/renpy/__pycache__/", b"")
        for name, value in files.items():
            bundle.writestr(f"renpy-{installer.SDK_RELEASE}-sdk/{name}", value)
    root = tmp_path / "sdk"
    entries = installer.sdk_entries(archive)
    installer.safe_extract(archive, root, entries)
    return archive, entries, root


def test_reuse_preserves_modified_caches_and_launcher_persistent_extra_files(sdk):
    archive, entries, root = sdk
    for relative in (
        "renpy/__pycache__/source.cpython-312.pyc", "renpy/legacy.pyc",
        "renpy/common/__pycache__/00db_ren.cpython-312.pyc",
        "renpy/common/00db.rpyc",
        "renpy/common/menu.rpyc", "renpy/common/module.rpymc",
    ):
        write(root / relative, "normal runtime rebuilt cache of different length")
    write(root / "launcher/game/saves/persistent", b"user persistent bytes")
    write(root / "launcher/log.txt", "old launcher log")
    write(root / "tmp/g/Info.plist", "build user metadata")
    (root / "empty-user-directory").mkdir()
    before = installer.sdk_tree_inventory(root)
    matched, records = installer.sdk_matches(root, archive, entries)
    assert matched and installer.sdk_tree_inventory(root) == before
    mutable = [item for item in records if item["mutable_cache"]]
    assert len(mutable) == 6
    assert all(item["authority"] and item["sha256"] != item["archive_sha256"] for item in mutable)
    assert any(item["path"] == "lib/python3.12/compiled_only.pyc" and not item["mutable_cache"] for item in records)


def test_missing_regenerable_cache_and_empty_cache_directory_do_not_replace_tree(sdk):
    archive, entries, root = sdk
    (root / "renpy/__pycache__/source.cpython-312.pyc").unlink()
    (root / "renpy/__pycache__").rmdir()
    before = snapshot(root)
    matched, records = installer.sdk_matches(root, archive, entries)
    assert matched and snapshot(root) == before
    record = next(item for item in records if item["path"] == "renpy/__pycache__/source.cpython-312.pyc")
    assert record["mutable_cache"] and record["sha256"] is None


@pytest.mark.parametrize("relative", [
    "renpy/source.py", "renpy.py", "lib/py3-windows-x86_64/runtime.dll",
    "lib/py3-windows-x86_64/python.exe", "lib/python3.12/compiled_only.pyc",
    "renpy/common/unknown_ren.pyc", "renpy/common/00db_ren.py",
])
@pytest.mark.parametrize("damage", ["missing", "changed"])
def test_authoritative_damage_refuses_reuse_without_modifying_existing_sdk(sdk, relative, damage):
    archive, entries, root = sdk
    if damage == "missing":
        (root / relative).unlink()
    else:
        write(root / relative, "modified authority")
    before = snapshot(root)
    with pytest.raises(installer.InstallError, match="authoritative file is missing or changed"):
        installer.sdk_matches(root, archive, entries)
    assert snapshot(root) == before


def test_user_source_cannot_make_a_compiled_only_archive_member_mutable(sdk):
    archive, entries, root = sdk
    write(root / "lib/python3.12/compiled_only.py", "# invented user authority")
    write(root / "lib/python3.12/compiled_only.pyc", "changed compiled-only member")
    with pytest.raises(installer.InstallError, match="authoritative file"):
        installer.sdk_matches(root, archive, entries)


def test_shipped_renpy_python_authority_regenerates_rpyc_without_a_guessed_pyc_rule(sdk):
    archive, entries, root = sdk
    write(root / "renpy/common/00db.rpyc", "runtime compiled the shipped 00db_ren.py")
    matched, records = installer.sdk_matches(root, archive, entries)
    record = next(item for item in records if item["path"] == "renpy/common/00db.rpyc")
    assert matched and record["mutable_cache"] and record["authority"] == "renpy/common/00db_ren.py"
    assert installer.sdk_cache_source(Path("renpy/common/unknown_ren.pyc"), {path for _,path in entries}) is None


def test_first_install_extracts_fixed_archive_but_existing_non_directory_refuses(sdk, tmp_path):
    archive, entries, _ = sdk
    target = tmp_path / "first"
    assert installer.sdk_matches(target, archive, entries) == (False, [])
    hashes = installer.safe_extract(archive, target, entries)
    assert all(installer.sha256(target / item["path"]) == item["sha256"] for item in hashes)
    write(tmp_path / "not-directory", "keep original")
    with pytest.raises(installer.InstallError, match="must be a directory"):
        installer.sdk_matches(tmp_path / "not-directory", archive, entries)


def test_extra_link_is_rejected_without_touching_sdk_or_external_target(sdk, tmp_path, monkeypatch):
    archive, entries, root = sdk
    outside = tmp_path / "outside"
    write(outside / "private", "do not touch")
    extra = root / "extra-link"
    # Simulate the link flag so Windows without symlink privilege still tests
    # the same rejection branch for an unknown entry outside ZIP membership.
    write(extra, "link sentinel")
    original = installer.linked
    monkeypatch.setattr(installer, "linked", lambda path: Path(path) == extra or original(path))
    with pytest.raises(installer.InstallError, match="links"):
        installer.sdk_matches(root, archive, entries)
    assert (outside / "private").read_text() == "do not touch"


def test_extra_windows_case_conflicts_are_refused(sdk, monkeypatch):
    archive, entries, root = sdk
    write(root / "user/notes.txt", "user notes")
    original = installer.Path.rglob
    extra = root / "user/notes.txt"
    def repeated_case(root_path, pattern):
        values = list(original(root_path, pattern))
        if root_path == root:
            values.append(extra)
        return iter(values)
    monkeypatch.setattr(installer.Path, "rglob", repeated_case)
    with pytest.raises(installer.InstallError, match="conflicting Windows paths"):
        installer.sdk_matches(root, archive, entries)


def test_version_probe_executes_only_isolated_official_copy_and_preserves_sdk(sdk, tmp_path, monkeypatch):
    archive, entries, root = sdk
    write(root / "renpy/__pycache__/source.cpython-312.pyc", "private old runtime cache")
    write(root / "launcher/game/saves/persistent", "private persistent")
    before = snapshot(root)
    run = tmp_path / "run"
    run.mkdir()
    calls = []
    def execute(argv, cwd, owner, label, report, **kwargs):
        calls.append((argv, cwd))
        assert cwd == run / "sdk-version-probe" and owner == run and label == "sdk-version"
        assert Path(argv[0]).is_relative_to(cwd) and Path(argv[1]).is_relative_to(cwd)
        assert (cwd / "renpy/__pycache__/source.cpython-312.pyc").read_bytes() == b"archive cache"
        write(cwd / "renpy/__pycache__/source.cpython-312.pyc", "version probe touched only isolated cache")
        write(cwd / "launcher/log.txt", "version probe touched only isolated launcher")
        return "Ren'Py " + installer.SDK_VERSION
    monkeypatch.setattr(installer, "execute", execute)
    report = {"sdk": {}}
    installer.verify_sdk_version(archive, entries, run, report)
    assert len(calls) == 1 and snapshot(root) == before
    assert report["sdk"]["version_probe_isolated"] and report["sdk"]["runtime_version_verified"]


def source_fixture(root):
    values = {
        "src/renpy_bridge/server.py": "# new core source",
        "requirements.lock": "setuptools==80.9.0\n",
        "pyproject.toml": "[project]\nname='fixture'\n",
        "plugin/renpy-bridge/.codex-plugin/plugin.json": json.dumps({"name": "renpy-bridge", "mcpServers": "./.mcp.json", "skills": "./skills/"}),
        "plugin/.agents/plugins/marketplace.json": json.dumps({"plugins": [{"name": "renpy-bridge", "source": {"source": "local", "path": "./renpy-bridge"}}]}),
    }
    for relative, value in values.items():
        write(root / relative, value)
    return [root / relative for relative in values]


def upgrade_fixture(tmp_path, sdk, monkeypatch):
    archive, entries, old_sdk = sdk
    home = tmp_path / "home"
    target = home / "runtimes" / installer.SDK_DIRECTORY
    shutil.copytree(old_sdk, target)
    write(target / "launcher/game/saves/persistent", "old persistent")
    write(target / "renpy/common/menu.rpyc", "runtime regenerated .rpyc")
    write(home / "src/renpy_bridge/server.py", "# previous core")
    write(home / ".runtime/Scripts/python.exe", "runtime sentinel")
    installer.atomic_json(home / ".installer/installed.json", {"schema_version": "1.0", "managed_entries": ["src"]})
    write(home / ".installer/install.lock", b"0")
    installer.atomic_json(home / "runtimes/sdk-lock.json", {"version": installer.SDK_VERSION})
    source = tmp_path / "source"
    files = source_fixture(source)
    run = home / ".installer/runs/upgrade"
    run.mkdir(parents=True)
    report = {"source_files": [{"path": path.relative_to(source).as_posix(), "sha256": installer.sha256(path)} for path in files]}
    monkeypatch.setattr(installer, "acquire_archive", lambda *args: (archive, "verified fixture archive"))
    calls = []
    def execute(argv, cwd, owner, label, state, **kwargs):
        calls.append(label)
        if label == "sdk-version":
            assert cwd == run / "sdk-version-probe"
            write(cwd / "renpy/common/menu.rpyc", "probe generated cache")
            return "Ren'Py " + installer.SDK_VERSION
        if label == "python-version":
            return json.dumps({"version": [3, 12, 11]})
        if label == "installed-environment":
            assert argv[-1] == "--no-runtime"
            return json.dumps({"ok": True, "home": str(home)})
        return ""
    monkeypatch.setattr(installer, "execute", execute)
    args = Namespace(sdk_source=None, register=False)
    return home, target, source, files, run, report, args, calls


def test_full_source_upgrade_reuses_complete_old_sdk_and_never_publishes_probe_tree(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    before = installer.sdk_tree_inventory(target)
    installer.run_install(args, source, home, files, run, report)
    assert report["installation_status"] == "installed"
    assert report["sdk"]["reused_sdk"] and report["sdk"]["reused_full_tree_preserved"]
    assert report["sdk"]["authoritative_archive_files_verified"]
    assert installer.sdk_tree_inventory(target) == before
    assert installer.read_json(run / "publication.json").get("sdk") is None
    assert not (run / "replaced-sdk").exists()
    assert calls.count("sdk-version") == 1


@pytest.mark.parametrize("state", ["queued", "running"])
def test_active_jobs_refuse_before_version_probe_or_publication(tmp_path, sdk, monkeypatch, state):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    installer.atomic_json(home / "jobs/active/status.json", {"state": state})
    before = snapshot(home)
    with pytest.raises(installer.InstallError, match="Finish/cancel managed jobs"):
        installer.run_install(args, source, home, files, run, report)
    assert calls == [] and snapshot(home) == before


def test_authoritative_damage_stops_upgrade_without_sdk_replacement_or_runtime_execution(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    write(target / "lib/py3-windows-x86_64/runtime.dll", "changed DLL")
    before = snapshot(target)
    with pytest.raises(installer.InstallError, match="authoritative file"):
        installer.run_install(args, source, home, files, run, report)
    assert calls == [] and snapshot(target) == before
    assert not (run / "sdk").exists() and not (run / "publication.json").exists()
    assert (home / "src/renpy_bridge/server.py").read_text() == "# previous core"


def test_interrupted_source_publication_recovers_previous_code_and_entire_reused_sdk(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    before_sdk = installer.sdk_tree_inventory(target)
    old_code = snapshot(home / "src")
    real_replace = installer.os.replace
    def interrupted(original, destination):
        result = real_replace(original, destination)
        if Path(original) == run / "code/src":
            raise KeyboardInterrupt("interrupted after actual code rename")
        return result
    monkeypatch.setattr(installer.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt, match="actual code rename"):
        installer.run_install(args, source, home, files, run, report)
    assert report["installation_status"] == "code_rolled_back"
    assert snapshot(home / "src") == old_code
    assert installer.sdk_tree_inventory(target) == before_sdk
    assert installer.read_json(run / "publication.json")["state"] == "rolled_back"
    assert not (run / "replaced-sdk").exists()


def test_sdk_concurrent_change_during_probe_refuses_source_publication(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    execute = installer.execute
    def changed(argv, cwd, owner, label, state, **kwargs):
        response = execute(argv, cwd, owner, label, state, **kwargs)
        if label == "sdk-version":
            write(target / "launcher/new-user-save", "concurrent outside installer writer")
        return response
    monkeypatch.setattr(installer, "execute", changed)
    with pytest.raises(installer.InstallError, match="Reused SDK changed"):
        installer.run_install(args, source, home, files, run, report)
    assert not (run / "publication.json").exists()
    assert (home / "src/renpy_bridge/server.py").read_text() == "# previous core"


def test_first_install_publishes_pristine_archive_not_version_probe_changes(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    assert target.resolve().is_relative_to(tmp_path.resolve())
    target.rename(tmp_path / "fixture-sdk-not-installed")
    installer.run_install(args, source, home, files, run, report)
    assert report["installation_status"] == "installed" and not report["sdk"]["reused_sdk"]
    assert report["sdk"]["verified_original_files_only"]
    assert (target / "renpy/common/menu.rpyc").read_bytes() == b"archive RenPy cache"
    assert (run / "sdk-version-probe/renpy/common/menu.rpyc").read_bytes() == b"probe generated cache"
    assert not (target / "launcher/game/saves/persistent").exists()
    assert installer.read_json(run / "publication.json")["sdk"]["state"] == "published"


def test_job_started_during_preparation_refuses_publication_without_changing_sdk(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    before_sdk = snapshot(target)
    execute = installer.execute
    def active(argv, cwd, owner, label, state, **kwargs):
        result = execute(argv, cwd, owner, label, state, **kwargs)
        if label == "install-locked-dependencies":
            installer.atomic_json(home / "jobs/new-active/status.json", {"state": "running"})
        return result
    monkeypatch.setattr(installer, "execute", active)
    with pytest.raises(installer.InstallError, match="Finish/cancel managed jobs"):
        installer.run_install(args, source, home, files, run, report)
    assert snapshot(target) == before_sdk
    assert not (run / "publication.json").exists()
    assert (home / "src/renpy_bridge/server.py").read_text() == "# previous core"


def test_version_mismatch_in_isolated_probe_prevents_upgrade_without_touching_sdk(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    before = snapshot(target)
    execute = installer.execute
    def wrong_version(argv, cwd, owner, label, state, **kwargs):
        result = execute(argv, cwd, owner, label, state, **kwargs)
        return "Ren'Py 8.0.0" if label == "sdk-version" else result
    monkeypatch.setattr(installer, "execute", wrong_version)
    with pytest.raises(installer.InstallError, match="expected full version"):
        installer.run_install(args, source, home, files, run, report)
    assert snapshot(target) == before and not (run / "publication.json").exists()


def test_first_sdk_publication_interruption_rolls_back_code_and_new_sdk(tmp_path, sdk, monkeypatch):
    home, target, source, files, run, report, args, calls = upgrade_fixture(tmp_path, sdk, monkeypatch)
    assert target.resolve().is_relative_to(tmp_path.resolve())
    target.rename(tmp_path / "fixture-sdk-not-installed")
    old_code = snapshot(home / "src")
    original = installer.os.replace
    def interrupted(first, second):
        result = original(first, second)
        if Path(first) == run / "sdk":
            raise KeyboardInterrupt("interrupted after first SDK publication")
        return result
    monkeypatch.setattr(installer.os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt, match="first SDK publication"):
        installer.run_install(args, source, home, files, run, report)
    assert snapshot(home / "src") == old_code and not target.exists()
    assert installer.read_json(run / "publication.json")["state"] == "rolled_back"
