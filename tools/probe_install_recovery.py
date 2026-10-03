"""Exercise installer filesystem publication, interruption and path refusal.

Publication destinations, old/new SDK sentinels and faults live in this
immutable workspace evidence run. One owned temporary directory under the
existing personal installation checks Windows Store path canonicalization and
is removed afterward. This is not pip/SDK/Codex registration acceptance.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/m7-install-recovery"
spec = importlib.util.spec_from_file_location("bridge_personal_installer", ROOT / "scripts/install_local.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class SimulatedInterruption(BaseException):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict:
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        current = Path(directory)
        for name in list(dirs):
            if installer.linked(current / name):
                result[(current / name).relative_to(root).as_posix()] = "link-refused"
                dirs.remove(name)
        for name in files:
            path = current / name
            result[path.relative_to(root).as_posix()] = "link-refused" if installer.linked(path) else digest(path)
    return result


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def fixture(case_dir: Path, first_install: bool = False):
    destination = case_dir / "installation"
    destination.mkdir(parents=True)
    run = destination / ".installer/runs/upgrade"
    run.mkdir(parents=True)
    names = ["src", "plugin", "docs"]
    (destination / "runtimes").mkdir()
    if not first_install:
        for name in ("src", "plugin"):
            write(destination / name / "owned.txt", "previous:" + name)
        write(destination / "runtimes" / installer.SDK_DIRECTORY / "runtime-sentinel.txt", "previous-sdk")
    write(destination / ".runtime/Scripts/python.exe", "runtime-must-be-retained")
    if not first_install:
        installer.atomic_json(destination / ".installer/installed.json", {
            "schema_version": "1.0", "destination": str(destination), "managed_entries": ["src", "plugin"], "run_id": "previous"})
        installer.atomic_json(destination / "runtimes/sdk-lock.json", {"version": "previous", "sha256": "previous-hash"})
    for path, text in {
        "projects/managed/model/head.json": '{"revision":17}',
        "projects/managed/assets/raw-sentinel.bin": "raw-owned-user-material",
        "projects/managed/history/17.json": "retained-history",
        "jobs/previous/status.json": '{"job_id":"previous","state":"completed"}',
        "backups/user-backup/sentinel.txt": "retained-user-backup",
        "checkpoints/user-checkpoint/sentinel.txt": "retained-checkpoint",
    }.items():
        write(destination / path, text)
    protected = {name: snapshot(destination / name) for name in ("projects", "jobs", "checkpoints")}
    protected["user-backup"] = snapshot(destination / "backups/user-backup")
    protected["runtime"] = snapshot(destination / ".runtime")
    original = {name: snapshot(destination / name) for name in ("src", "plugin", "docs", "runtimes")}
    original["marker"] = None if first_install else digest(destination / ".installer/installed.json")
    backup = destination / "backups" / ("install-" + run.name)
    backup.mkdir(parents=True)
    installer.copy_backup(destination, backup, names)
    for name in names:
        write(run / "code" / name / "owned.txt", "candidate:" + name)
    write(run / "sdk/runtime-sentinel.txt", "candidate-sdk")
    return destination, run, names, original, protected


def assert_preserved(destination: Path, protected: dict) -> None:
    for name in ("projects", "jobs", "checkpoints"):
        assert snapshot(destination / name) == protected[name], name
    assert snapshot(destination / "backups/user-backup") == protected["user-backup"]
    assert snapshot(destination / ".runtime") == protected["runtime"]


def assert_restored(destination: Path, original: dict, protected: dict) -> None:
    for name in ("src", "plugin", "docs", "runtimes"):
        assert snapshot(destination / name) == original[name], name
    if original["marker"] is None:
        assert not (destination / ".installer/installed.json").exists()
    else:
        assert digest(destination / ".installer/installed.json") == original["marker"]
    assert_preserved(destination, protected)


def main() -> int:
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "probe": "installer-publication-and-interrupted-recovery",
              "run_directory": str(run_dir), "complete": False, "cases": [],
              "scope": "Actual workspace-contained filesystem rename/restore and refusal checks; injected interruption boundaries. No pip install, SDK runtime acceptance or Codex registration is claimed.",
              "registration_executed": False, "development_projects_modified": False,
              "code_sha256": {"scripts/install_local.py": digest(ROOT / "scripts/install_local.py"),
                              "tools/probe_install_recovery.py": digest(Path(__file__).resolve())},
              "atomicity_boundary": "Journaled code and SDK tree publication plus SDK-lock and installation marker restore. Destination venv dependency/local-wheel updates and verified SDK archive cache are retained on failure; whole-environment atomic rollback is not claimed."}
    def case(name, callback):
        started = time.monotonic()
        path = run_dir / "cases" / name
        path.mkdir(parents=True)
        try:
            detail = callback(path) or {}
            report["cases"].append({"name": name, "passed": True, "duration_seconds": time.monotonic() - started, **detail})
        except BaseException as exc:
            report["cases"].append({"name": name, "passed": False, "error": repr(exc), "traceback": traceback.format_exc()})

    def committed(path):
        destination, run, names, original, protected = fixture(path)
        journal = installer.publish_code(destination, run, names, run / "sdk")
        assert all((destination / name / "owned.txt").read_text() == "candidate:" + name for name in names)
        assert (destination / "runtimes" / installer.SDK_DIRECTORY / "runtime-sentinel.txt").read_text() == "candidate-sdk"
        journal["state"] = "committed"
        installer.atomic_json(run / "publication.json", journal)
        before = snapshot(destination)
        assert installer.recover_interrupted(destination) == []
        assert snapshot(destination) == before
        assert_preserved(destination, protected)
        return {"publication_journal": str(run / "publication.json"), "protected_hashes": protected}
    case("committed-upgrade-preserves-projects", committed)

    def initial_rollback(path):
        destination, run, names, original, protected = fixture(path, first_install=True)
        journal = installer.publish_code(destination, run, names, run / "sdk")
        installer.atomic_json(destination / "runtimes/sdk-lock.json", {"version": "candidate"})
        installer.atomic_json(destination / ".installer/installed.json", {"schema_version": "1.0", "managed_entries": names})
        installer.rollback_publication(destination, run, journal)
        assert_restored(destination, original, protected)
        assert (run / "rolled-back-sdk" / installer.SDK_DIRECTORY / "runtime-sentinel.txt").is_file()
        return {"publication_journal": str(run / "publication.json"), "no_previous_code_or_sdk": True}
    case("first-publication-rollback-removes-new-entries", initial_rollback)

    def interruption(path, boundary):
        destination, run, names, original, protected = fixture(path)
        real_replace = installer.os.replace
        hits = []
        def stopped(source, target):
            result = real_replace(source, target)
            if Path(source) == boundary(destination, run)[0] and Path(target) == boundary(destination, run)[1]:
                hits.append({"source": str(source), "target": str(target)})
                raise SimulatedInterruption("simulated process loss after actual rename, before journal update")
            return result
        with patch.object(installer.os, "replace", stopped):
            try:
                installer.publish_code(destination, run, names, run / "sdk")
                raise AssertionError("fault boundary was never exercised")
            except SimulatedInterruption:
                pass
        assert len(hits) == 1
        incomplete = installer.read_json(run / "publication.json")
        assert incomplete["state"] == "publishing"
        recovered = installer.recover_interrupted(destination)
        assert recovered == [str(run / "publication.json")]
        assert_restored(destination, original, protected)
        assert installer.read_json(run / "publication.json")["state"] == "rolled_back"
        assert installer.recover_interrupted(destination) == []
        return {"injected_boundary": hits[0], "recovered_publications": recovered, "protected_hashes": protected}
    boundaries = {
        "sdk-previous-move": lambda d, r: (d / "runtimes" / installer.SDK_DIRECTORY, r / "replaced-sdk" / installer.SDK_DIRECTORY),
        "sdk-candidate-move": lambda d, r: (r / "sdk", d / "runtimes" / installer.SDK_DIRECTORY),
        "code-previous-move": lambda d, r: (d / "src", r / "replaced/src"),
        "code-candidate-move": lambda d, r: (r / "code/src", d / "src"),
        "new-entry-move": lambda d, r: (r / "code/docs", d / "docs"),
    }
    for name, boundary in boundaries.items():
        case("interrupted-" + name, lambda path, boundary=boundary: interruption(path, boundary))

    def rejected_write(path):
        destination, run, names, original, protected = fixture(path)
        real_replace = installer.os.replace
        def rejected(source, target):
            if Path(source) == run / "code/plugin":
                raise PermissionError("injected destination publication failure")
            return real_replace(source, target)
        with patch.object(installer.os, "replace", rejected):
            try:
                installer.publish_code(destination, run, names, run / "sdk")
                raise AssertionError("publication fault was not exercised")
            except PermissionError:
                pass
        installer.rollback_publication(destination, run, installer.read_json(run / "publication.json"))
        assert_restored(destination, original, protected)
        return {"publication_journal": str(run / "publication.json")}
    case("publication-failure-restores-code-and-sdk", rejected_write)

    def config_recovery(path):
        destination, run, names, original, protected = fixture(path)
        installer.publish_code(destination, run, names, run / "sdk")
        installer.atomic_json(destination / "runtimes/sdk-lock.json", {"version": "candidate"})
        installer.atomic_json(destination / ".installer/installed.json", {"schema_version": "1.0", "destination": str(destination), "managed_entries": names})
        installer.recover_interrupted(destination)
        assert_restored(destination, original, protected)
    case("interrupt-after-config-writes-restores-markers", config_recovery)

    def refused_scope(path):
        destination, run, names, original, protected = fixture(path)
        before = snapshot(destination)
        try:
            installer.publish_code(destination, run, ["src", "projects"], run / "sdk")
            raise AssertionError("unmanaged publication was accepted")
        except installer.InstallError:
            pass
        assert snapshot(destination) == before
        installer.atomic_json(run / "publication.json", {"state": "publishing", "entries": [{"name": "projects", "had_previous": True, "state": "pending"}, {"name": "src", "had_previous": True, "state": "pending"}]})
        before = snapshot(destination)
        try:
            installer.recover_interrupted(destination)
            raise AssertionError("unmanaged recovery scope was accepted")
        except installer.InstallError:
            pass
        assert snapshot(destination) == before
        assert_preserved(destination, protected)
    case("unmanaged-publication-and-journal-rejected", refused_scope)

    def bad_marker(path):
        destination, run, names, original, protected = fixture(path)
        installer.atomic_json(destination / ".installer/installed.json", {"schema_version": "future", "managed_entries": ["src"]})
        args = SimpleNamespace(sdk_source=None, register=False)
        before = snapshot(destination)
        try:
            installer.run_install(args, ROOT, destination, [], run, {})
            raise AssertionError("unknown installation marker was accepted")
        except installer.InstallError as exc:
            assert "unsupported schema" in str(exc)
        after = snapshot(destination)
        before.pop(".installer/install.lock", None)
        after.pop(".installer/install.lock", None)
        assert before == after
        assert_preserved(destination, protected)
    case("unknown-marker-rejected-before-publication", bad_marker)

    def escaping_sdk(path):
        destination, run, names, original, protected = fixture(path)
        escaped = path / "outside-sdk"
        write(escaped / "sentinel.txt", "outside-must-remain")
        before = snapshot(destination)
        outside_before = snapshot(escaped)
        try:
            installer.publish_code(destination, run, names, escaped)
            raise AssertionError("SDK staging outside installation root was accepted")
        except installer.InstallError:
            pass
        assert snapshot(destination) == before and snapshot(escaped) == outside_before
    case("out-of-root-sdk-stage-rejected", escaping_sdk)

    def link_refusal(path):
        destination, run, names, original, protected = fixture(path)
        outside = path / "outside-target"
        write(outside / "sentinel.txt", "outside-link-target-must-remain")
        link = run / "code/docs/junction"
        helper = path / "create-junction.ps1"
        write(helper, 'param([string]$LinkPath,[string]$TargetPath)\n$ErrorActionPreference="Stop"\nNew-Item -ItemType Junction -Path $LinkPath -Target $TargetPath | Out-Null\n')
        process = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper), "-LinkPath", str(link), "-TargetPath", str(outside)], capture_output=True, timeout=15,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        assert process.returncode == 0, process.stderr.decode(errors="replace")
        assert installer.linked(link)
        before = snapshot(destination)
        try:
            installer.publish_code(destination, run, names, run / "sdk")
            raise AssertionError("linked staged code was accepted")
        except installer.InstallError:
            pass
        assert snapshot(destination) == before
        assert (outside / "sentinel.txt").read_text() == "outside-link-target-must-remain"
        try:
            installer.unlinked_path(link / "sentinel.txt")
            raise AssertionError("linked destination traversal was accepted")
        except installer.InstallError:
            pass
        assert_preserved(destination, protected)
        return {"junction": str(link), "target_sentinel_sha256": digest(outside / "sentinel.txt")}
    case("junction-in-staging-and-traversal-rejected", link_refusal)

    def canonical_personal_path(path):
        requested = Path(os.environ["LOCALAPPDATA"]) / "RenPyBridge/.installer/canonicalization-probes" / run_dir.name
        candidate = installer.unlinked_path(requested)
        candidate.mkdir(parents=True, exist_ok=False)
        canonical = installer.unlinked_path(candidate)
        sentinel = installer.bounded(canonical, canonical / "owned-canonicalization-sentinel.txt")
        try:
            write(sentinel, "owned-installer-canonicalization-probe")
            assert sentinel.resolve().parent == canonical
            mcp = installer.installed_mcp(canonical)["mcpServers"]["renpy_bridge"]
            assert Path(mcp["env"]["RENPY_BRIDGE_HOME"]).resolve() == canonical
            assert Path(mcp["env"]["PYTHONPATH"]).resolve() == canonical / "src"
            assert installer.bounded(canonical, canonical / ".installer/runs/test.json").is_relative_to(canonical)
            return {"requested_destination": str(requested), "before_mkdir_resolved": str(candidate),
                    "canonical_home": str(canonical), "host_virtualization_observed": str(requested) != str(canonical),
                    "mcp_configuration_home": mcp["env"]["RENPY_BRIDGE_HOME"],
                    "sentinel_sha256": digest(sentinel), "temporary_directory_removed": True,
                    "personal_code_config_modified": False}
        finally:
            sentinel.unlink(missing_ok=True)
            canonical.rmdir()
    case("actual-personal-target-canonicalization", canonical_personal_path)

    report["complete"] = all(item["passed"] for item in report["cases"])
    report["case_count"] = len(report["cases"])
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    (run_dir / "report.json").write_text(content, encoding="utf-8")
    (EVIDENCE / "report.json").write_text(content, encoding="utf-8")
    print(json.dumps({"report_path": str(run_dir / "report.json"), "complete": report["complete"], "case_count": len(report["cases"])}, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
