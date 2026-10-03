"""Archive a tested same-window HTML extension, its source, locks and project.

This independent tool does not modify the legacy 0.1.3 delivery or certify a
runtime. It refuses unfinished/stale builds, missing recoverable packages,
unbound native source, and missing dependency license bodies.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import Parser
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_html_delivery_legacy", Path(__file__).with_name("package_delivery.py"))
legacy = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = legacy
_spec.loader.exec_module(legacy)
DeliveryError = legacy.DeliveryError
file_hash, read_json, digest = legacy.file_hash, legacy.read_json, legacy.digest
unlinked, child, relative, bounded = legacy.unlinked, legacy.child, legacy.relative, legacy.bounded

SOURCE_FILES = (".gitignore", ".gitattributes", "pyproject.toml", "requirements.lock", "START_HERE.md")
SOURCE_TREES = ("src", "tests", "docs", "plugin", "scripts", "tools", "examples/html_minigames",
                "native/html_minigame_embedded_host")
PROJECT_TREES = (*sorted(legacy.PROJECT_TREES), "minigames")
SKIP_PARTS = {"__pycache__", ".pytest_cache", "node_modules", "bin", "obj", ".git", ".venv"}
NATIVE_NAMES = {"packages.lock.json", "app.manifest"}


def source_files(root):
    """Prune private/build trees before inspecting their intentionally skipped links.

    Included paths retain the archive's strict link and regular-file checks.
    Filtering a completed recursive inventory is too late for node_modules
    junctions and can inspect dependencies which are outside this snapshot.
    """
    root = unlinked(root)
    if not root.is_dir():
        raise DeliveryError("Required directory missing: " + str(root))

    def excluded(path):
        rel = path.relative_to(root)
        return any(part.casefold() in SKIP_PARTS for part in rel.parts) or legacy.private_path(rel)

    def scan_error(error):
        raise DeliveryError("Cannot inventory required source directory: " + str(error)) from error

    files = []
    for directory, dirs, names in os.walk(root, topdown=True, followlinks=False, onerror=scan_error):
        base = bounded(root, directory)
        dirs[:] = [name for name in dirs if not excluded(base / name)]
        for name in dirs:
            bounded(root, base / name)
        for name in names:
            path = base / name
            if excluded(path):
                continue
            path = bounded(root, path)
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise DeliveryError("Required source is not a regular unlinked file: " + str(path))
            files.append(path)
    return sorted(files)


def _file_record(path):
    return {"sha256": file_hash(path), "bytes": path.stat().st_size}


def _verify_file(path, record):
    path = unlinked(path)
    if (not isinstance(record, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256", "")))
            or type(record.get("bytes")) is not int or record["bytes"] < 0
            or not path.is_file() or _file_record(path) != {key: record[key] for key in ("sha256", "bytes")}):
        raise DeliveryError("Missing or changed recorded file: " + str(path))


def historical_staged_package(report, package):
    """Identify only the cleaned ZIP's provenance record, never raw evidence.

    The final player ZIP is verified separately before this record is omitted
    from recursive evidence collection. A matching hash alone cannot exempt
    another missing path, even when its dictionary has the same contents.
    """
    staging = report.get("release_staging")
    if staging is None:
        return None
    if not isinstance(staging, dict):
        raise DeliveryError("Cleaned staged ZIP provenance is not an object")
    if "staged_package" not in staging:
        return None
    staged = staging["staged_package"]
    cleanup = staging.get("cleanup", {})
    released = report["release"]
    if (not isinstance(staged, dict) or not isinstance(cleanup, dict)
            or set(staged) != {"path", "sha256", "bytes"}
            or staged.get("sha256") != released.get("sha256")
            or type(staged.get("bytes")) is not int or staged["bytes"] != released.get("bytes")
            or cleanup.get("status") != "removed" or cleanup.get("directory_exists") is not False
            or cleanup.get("ownership_verified") is not True):
        raise DeliveryError("Cleaned staged ZIP provenance differs from the verified final release")
    values = [staging.get("requested_directory"), staging.get("canonical_directory"),
              staging.get("output_directory"), cleanup.get("directory", staging.get("canonical_directory")), staged.get("path")]
    if any(not isinstance(value, str) or not Path(value).is_absolute() for value in values):
        raise DeliveryError("Cleaned staged ZIP has no exact owned staging provenance")
    requested, canonical, output, removed, original = map(unlinked, values)
    if (requested != canonical or removed != canonical or output != canonical / "o"
            or original != output / package.name or original.exists()):
        raise DeliveryError("Cleaned staged ZIP path is not the reported removed staging output")
    return staged


def validate_native_source(build, native_root):
    """Require source recorded during the actual build, never a later stamp."""
    record = build.get("native_source")
    files = {path.relative_to(native_root).as_posix(): _file_record(path) for path in source_files(native_root)
             if path.suffix in {".cs", ".csproj"} or path.name in NATIVE_NAMES}
    required = {"Program.cs", "NativeParent.cs", "HtmlMinigameEmbeddedHost.csproj", "packages.lock.json", "app.manifest"}
    if (not isinstance(record, dict) or record.get("root") != "native/html_minigame_embedded_host"
            or not required <= set(files) or record.get("files") != files or record.get("tree_sha256") != digest(files)):
        raise DeliveryError("Actual embedded-host build has no matching native source/lock binding")
    outputs = build.get("build", {})
    if outputs.get("exit_code") != 0 or outputs.get("self_contained") is not True:
        raise DeliveryError("Embedded host was not successfully built self-contained")
    if any(item.get("path") == "build-report.json" for item in outputs.get("files", [])):
        raise DeliveryError("Host output inventory cannot contain its own build report")
    return record


def validate_html_build(report, model):
    if (report.get("kind") != "build" or report.get("ok") is not True or report.get("status") != "passed"
            or report.get("independent_release_verified") is not True
            or report.get("minigame_package_members", {}).get("all_declared_files_included") is not True
            or report.get("execution", {}).get("tested_and_published_identical") is not True
            or report.get("execution", {}).get("package_members_unchanged") is not True):
        raise DeliveryError("Expected a passed actual HTML build with verified independent package members")
    minimum = report.get("minimum_minigame_release_coverage", {})
    if minimum.get("minimum_outcomes_verified_by_declared_scenarios") is not True:
        raise DeliveryError("Build has no minimum declared outcome/cancel/host-failure coverage")
    outcomes = {(frame_id, outcome) for frame_id, frame in model.get("frames", {}).items()
        if frame.get("flow", {}).get("kind") == "minigame"
        for outcome in [*model["minigames"][frame["flow"]["game_id"]]["manifest"]["results"]["outcomes"], "cancel", "error"]}
    if not outcomes:
        raise DeliveryError("Selected project has no HTML calls")
    for name in ("minigame_verification", "minigame_package_verification"):
        suite = report.get(name, {})
        count = suite.get("requested")
        cases = suite.get("reports", [])
        if (suite.get("passed") is not True or type(count) is not int or not 1 <= count <= 1024
                or suite.get("executed") != count or len(cases) != count):
            raise DeliveryError("Incomplete actual same-window scenario suite: " + name)
        seen = set()
        for case in cases:
            if (case.get("passed") is not True or case.get("requested_testcase_passed") is not True
                    or case.get("injected_results") is not False
                    or case.get("same_window_verification", {}).get("passed") is not True
                    or len(case.get("screenshots", [])) != 2):
                raise DeliveryError("A concrete testcase/window/screenshot result is not passed: " + name)
            scenario = case.get("scenario", {})
            if scenario.get("expected_outcome") == "error" and scenario.get("fault_plan") != {"type": "terminate_own_host", "after": "running"}:
                raise DeliveryError("Error scenario is not the real owned-host fault plan")
            seen.add((scenario.get("frame_id"), scenario.get("expected_outcome")))
        if not outcomes <= seen:
            raise DeliveryError("Actual suite omits declared outcomes or system exits: " + name)


def _managed_games(project):
    from renpy_bridge.minigame_packages import validate_directory
    games = {}
    root = child(project, "minigames")
    if not root.is_dir():
        raise DeliveryError("Managed immutable minigames directory is missing")
    for directory in sorted(root.iterdir()):
        unlinked(directory)
        if directory.name.startswith("."):
            continue  # Failed import staging is not an immutable registered package.
        if not directory.is_dir() or not re.fullmatch(r"[0-9a-f]{64}", directory.name):
            raise DeliveryError("Unexpected immutable package directory: " + str(directory))
        info = validate_directory(directory)
        if info.digest != directory.name:
            raise DeliveryError("Managed package directory differs from its tree hash")
        games[info.digest] = info
    return games


def collect_python_licenses(source_root, add):
    """Copy exact locked distribution metadata and actual license bodies."""
    site = child(source_root, ".venv/Lib/site-packages")
    if not site.is_dir():
        raise DeliveryError("Locked authoring dependency license inventory requires the workspace venv")
    installed = {}
    for directory in site.glob("*.dist-info"):
        metadata = child(directory, "METADATA")
        if not metadata.is_file():
            continue
        fields = Parser().parsestr(metadata.read_text(encoding="utf-8"))
        key = re.sub(r"[-_.]+", "-", fields.get("Name", "")).lower()
        installed.setdefault(key, []).append((directory, fields))
    index = []
    for line in (source_root / "requirements.lock").read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line.strip())
        if not match:
            raise DeliveryError("Dependency lock contains a non-exact unsupported requirement")
        name, version = match.groups()
        key = re.sub(r"[-_.]+", "-", name).lower()
        candidates = [(directory, fields) for directory, fields in installed.get(key, []) if fields.get("Version") == version]
        if len(candidates) != 1:
            raise DeliveryError("Locked dependency metadata missing or ambiguous: " + line)
        directory, fields = candidates[0]
        bodies = [path for path in source_files(directory) if "licenses" in path.relative_to(directory).parts
                  or re.search(r"license|licence|copying|notice", path.name, re.IGNORECASE)]
        if not bodies or any(not path.stat().st_size for path in bodies):
            raise DeliveryError("Locked dependency has no preserved license body: " + line)
        prefix = "bridge-source/licenses/python/" + directory.name + "/"
        add(directory / "METADATA", prefix + "METADATA", "python_dependency_metadata")
        for path in bodies:
            add(path, prefix + path.relative_to(directory).as_posix(), "python_dependency_license")
        index.append({"name": name, "version": version, "license_files": [prefix + path.relative_to(directory).as_posix() for path in bodies]})
    return index


def prepare_plan(home, project_id, build_report, output, source_root=ROOT):
    home, source_root, output = unlinked(home), unlinked(source_root), unlinked(output)
    if output.exists():
        raise DeliveryError("Output already exists; choose a new directory")
    forbidden = [home / "projects", home / "jobs", *(source_root / name for name in SOURCE_TREES), source_root / "runtimes", source_root / "evidence"]
    if output in {home, source_root} or any(output.is_relative_to(path) or path.is_relative_to(output) for path in forbidden):
        raise DeliveryError("Output overlaps a delivery input tree")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", project_id):
        raise DeliveryError("Invalid project ID")
    project = child(home, "projects/" + project_id)
    report_path = unlinked(build_report)
    if not (report_path.is_relative_to(home / "jobs") or report_path.is_relative_to(source_root / "evidence")):
        raise DeliveryError("Build report leaves managed job/workspace evidence roots")
    report = read_json(report_path)
    head_path = child(project, "model/head.json")
    head = read_json(head_path)
    model = read_json(child(project, head.get("model_path")))
    revision = head.get("revision")
    if (head.get("schema_version") != "1.1" or model.get("schema_version") != "1.1"
            or type(revision) is not int or revision < 0 or model.get("revision") != revision
            or model.get("project_id") != project_id or report.get("project_id") != project_id
            or report.get("revision") != revision or report.get("model_hash") != digest(model)
            or head.get("runnable_revision") != revision):
        raise DeliveryError("Current schema1.1 model/revision is not this runnable build")
    validate_html_build(report, model)
    hashes, code_digest = legacy.core_hashes(source_root)
    if legacy.core_hashes(home) != (hashes, code_digest) or report.get("code_digest") != code_digest:
        raise DeliveryError("Build/current authoring core source hashes do not match")
    baseline = child(project, head.get("runnable_project_path"))
    generated = bounded(project / "generated", Path(report.get("generation", {}).get("project_path", "")))
    if not baseline.is_dir() or not baseline.is_relative_to(project / "generated") or not generated.is_dir():
        raise DeliveryError("Runnable/generated baseline is missing")
    configuration = read_json(child(generated, "game/bridge-minigames.json"))
    if (configuration.get("host", {}).get("window_mode") != "embedded_same_top_level_window"
            or configuration.get("developer") is not False):
        raise DeliveryError("Tested HTML configuration is not the same visible window")
    release = report.get("release", {})
    package = bounded(project / "releases", Path(release.get("path", "")))
    independent = bounded(project / "releases", Path(release.get("independent_directory", "")))
    exe = bounded(independent, Path(release.get("executable", "")))
    if package.suffix.lower() != ".zip" or not exe.is_file() or exe.suffix.lower() != ".exe" or type(release.get("entries")) is not int:
        raise DeliveryError("Final tested ZIP/executable is missing")
    _verify_file(package, release)
    if file_hash(exe) != release.get("executable_sha256"):
        raise DeliveryError("Published executable differs from its tested report")
    members = legacy.package_members(package, independent, release["entries"])
    from renpy_bridge.pipeline import _minigame_required_members
    declared = _minigame_required_members(read_json(child(generated, "bridge-manifest.json")), configuration)
    released = {path.relative_to(independent).as_posix() for path, _, _ in members}
    if file_hash(child(independent, "game/bridge-minigames.json")) != file_hash(child(generated, "game/bridge-minigames.json")):
        raise DeliveryError("Published configuration differs from the tested generated configuration")
    for name, record in declared.items():
        if name not in released:
            raise DeliveryError("Player ZIP omits a declared current/retained package or runtime member")
        _verify_file(child(independent, name), record)
    with zipfile.ZipFile(package) as archive:
        if any(info.orig_filename != info.filename or info.flag_bits & 1 for info in archive.infolist()):
            raise DeliveryError("Release ZIP contains truncated/encrypted members")
    staged_provenance = historical_staged_package(report, package)
    items, destinations = [], {}
    scans = {}

    def add(path, destination, category, expected=None):
        path = unlinked(path)
        relative(destination)
        if not path.is_file() or path.stat().st_nlink != 1:
            raise DeliveryError("Required archive input is missing/non-regular or hardlinked: " + str(path))
        record = _file_record(path)
        if expected is not None and record["sha256"] != expected:
            raise DeliveryError("Archive evidence/content hash differs: " + str(path))
        item = legacy.CopyItem(path, destination, record["sha256"], record["bytes"], category)
        key = destination.casefold()
        if key in destinations:
            if (destinations[key].sha256, destinations[key].bytes) != (item.sha256, item.bytes):
                raise DeliveryError("Archive destination collision: " + destination)
            return
        destinations[key] = item
        items.append(item)

    for name in SOURCE_FILES:
        add(child(source_root, name), "bridge-source/" + name, "bridge_source")
    for name in SOURCE_TREES:
        root = child(source_root, name)
        files = source_files(root)
        scans[root] = [path.relative_to(root).as_posix() for path in files]
        for path in files:
            add(path, "bridge-source/" + path.relative_to(source_root).as_posix(), "bridge_source")
    games = _managed_games(project)
    for path in legacy.tree_files(child(project, "history/revisions"), suffixes={".json"}):
        old_model = read_json(path)
        for game in old_model.get("minigames", {}).values():
            info = games.get(game.get("package_digest"))
            if info is None or game.get("path") != "minigames/" + info.digest or game.get("manifest") != info.manifest:
                raise DeliveryError("Historical immutable HTML package cannot be restored")
        for asset in old_model.get("assets", {}).values():
            media = child(project, asset.get("path"))
            if not media.is_relative_to(project / "assets") or file_hash(media) != asset.get("sha256"):
                raise DeliveryError("Historical managed media cannot be restored")
    for path in child(project, "checkpoints").glob("snap_*.json"):
        checkpoint = read_json(path)
        old_revision = checkpoint.get("revision")
        if type(old_revision) is not int or not 0 <= old_revision <= revision or digest(read_json(child(project, f"history/revisions/{old_revision:08d}.json"))) != checkpoint.get("model_hash"):
            raise DeliveryError("Checkpoint model is missing or changed")
    legacy.validate_visual_baselines(project, project_id)
    for name in PROJECT_TREES:
        root = child(project, name)
        if root.exists():
            for path in source_files(root):
                add(path, "managed-home/projects/" + project_id + "/" + path.relative_to(project).as_posix(), "managed_project")
    sdk_lock_path = child(home, "runtimes/sdk-lock.json")
    sdk_lock = read_json(sdk_lock_path)
    if sdk_lock.get("version") != report.get("sdk_version"):
        raise DeliveryError("Tested RenPy SDK does not match its lock")
    add(sdk_lock_path, "metadata/sdk-lock.json", "sdk_lock")
    add(child(home, "runtimes/renpy-8.5.3/LICENSE.txt"), "bridge-source/licenses/renpy/LICENSE.txt", "runtime_license")
    host_root = child(home, "runtimes/html-minigame-embedded-host/win-x64")
    host_build_path = child(host_root, "build-report.json")
    host_build = read_json(host_build_path)
    native_source = validate_native_source(host_build, child(source_root, "native/html_minigame_embedded_host"))
    host_inventory = configuration["host"]["files"]
    for name, record in host_inventory.items():
        _verify_file(child(host_root, name), record)
        add(child(host_root, name), "bridge-source/runtimes/html-minigame-embedded-host/win-x64/" + name, "embedded_host_runtime")
    for record in host_build["build"]["files"]:
        _verify_file(child(host_root, record["path"]), record)
        if host_inventory.get(record["path"]) != {key: record[key] for key in ("sha256", "bytes")}:
            raise DeliveryError("Actual native build output differs from tested host inventory")
    add(host_build_path, "metadata/embedded-host-build-report.json", "native_build_lock")
    fixed = configuration.get("fixed_runtime", {})
    version = fixed.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9.]+", version):
        raise DeliveryError("Fixed browser version is missing")
    browser_lock_path = child(home, "runtimes/webview2-fixed-" + version + "/runtime-lock.json")
    browser_lock = read_json(browser_lock_path)
    browser_root = child(home, browser_lock.get("relative_runtime_path"))
    if (browser_lock.get("version") != version or browser_lock.get("signature_status") != "Valid"
            or "Microsoft Corporation" not in str(browser_lock.get("signer", ""))
            or file_hash(child(browser_root, "msedgewebview2.exe")) != browser_lock.get("exe_sha256")):
        raise DeliveryError("Fixed browser hash/signature lock differs")
    for name, record in fixed.get("files", {}).items():
        _verify_file(child(browser_root, name), record)
    add(browser_lock_path, "metadata/fixed-browser-signature-lock.json", "fixed_browser_signature_lock")
    # Preserve notices at their runtime-relative path. The unchanged player ZIP
    # also contains the complete fixed browser; it is not downloaded here.
    for path in source_files(browser_root):
        if re.search(r"license|licence|notice", path.name, re.IGNORECASE):
            add(path, "bridge-source/" + path.relative_to(home).as_posix(), "fixed_browser_license")
    dependencies = child(home, "runtimes/html-host-dependencies")
    dependency_files = [path for path in source_files(dependencies) if
        re.search(r"license|licence|notice", path.name, re.IGNORECASE) or path.suffix in {".nuspec", ".nupkg", ".sha512"}]
    if not any(path.name == "LICENSE.txt" and "microsoft.web.webview2" in path.parts for path in dependency_files):
        raise DeliveryError("WebView2 SDK dependency license is missing")
    for path in dependency_files:
        add(path, "bridge-source/" + path.relative_to(home).as_posix(), "native_dependency_license_or_lock")
    python_licenses = collect_python_licenses(source_root, add)
    add(report_path, "evidence/final-html-build-report.json", "final_build_report")
    add(package, "release/" + package.name, "final_release_zip", release["sha256"])
    for path, expected, _ in members:
        add(path, "release/independent/" + independent.name + "/" + path.relative_to(independent).as_posix(), "independent_release", expected)
    visited = set()

    def evidence_refs(value):
        if isinstance(value, dict):
            if value is staged_provenance:
                return  # The exact verified final ZIP is already preserved.
            pairs = [(value.get("path"), value.get("sha256"))]
            pairs += [(value.get(key), value.get(key + "_sha256")) for key in ("browser_report", "event_log", "report_path")]
            for name, expected in pairs:
                if not isinstance(name, str) or not Path(name).is_absolute() or not isinstance(expected, str):
                    continue
                path = unlinked(name)
                if path in visited:
                    continue
                if not any(path.is_relative_to(root) for root in (home / "jobs", source_root / "evidence", project / "generated", project / "releases")):
                    raise DeliveryError("Pinned build evidence leaves selected project/job roots")
                visited.add(path)
                add(path, "evidence/referenced/" + expected[:24] + "/" + path.name, "pinned_runtime_evidence", expected)
                if path.suffix == ".json":
                    evidence_refs(read_json(path))
            for nested in value.values():
                evidence_refs(nested)
        elif isinstance(value, list):
            for nested in value:
                evidence_refs(nested)
    evidence_refs(report)
    index = read_json(child(home, "projects/index.json"))
    if project_id not in index.get("projects", {}):
        raise DeliveryError("Selected project is absent from the index")
    selected = {"projects": {project_id: index["projects"][project_id]}, "operations": {key: value for key, value in index.get("operations", {}).items() if value.get("result", {}).get("project_id") == project_id}}
    return {"home": home, "source_root": source_root, "project": project, "project_id": project_id,
        "revision": revision, "model_hash": digest(model), "code_digest": code_digest, "core_hashes": hashes,
        "head_path": head_path, "head_sha256": file_hash(head_path), "report": report, "output": output,
        "items": sorted(items, key=lambda item: item.destination), "source_scans": scans, "games": sorted(games),
        "selected_index": selected, "native_source": native_source, "python_licenses": python_licenses,
        "browser_lock": browser_lock, "sdk_lock": sdk_lock}


def _verify_source_zip(path, records):
    expected = {record["path"][len("bridge-source/"):]: record for record in records if record["path"].startswith("bridge-source/")}
    with zipfile.ZipFile(path) as archive:
        if set(archive.namelist()) != set(expected) or len(archive.infolist()) != len(expected):
            raise DeliveryError("Source ZIP inventory differs from delivered source")
        for name, record in expected.items():
            with archive.open(name) as stream:
                import hashlib
                if archive.getinfo(name).file_size != record["bytes"] or hashlib.file_digest(stream, "sha256").hexdigest() != record["sha256"]:
                    raise DeliveryError("Source ZIP member differs from preserved source")


def package_html_delivery(home, project_id, build_report, output, source_root=ROOT, *, dry_run=False):
    plan = prepare_plan(home, project_id, build_report, output, source_root)
    if dry_run:
        return {"status": "dry_run", "schema_version": "1.1", "output": str(plan["output"]), "project_id": project_id,
            "revision": plan["revision"], "files": len(plan["items"]), "copy_bytes": sum(item.bytes for item in plan["items"]), "code_digest": plan["code_digest"]}
    output = plan["output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.with_name("." + output.name + ".stage-" + uuid.uuid4().hex)
    stage.mkdir(exist_ok=False)
    try:
        files = [legacy.copy_verified(item, stage) for item in plan["items"]]
        metadata = {"managed-home/projects/index.json": plan["selected_index"],
            "metadata/python-dependency-license-index.json": plan["python_licenses"],
            "RESTORATION.md": f"""# Same-window HTML extension source snapshot

