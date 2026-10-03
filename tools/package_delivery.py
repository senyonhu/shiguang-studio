"""Read-only, hash-checked delivery archive for one final managed project.

This copies an already tested release. It does not run Ren'Py, install anything,
modify a project, remove QA, or turn an archive into runtime acceptance evidence.
Existing outputs are never overwritten or deleted, including failed attempts.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.1.3"
SOURCE_SCRIPT_FILES = (
    "scripts/environment_probe.py", "scripts/install_local.py", "scripts/mcp_probe.py",
    "scripts/plugin_prepare.py", "scripts/progress.py", "scripts/record_evidence.py",
    "scripts/sync_evidence.py", "tools/capture_project_state.py", "tools/codex_author.py",
    "tools/create_demo_audio.py", "tools/create_demo_workbook.mjs", "tools/package_delivery.py",
    "tools/probe_audio.py", "tools/probe_codex.py", "tools/probe_core_integrity.py",
    "tools/probe_demo_audio.py", "tools/probe_diagnostics_jobs.py", "tools/probe_engine_audio.py",
    "tools/probe_generator.py", "tools/probe_install_recovery.py", "tools/probe_installed_plugin.py",
    "tools/probe_interactions.py", "tools/probe_old_save.py", "tools/probe_pipeline.py",
    "tools/probe_plugin_host.py", "tools/probe_plugin_uninstall.py", "tools/probe_scale.py",
    "tools/probe_sdk.py", "tools/probe_tool_contract.py", "tools/probe_voice.ps1",
    "tools/record_demo_visual_review.py", "tools/update_demo_workbook.mjs",
    "tools/verify_demo_authoring.py",
)
SOURCE_FILES = (".gitignore", ".gitattributes", "pyproject.toml", "requirements.lock", "START_HERE.md", *SOURCE_SCRIPT_FILES)
SOURCE_TEST_FIXTURES = {
    "evidence/m0-audio/runs/20261001T211735Z-fa0b6f4a/chinese-test-voice.wav":
        "ca3488ffc5edbf6a66db528252fe27d84e538b82a098a0f087f0e71687f30c47",
}
SOURCE_TREES = {"src/renpy_bridge": {".py"}, "docs": {".md", ".json"},
                "tests": {".py", ".json", ".md", ".yaml", ".yml"}}
PLUGIN_EXTENSIONS = {".json", ".md", ".png", ".svg"}
PROJECT_TREES = {"model", "history", "assets", "source_inputs", "imports",
                 "checkpoints", "jobs", "generated", "visual_baselines"}
PRIVATE_NAMES = {"saves", "private-state", "private-saves", "package-saves",
                 "multipersistent", "__pycache__", "cache", ".cache", ".renpy"}
RUNTIME_FILES = {"log.txt", "traceback.txt", "errors.txt"}
EVIDENCE_COMPANIONS = {"summary.json", "version-review.json", "visual-review.json",
                       "tests.xml", "raw-events.jsonl", "prompt.txt",
                       "last-message.txt", "stdout.txt", "stderr.txt"}
SCREENSHOT_COLLECTIONS = {"screenshots", "package_screenshots", "rendered_views"}


class DeliveryError(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise DeliveryError(f"Expected a JSON object: {path}")
    return value


def unlinked(path):
    """Reject symlinks and Windows reparse points before resolving a path."""
    absolute = Path(os.path.abspath(Path(path).expanduser()))
    for item in (absolute, *absolute.parents):
        if item.is_symlink() or (item.exists() and
                getattr(item.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise DeliveryError(f"Links/reparse points are not archive inputs or outputs: {item}")
    return absolute.resolve()


def relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise DeliveryError(f"Expected a portable relative path: {value!r}")
    parts = value.split("/")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise DeliveryError(f"Absolute path is not allowed: {value!r}")
    for part in parts:
        if part in {"", ".", ".."} or part[-1:] in {" ", "."} or any(
                ord(c) < 32 or c in '<>"|?*' for c in part):
            raise DeliveryError(f"Invalid relative path: {value!r}")
        if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", part.split(".")[0], re.I):
            raise DeliveryError(f"Windows device name is not allowed: {value!r}")
    return PurePosixPath(value)


def bounded(root, path):
    root, candidate = unlinked(root), unlinked(path)
    if not candidate.is_relative_to(root):
        raise DeliveryError(f"Input leaves its allowed root: {path}")
    return candidate


def child(root, value):
    return bounded(root, Path(root).joinpath(*relative(value).parts))


def private_path(path):
    return any(part in PRIVATE_NAMES or part.startswith(".rb-sdk-state-") for part in path.parts) or path.suffix.lower() in {".lock", ".tmp"} or path.name in RUNTIME_FILES


def tree_files(root, *, suffixes=None, exclude_private=False):
    root = unlinked(root)
    if not root.is_dir():
        raise DeliveryError(f"Required directory missing: {root}")
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*dirs, *names]:
            bounded(root, base / name)
        dirs[:] = [name for name in dirs if name != "__pycache__" and
                   (not exclude_private or not private_path((base / name).relative_to(root)))]
        for name in names:
            path = base / name
            rel = path.relative_to(root)
            if exclude_private and private_path(rel):
                continue
            if suffixes is not None and path.suffix.lower() not in suffixes:
                continue
            if not path.is_file():
                raise DeliveryError(f"Expected a regular file: {path}")
            files.append(path)
    return sorted(files)


@dataclass(frozen=True)
class CopyItem:
    source: Path
    destination: str
    sha256: str
    bytes: int
    category: str


def literal_assignment(path, name):
    for node in ast.parse(Path(path).read_text(encoding="utf-8-sig")).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise DeliveryError(f"Missing literal version {name}: {path}")


def core_hashes(root):
    files = tree_files(Path(root) / "src/renpy_bridge", suffixes={".py"})
    by_relative = {p.relative_to(root).as_posix(): file_hash(p) for p in files}
    by_name = {p.name: by_relative[p.relative_to(root).as_posix()] for p in files}
    if len(by_name) != len(files):
        raise DeliveryError("Core contains duplicate basenames; pipeline code_digest is ambiguous")
    return by_relative, digest(by_name)


def validate_visual_baselines(project, project_id):
    """Verify reviewed pointer/image integrity without claiming a visual pass."""
    base = child(project, "visual_baselines")
    if not base.exists():
        return
    tree_files(base)  # Reject all links, including unindexed immutable versions.
    index_path = child(base, "index.json")
    if not index_path.exists():
        return  # Interrupted, uncommitted runs are preserved as recovery data.
    index = read_json(index_path)
    if (index.get("schema_version") != "1.0" or index.get("project_id") != project_id
            or any(not isinstance(index.get(key), dict) for key in ("baselines", "active_frames", "operations"))):
        raise DeliveryError("Visual baseline index identity/schema is invalid")
    frames_by_id = {}
    for baseline_id, entry in index["baselines"].items():
        if not re.fullmatch(r"visual_[0-9a-f]{24}", baseline_id) or not isinstance(entry, dict):
            raise DeliveryError("Visual baseline pointer is invalid")
        expected_path = f"runs/{baseline_id}/manifest.json"
        if entry.get("manifest_path") != expected_path:
            raise DeliveryError("Visual baseline manifest pointer leaves its immutable run")
        manifest_path = child(base, expected_path)
        if not manifest_path.is_file() or file_hash(manifest_path) != entry.get("sha256"):
            raise DeliveryError("Visual baseline manifest is missing or has changed")
        manifest = read_json(manifest_path)
        frames = manifest.get("frames")
        if (manifest.get("schema_version") != "1.0" or manifest.get("project_id") != project_id
                or manifest.get("baseline_id") != baseline_id or not isinstance(frames, dict)):
            raise DeliveryError("Visual baseline manifest identity/schema is invalid")
        image_root = child(base, f"runs/{baseline_id}/images")
        for frame_id, image in frames.items():
            if not isinstance(image, dict):
                raise DeliveryError("Visual baseline frame entry is invalid")
            # Frame IDs are JSON keys, never interpreted as Windows filenames.
            path = child(base, image.get("image_path"))
            if not path.is_relative_to(image_root) or not path.is_file() or file_hash(path) != image.get("sha256"):
                raise DeliveryError(f"Visual baseline image cannot be restored: {frame_id}")
        frames_by_id[baseline_id] = frames
    for frame_id, baseline_id in index["active_frames"].items():
        if not isinstance(baseline_id, str) or frame_id not in frames_by_id.get(baseline_id, {}):
            raise DeliveryError("Visual baseline active pointer is invalid")


def package_members(package, independent, expected_entries):
    """Use the actual ZIP member list as the independent-copy allowlist."""
    result, seen = [], set()
    with zipfile.ZipFile(package) as archive:
        infos = archive.infolist()
        if len(infos) != expected_entries:
            raise DeliveryError("Release ZIP entry count differs from report")
        for info in infos:
            name = info.filename.rstrip("/")
            path = relative(name)
            key = path.as_posix().casefold()
            if key in seen or stat.S_ISLNK(info.external_attr >> 16):
                raise DeliveryError("ZIP contains duplicate/case-colliding/link members")
            seen.add(key)
            if not path.parts or path.parts[0] != independent.name:
                raise DeliveryError("ZIP root does not match reported independent directory")
            if info.is_dir():
                continue
            if len(path.parts) < 2:
                raise DeliveryError("ZIP contains a root-level file outside its game directory")
            rel = PurePosixPath(*path.parts[1:]).as_posix()
            source = child(independent, rel)
            if not source.is_file():
                raise DeliveryError(f"Extracted package member missing: {source}")
            with archive.open(info) as stream:
                expected_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if file_hash(source) != expected_hash or source.stat().st_size != info.file_size:
                raise DeliveryError(f"Extracted release member differs from ZIP: {rel}")
            result.append((source, expected_hash, info.file_size))
    return result


def prepare_plan(home, project_id, build_report, output, source_root=ROOT):
    home, source_root, output = unlinked(home), unlinked(source_root), unlinked(output)
    if output.exists():
        raise DeliveryError(f"Output already exists; choose a new directory: {output}")
    if output.is_relative_to(home) or output == source_root or any(
            output.is_relative_to(source_root / name) for name in ("src", "plugin", "docs", "tests", "scripts", "tools", "evidence", "runtimes")):
        raise DeliveryError("Output must be outside production home and archive input trees")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", project_id):
        raise DeliveryError("Invalid project ID")
    project = child(home, "projects/" + project_id)
    report_path = unlinked(build_report)
    if not (report_path.is_relative_to(home / "jobs") or report_path.is_relative_to(source_root / "evidence")):
        raise DeliveryError("Build report must be in managed jobs or workspace evidence")
    report = read_json(report_path)
    if report.get("kind") != "build" or report.get("ok") is not True or report.get("status") != "passed" or report.get("independent_release_verified") is not True:
        raise DeliveryError("Expected a passed actual pipeline build report with independent release verification")
    root_hashes, code_digest = core_hashes(source_root)
    installed_hashes, installed_digest = core_hashes(home)
    if installed_hashes != root_hashes or installed_digest != code_digest or report.get("code_digest") != code_digest:
        raise DeliveryError("Production/root/build core source hashes do not match")
    versions = [literal_assignment(source_root / "src/renpy_bridge/__init__.py", "__version__"),
                literal_assignment(source_root / "src/renpy_bridge/generator.py", "GENERATOR_VERSION"),
                read_json(home / "plugin/renpy-bridge/.codex-plugin/plugin.json").get("version"),
                tomllib.loads((source_root / "pyproject.toml").read_text(encoding="utf-8-sig"))["project"]["version"],
                report.get("generator_version")]
    if any(value != RELEASE_VERSION for value in versions):
        raise DeliveryError(f"All source/plugin/build versions must be {RELEASE_VERSION}: {versions}")
    head_path = child(project, "model/head.json")
    head = read_json(head_path)
    model_path = child(project, head.get("model_path"))
    model = read_json(model_path)
    revision = head.get("revision")
    if type(revision) is not int or revision < 0 or head.get("schema_version") != "1.0" or model.get("schema_version") != "1.0":
        raise DeliveryError("Current managed schema/revision is invalid")
    if any(value != project_id for value in [model.get("project_id"), report.get("project_id")]) or model.get("revision") != revision or report.get("revision") != revision or report.get("model_hash") != digest(model):
        raise DeliveryError("Build report does not describe the current committed model")
    if head.get("runnable_revision") != revision:
        raise DeliveryError("Current content revision is not marked runnable")
    baseline = child(project, head.get("runnable_project_path"))
    if not baseline.is_dir() or not baseline.is_relative_to(project / "generated"):
        raise DeliveryError("Runnable generated baseline is missing or outside generated")
    generated = bounded(project / "generated", Path(report.get("generation", {}).get("project_path", "")))
    if not generated.is_dir():
        raise DeliveryError("Build generated directory is missing")
    sdk_lock_path = child(home, "runtimes/sdk-lock.json")
    sdk_lock = read_json(sdk_lock_path)
    if sdk_lock.get("version") != report.get("sdk_version") or not re.fullmatch(r"[0-9a-f]{64}", str(sdk_lock.get("sha256", ""))):
        raise DeliveryError("Build SDK version/hash lock is missing or inconsistent")
    release = report.get("release")
    if not isinstance(release, dict):
        raise DeliveryError("Build report has no release object")
    package = bounded(project / "releases", Path(release.get("path", "")))
    independent = bounded(project / "releases", Path(release.get("independent_directory", "")))
    exe = bounded(independent, Path(release.get("executable", "")))
    if package.suffix.lower() != ".zip" or not package.is_file() or not exe.is_file() or exe.suffix.lower() != ".exe":
        raise DeliveryError("Final ZIP/executable is missing")
    if file_hash(package) != release.get("sha256") or package.stat().st_size != release.get("bytes") or file_hash(exe) != release.get("executable_sha256"):
        raise DeliveryError("Final ZIP/executable hash or size differs from build report")
    if type(release.get("entries")) is not int:
        raise DeliveryError("Build report is missing ZIP entries")
    members = package_members(package, independent, release["entries"])
    items, destinations = [], {}

    def add(path, destination, category, expected=None):
        path = unlinked(path)
        relative(destination)
        key = destination.casefold()
        if not path.is_file():
            raise DeliveryError(f"Required archive input missing: {path}")
        hash_value = file_hash(path)
        if expected is not None and hash_value != expected:
            raise DeliveryError(f"Evidence/content hash differs from its record: {path}")
        item = CopyItem(path, destination, hash_value, path.stat().st_size, category)
        if key in destinations:
            old = destinations[key]
            if (old.sha256, old.bytes) != (item.sha256, item.bytes):
                raise DeliveryError(f"Archive destination collision: {destination}")
            return
        destinations[key] = item
        items.append(item)

    for name in SOURCE_FILES:
        add(child(source_root, name), "bridge-source/" + name, "bridge_source")
    for name, expected in SOURCE_TEST_FIXTURES.items():
        add(child(source_root, name), "bridge-source/" + name, "bridge_source_test_fixture", expected)
    for name, suffixes in SOURCE_TREES.items():
        for path in tree_files(child(source_root, name), suffixes=suffixes):
            add(path, "bridge-source/" + path.relative_to(source_root).as_posix(), "bridge_source")
    for path in tree_files(child(home, "plugin"), suffixes=PLUGIN_EXTENSIONS):
        add(path, "bridge-source/" + path.relative_to(home).as_posix(), "personal_plugin")
    for name in ("plugin/renpy-bridge/.mcp.json", "plugin/.agents/plugins/marketplace.json"):
        if "bridge-source/" + name not in {x.destination for x in items}:
            raise DeliveryError(f"Required personal plugin configuration missing: {name}")

    # Verify all committed history assets, not just the final model. Checkpoint
    # restoration may need media retired long before the current head.
    for path in tree_files(child(project, "history/revisions"), suffixes={".json"}):
        previous = read_json(path)
        for asset in previous.get("assets", {}).values():
            media = child(project, asset.get("path"))
            if not media.is_relative_to(project / "assets") or not media.is_file() or file_hash(media) != asset.get("sha256"):
                raise DeliveryError(f"Historical managed media cannot be restored: {media}")
    for path in child(project, "checkpoints").glob("snap_*.json"):
        checkpoint = read_json(path)
        old_revision = checkpoint.get("revision")
        if type(old_revision) is not int or not 0 <= old_revision <= revision:
            raise DeliveryError(f"Invalid checkpoint revision: {path}")
        previous = read_json(child(project, f"history/revisions/{old_revision:08d}.json"))
        if digest(previous) != checkpoint.get("model_hash"):
            raise DeliveryError(f"Checkpoint model hash differs: {path}")
    validate_visual_baselines(project, project_id)
    for name in PROJECT_TREES:
        tree = child(project, name)
        if tree.exists():
            for path in tree_files(tree, exclude_private=True):
                add(path, "managed-home/projects/" + project_id + "/" + path.relative_to(project).as_posix(), "managed_project")
    add(sdk_lock_path, "metadata/sdk-lock.json", "sdk_lock")
    add(report_path, "evidence/final-build-report.json", "final_build_report")
    add(package, "release/" + package.name, "final_release_zip", release["sha256"])
    for path, expected_hash, _ in members:
        add(path, "release/independent/" + independent.name + "/" + path.relative_to(independent).as_posix(), "independent_release", expected_hash)

    evidence_index = read_json(source_root / "docs/EVIDENCE.json")
    report_objects = [report]
    for record in evidence_index.get("implementation_runs", []):
        name = record.get("report_path", "").replace("\\", "/")
        path = child(source_root, name)
        if not path.is_relative_to(source_root / "evidence"):
            raise DeliveryError("Indexed evidence is outside the workspace evidence root")
        add(path, "bridge-source/" + name, "indexed_evidence", record.get("report_sha256"))
        for companion_name in EVIDENCE_COMPANIONS:
            companion = child(source_root, path.parent.relative_to(source_root).as_posix() + "/" + companion_name)
            if companion.is_file():
                add(companion, "bridge-source/" + companion.relative_to(source_root).as_posix(), "indexed_evidence_companion")
        if path.suffix.lower() == ".json":
            report_objects.append(read_json(path))

    def managed_artifact(path, root):
        if not path.is_relative_to(root):
            return False
        parts = path.relative_to(root).parts
        return len(parts) >= 4 and parts[0] == "projects" and bool(
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", parts[1])) and parts[2] in {"generated", "releases"} and not private_path(Path(*parts[3:]))

    def screenshots(value, collection=False):
        if isinstance(value, dict):
            name, expected = value.get("path"), value.get("sha256")
            if collection and isinstance(name, str) and Path(name).suffix.lower() == ".png" and isinstance(expected, str):
                if not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise DeliveryError("Screenshot has an invalid recorded SHA256")
                path = unlinked(name)
                if not (path.is_relative_to(source_root / "evidence") or path.is_relative_to(source_root / "outputs/windows-demo") or managed_artifact(path, source_root) or managed_artifact(path, home) or path.is_relative_to(home / "jobs")):
                    raise DeliveryError(f"Screenshot leaves evidence/project job roots: {name}")
                add(path, "evidence/key-screenshots/" + expected[:24] + "/" + path.name, "key_screenshot", expected)
            for key, child_value in value.items():
                screenshots(child_value, collection or key in SCREENSHOT_COLLECTIONS)
        elif isinstance(value, list):
            for child_value in value:
                screenshots(child_value, collection)
    for value in report_objects:
        screenshots(value)
    original_index = read_json(child(home, "projects/index.json"))
    if project_id not in original_index.get("projects", {}):
        raise DeliveryError("Selected project is missing from managed project index")
    selected_index = {"projects": {project_id: original_index["projects"][project_id]},
                      "operations": {k: v for k, v in original_index.get("operations", {}).items()
                                     if v.get("result", {}).get("project_id") == project_id}}
    return {"home": home, "source_root": source_root, "output": output, "project_id": project_id,
            "revision": revision, "model_hash": digest(model), "code_digest": code_digest,
            "core_hashes": root_hashes, "head_path": head_path, "head_sha256": file_hash(head_path),
            "sdk_lock": sdk_lock, "report": report, "selected_index": selected_index,
            "items": sorted(items, key=lambda item: item.destination),
            "excluded_project_state": sorted(PRIVATE_NAMES | RUNTIME_FILES) + ["*.lock", "*.tmp", ".rb-sdk-state-*", "historical releases (final ZIP and exact ZIP-listed independent files copied separately)"]}


def copy_verified(item, output):
    if file_hash(item.source) != item.sha256:
        raise DeliveryError(f"Input changed before copy: {item.source}")
    destination = child(output, item.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    unlinked(destination)
    with item.source.open("rb") as source, destination.open("xb") as target:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            target.write(chunk)
    if destination.stat().st_size != item.bytes or file_hash(destination) != item.sha256 or file_hash(item.source) != item.sha256:
        raise DeliveryError(f"Input changed during copy or copied bytes differ: {item.source}")
    return {"path": item.destination, "sha256": item.sha256, "bytes": item.bytes,
            "category": item.category, "original_path": str(item.source)}


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    unlinked(path)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode() if not isinstance(value, str) else value.encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)
    return {"path": None, "sha256": file_hash(path), "bytes": len(data), "category": "generated_archive_metadata"}


def package_delivery(home, project_id, build_report, output, source_root=ROOT, *, dry_run=False):
    plan = prepare_plan(home, project_id, build_report, output, source_root)
    if dry_run:
        return {"status": "dry_run", "output": str(plan["output"]), "project_id": project_id,
                "revision": plan["revision"], "files": len(plan["items"]),
                "copy_bytes": sum(item.bytes for item in plan["items"]), "code_digest": plan["code_digest"]}
    output = plan["output"]
    output.mkdir(parents=True, exist_ok=False)
    try:
        files = [copy_verified(item, output) for item in plan["items"]]
        metadata = {
            "managed-home/projects/index.json": plan["selected_index"],
            "RESTORATION.md": f"""# RenPy Bridge {RELEASE_VERSION} delivery restoration