Core reports its existing version; this delivery is a schema1.1 extension
snapshot, not evidence that an installed personal plugin was upgraded.

Project `{project_id}`, revision {plan['revision']}, and its historical immutable
HTML packages are in `managed-home/projects/`. Restore into a new workspace and
merge only this selected project's index. Do not overwrite another project.
The final player ZIP and its exact original members are in `release/`; copy the
whole player directory when running it. Its embedded host and fixed browser
require no Python/MCP/SDK for gameplay.

`bridge-source/` and its source ZIP preserve current source, tests, docs, skills,
tools, all formal example versions, embedded C# source/locked project, compiled
host and native dependency licenses/locks. `metadata/` preserves actual native
build/source binding, fixed-browser signature lock, RenPy SDK lock and Python
dependency license index. Native source SHA and the browser signature are
historical verified records, not a fresh compilation or signature operation.

For authoring, provide Python and the exact `requirements.lock` dependencies.
Create a new workspace venv, restore managed project files and the SDK version
in `metadata/sdk-lock.json`. Rebuilding C# requires the pinned official .NET SDK
and the NuGet versions/content hashes in `packages.lock.json`; use the preserved
`tools/build_html_embedded_host.py`, with network preparation only when needed.
The .NET SDK and RenPy SDK binaries are not duplicated in this source snapshot.
To restore the exact fixed browser, copy its complete `html-runtime/webview2-fixed`
tree from the unchanged player package to the path in its signature lock; verify
the complete tested inventory before authoring/generation. Python venv binaries,
browser profiles, private saves and temporary developer state are not bundled.