This archive copies an existing tested build. Archiving is not native, visual,
audio, desktop-host, or full-route acceptance. Consult `evidence/final-build-report.json`
and `bridge-source/docs/EVIDENCE.json` for actual result scopes.

Relative archive paths:

- `bridge-source/`: installed personal plugin and matching core; current source
  docs/tests, dependency lock, all fixed listed `scripts/` and `tools/` source files,
  and the SHA-pinned Chinese voice fixture required by `tests/test_media.py`.
  Personal `.mcp.json`
  preserves the original installation path as evidence; reinstall to regenerate it.
- `managed-home/projects/{project_id}/`: byte-preserved content, media versions,
  original source snapshots, history/checkpoints/import candidates and generated
  compatibility baselines and immutable reviewed `visual_baselines/` runs/index.
  Their image/manifest pointers remain project-relative after relocation.
  `managed-home/projects/index.json` selects only this project.
- `release/`: unchanged final ZIP and its exact ZIP-listed independent game files.
  Run the copied package's reported `.exe` without MCP, Python or the SDK.
- `metadata/sdk-lock.json`: pinned SDK metadata; the SDK and Python/venv are not bundled.
- `evidence/key-screenshots/`: copied hashed screenshot evidence. Manifest `original_path`
  maps preserved absolute report references to their new relative archive paths.