Read `evidence/final-html-build-report.json` for each actual scenario. Outcome,
cancel and owned-host-failure coverage does not prove every predicate branch,
combined route, old player save or physical DPI/device matrix. This archiving
operation adds no runtime, audio-device or gameplay acceptance.

Every delivered file except `manifest.json` is SHA256 pinned in that manifest.
Original absolute report paths are provenance; `original_path` in the inventory
maps them to archive-relative copies. On copy races, partial staging is kept
with `FAILED.json` and never published as a complete output.

The report's removed staging ZIP path is historical provenance only. Its exact
SHA256 and byte length must match the preserved final player ZIP; no temporary
ZIP is recovered or copied. Other missing raw evidence references still reject
the archive.
"""}
        for name, value in metadata.items():
            record = legacy.write_new(child(stage, name), value)
            record["path"] = name
            files.append(record)
        source_zip = child(stage, "html-source-schema1.1-" + plan["code_digest"][:12] + ".zip")
        with zipfile.ZipFile(source_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for record in files:
                if record["path"].startswith("bridge-source/"):
                    archive.write(child(stage, record["path"]), record["path"][len("bridge-source/"):])
        _verify_source_zip(source_zip, files)
        files.append({"path": source_zip.name, **_file_record(source_zip), "category": "recoverable_html_source_zip"})
        for item in plan["items"]:
            if _file_record(item.source) != {"sha256": item.sha256, "bytes": item.bytes}:
                raise DeliveryError("Archive input changed after copy")
        for root, names in plan["source_scans"].items():
            if [path.relative_to(root).as_posix() for path in source_files(root)] != names:
                raise DeliveryError("Source inventory changed during archive")
        if (legacy.core_hashes(plan["home"])[0] != plan["core_hashes"] or file_hash(plan["head_path"]) != plan["head_sha256"]
                or sorted(_managed_games(plan["project"])) != plan["games"]):
            raise DeliveryError("Current source/head/immutable package set changed during archive")
        manifest = {"schema_version": "1.1", "extension": "same_window_html_minigames", "status": "complete",
            "created_at": datetime.now(timezone.utc).isoformat(), "archive_is_runtime_acceptance": False,
            "project_id": project_id, "revision": plan["revision"], "model_hash": plan["model_hash"],
            "pipeline_code_digest": plan["code_digest"], "core_files": plan["core_hashes"], "native_source": plan["native_source"],
            "fixed_browser_signature_lock": plan["browser_lock"], "sdk_lock": plan["sdk_lock"], "release_report": plan["report"]["release"],
            "scope": "byte-preserved tested same-window release and recoverable source; no new runtime acceptance",
            "combination_routes_verified": False, "physical_dpi_matrix_verified": False, "old_player_save_verified": False,
            "file_hash_scope": "Every delivered file except manifest.json", "files": sorted(files, key=lambda record: record["path"])}
        legacy.write_new(stage / "manifest.json", manifest)
        if output.exists():
            raise DeliveryError("Output appeared during archive; refusing overwrite")
        stage.rename(output)
        return {"status": "complete", "output": str(output), "schema_version": "1.1", "project_id": project_id,
                "revision": plan["revision"], "file_count": len(files), "manifest_sha256": file_hash(output / "manifest.json")}
    except Exception as error:
        if stage.exists() and not (stage / "FAILED.json").exists():
            legacy.write_new(stage / "FAILED.json", {"status": "incomplete", "error": f"{type(error).__name__}: {error}",
                "policy": "Partial staging retained; no complete output was published."})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-home", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = package_html_delivery(args.bridge_home, args.project_id, args.build_report, args.output, dry_run=args.dry_run)
    except Exception as error:
        print(f"HTML_DELIVERY_REJECTED: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