For authoring restoration, install Python 3.12, then run from `bridge-source/`:
`python scripts/install_local.py --source . --destination <new-personal-home> --register`.
The installer builds its venv and obtains the official pinned Ren'Py SDK
{plan['sdk_lock'].get('version')}, archive SHA256 `{plan['sdk_lock'].get('sha256')}`.
Use a verified local SDK ZIP with `--sdk-source` for offline SDK installation;
the locked Python dependencies also need to be available to pip.
For the full source regression suite, separately provide the locked official SDK
at `bridge-source/runtimes/renpy-8.5.3/` (in particular its original
`tutorial/game/tower_clock.ogg` and `launcher/game/fonts/Roboto-Regular.ttf`)
and install FFmpeg on PATH. Run the source tests with the locked Python
dependencies after rebuilding the venv. SDK, Python/venv and FFmpeg are not in
this archive. Material preparation `.mjs` scripts also require Node.js and their
external libraries; their source is preserved, their dependency directories are
not bundled.
Copy the selected project from `managed-home/projects/` into the newly installed
home's `projects/`, and merge its selected index entries when other projects exist.
Do not overwrite other projects or their index. Open project `{project_id}`, revision
{plan['revision']}, through the public API. Managed media/model and runnable baselines
use relative paths preserved here. Original source paths and historical call/report
paths remain provenance from the old machine; they are not media loading dependencies.
Source extraction from a relocated original document must use its new local path.
No private test saves, SDK state, environment, locks or other personal projects are copied.

Verify all file SHA256 values in `manifest.json` before restoration. The manifest
excludes itself to avoid a self-referential hash. A failed partial output is kept
with `FAILED.json`; choose a fresh output instead of resuming or overwriting it.
"""}
        for name, value in metadata.items():
            record = write_new(child(output, name), value)
            record["path"] = name
            files.append(record)
        source_zip = child(output, "renpy-bridge-source-" + RELEASE_VERSION + ".zip")
        with zipfile.ZipFile(source_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for record in files:
                if record["path"].startswith("bridge-source/"):
                    archive.write(child(output, record["path"]), "renpy-bridge-source-" + RELEASE_VERSION + "/" + record["path"][len("bridge-source/"):])
        files.append({"path": source_zip.name, "sha256": file_hash(source_zip), "bytes": source_zip.stat().st_size, "category": "bridge_source_zip"})
        if core_hashes(plan["home"])[0] != plan["core_hashes"] or core_hashes(plan["source_root"])[0] != plan["core_hashes"] or file_hash(plan["head_path"]) != plan["head_sha256"]:
            raise DeliveryError("Production source or current project head changed during archive")
        for item in plan["items"]:
            if file_hash(item.source) != item.sha256:
                raise DeliveryError(f"Archive input changed after its copy: {item.source}")
        manifest = {"schema_version": "1.0", "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
                    "archive_is_runtime_acceptance": False, "bridge_version": RELEASE_VERSION,
                    "project_id": project_id, "revision": plan["revision"], "model_hash": plan["model_hash"],
                    "pipeline_code_digest": plan["code_digest"], "core_files": plan["core_hashes"],
                    "sdk_lock": plan["sdk_lock"], "release_report": plan["report"]["release"],
                    "native_acceptance_preserved_from_report": {key: plan["report"].get(key) for key in ["independent_release_verified", "visual_review", "audio_output_verification", "interaction_verification"]},
                    "excluded_project_state": plan["excluded_project_state"],
                    "file_hash_scope": "Every delivered file except manifest.json itself", "files": sorted(files, key=lambda record: record["path"])}
        write_new(output / "manifest.json", manifest)
        return {"status": "complete", "output": str(output), "manifest_sha256": file_hash(output / "manifest.json"),
                "project_id": project_id, "revision": plan["revision"], "file_count": len(files)}
    except Exception as error:
        failure = output / "FAILED.json"
        if not failure.exists():
            write_new(failure, {"status": "incomplete", "error": f"{type(error).__name__}: {error}",
                                "policy": "Partial output retained; no overwrite/delete. Use a fresh output."})
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
        result = package_delivery(args.bridge_home, args.project_id, args.build_report, args.output, dry_run=args.dry_run)
    except (DeliveryError, OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"DELIVERY_REJECTED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
