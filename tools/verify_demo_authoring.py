"""Read-only acceptance of a real installed-plugin Demo author turn.

The source XLSX, Codex JSONL events, committed version JSON, managed asset bytes,
and persisted worker reports are independent inputs. No Store/core functions are
imported or called. Each invocation creates a new evidence directory; it never
changes the game, source materials, upstream jobs, or prior verification runs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
JOB_TOOLS = {"check_project": "check", "render_preview": "preview", "test_routes": "routes", "build_release": "build"}
REQUIRED_TOOLS = {"get_environment", "create_project", "extract_source", "register_assets", "begin_import",
                  "stage_import_batch", "commit_import", "apply_layout", "check_project", "render_preview", "test_routes", "get_job"}
PUBLIC_TOOLS = REQUIRED_TOOLS | {"inspect_project", "apply_frame_batch", "patch_frame", "update_flow",
                                "discard_import", "plan_reimport", "apply_reimport", "create_checkpoint", "restore_checkpoint",
                                "plan_migration", "apply_migration", "build_release", "cancel_job", "register_visual_baseline"}
PREVIEW_NUMBERS = {1, 2, 4, 5, 9, 10, 12}
CHAT_THIRD_TEXT = "沿着湿漉漉的铁轨，我们慢慢走向站台另一端。"
UPDATED_THIRD_TEXT = "我把旧车票递给林汐，雨点落在我们的指尖。"
UPDATED_END_TEXT = "灯光在远处亮起。明天，我们再一起去新的地方。"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def digest(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def difference_paths(before, after, path="") -> set[str]:
    if type(before) is not type(after):
        return {path}
    if isinstance(before, dict):
        paths = set()
        for key in before.keys() | after.keys():
            child = path + "/" + str(key)
            paths.update({child} if key not in before or key not in after else difference_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list):
        if len(before) != len(after):
            return {path}
        return set().union(*(difference_paths(a, b, path + "/" + str(i)) for i, (a, b) in enumerate(zip(before, after))))
    return {path} if before != after else set()


def semantic_project(value):
    """Remove only source provenance and revision/baseline bookkeeping."""
    if isinstance(value, dict):
        return {key: semantic_project(item) for key, item in value.items() if key != "provenance"}
    if isinstance(value, list):
        return [semantic_project(item) for item in value]
    return value


def within(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve(strict=True)
    if not candidate.is_relative_to(root.resolve(strict=True)):
        raise ValueError(f"Evidence path escapes its declared root: {candidate}")
    return candidate


def regular_tree(root: Path) -> dict:
    """Independently hash a published tree without following links or reparse points."""
    root = root.resolve(strict=True)
    files = {}
    for current, directories, names in os.walk(root, followlinks=False):
        for name in directories + names:
            path = Path(current) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Release evidence tree contains a link or reparse point")
            if not path.resolve(strict=True).is_relative_to(root):
                raise ValueError("Release evidence tree escapes its declared directory")
            if stat.S_ISREG(info.st_mode):
                files[path.relative_to(root).as_posix()] = {"sha256": sha(path), "bytes": info.st_size}
            elif not stat.S_ISDIR(info.st_mode):
                raise ValueError("Release evidence tree contains a nonregular entry")
    return dict(sorted(files.items()))


def archive_members(package: Path) -> tuple[list[str], dict]:
    """Read the original ZIP member bytes, without extracting or running the package."""
    members, destinations = {}, set()
    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
        for item in archive.infolist():
            # Windows ZipInfo normalizes backslashes in filename, while keeping
            # the actual archive spelling in orig_filename.
            raw = item.orig_filename
            parts = raw.rstrip("/").split("/")
            if (not raw or any(char in raw for char in ("\\", ":", "\x00"))
                    or raw.startswith("/") or any(part in {"", ".", ".."} for part in parts)
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Release archive contains an unsafe member path")
            relative = "/".join(parts)
            if relative.casefold() in destinations:
                raise ValueError("Release archive contains colliding member paths")
            destinations.add(relative.casefold())
            if not item.is_dir():
                with archive.open(item) as stream:
                    member_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                members[relative] = {"sha256": member_hash, "bytes": item.file_size}
    return names, dict(sorted(members.items()))


def same_rgba_pixels(first: Path, second: Path) -> bool:
    """Independently check actual decoded pixels; do not trust comparison metrics."""
    from PIL import Image
    with Image.open(first) as before, Image.open(second) as after:
        before.load()
        after.load()
        return before.size == after.size and before.convert("RGBA").tobytes() == after.convert("RGBA").tobytes()


def png_capture_resolution(path: Path) -> list[int]:
    """Read the physical PNG dimensions without resizing to the logical canvas."""
    from PIL import Image
    with Image.open(path) as captured:
        if captured.format != "PNG":
            raise ValueError("Native capture evidence must be an actual PNG")
        captured.load()
        return list(captured.size)


def native_renderer_from_log(text: str) -> dict:
    """Read native SDK renderer facts independently from its preserved log."""
    backend = re.search(r"^Initializing ([a-zA-Z0-9_]+) renderer:\s*$", text, re.MULTILINE)
    values = {}
    for key in ("vendor", "renderer", "version"):
        match = re.search(r"^\s*" + key.capitalize() + r":\s*(.+)$", text, re.MULTILINE)
        values[key] = match.group(1).strip() if match else None
    values["backend"] = backend.group(1) if backend else None
    return values


def audit_visual_baseline(audit, calls: list[dict], final_jobs: list[dict], directory: Path,
                          project_id: str, revision: int, model: dict, required_frames: set[str]) -> dict:
    initial_check_count = len(audit.checks)
    registrations = [call for call in calls if call.get("tool") == "register_visual_baseline"
                     and call.get("arguments", {}).get("project_id") == project_id]
    if not registrations:
        return {"status": "not_run", "reason": "No actual public baseline registration; historical previews remain unverified for automatic comparison."}
    last = registrations[-1]
    arguments, result = last["arguments"], public_result(last)
    repeated = [call for call in registrations if call["arguments"] == arguments]
    audit.check("visual_baseline_exact_public_retry_is_idempotent", len(repeated) >= 2
                and all(public_result(call) == result for call in repeated)
                and result.get("revision") == revision and arguments.get("expected_revision") == revision
                and result.get("content_revision_changed") is False and result.get("capture_resolution_verified") is True)
    baseline_id = result.get("baseline_id")
    if not re.fullmatch(r"visual_[0-9a-f]{24}", str(baseline_id)):
        raise ValueError("Baseline identity must be returned by an actual public registration")
    base = within(directory, "visual_baselines")
    manifest_path = within(base, result["manifest_path"])
    manifest = read_json(manifest_path)
    index_path = within(base, "index.json")
    index = read_json(index_path)
    audit.record("visual_baseline_manifest", manifest_path)
    audit.record("visual_baseline_index", index_path)
    payload_hash = digest({"project_id": project_id, "expected_revision": arguments["expected_revision"],
                           "preview_job_id": arguments["preview_job_id"], "review_note": arguments["review_note"],
                           "frame_ids": None if arguments.get("frame_ids") is None else sorted(arguments["frame_ids"])})
    indexed = index.get("baselines", {}).get(baseline_id, {})
    operation = index.get("operations", {}).get(arguments.get("operation_id"), {})
    frames = manifest.get("frames", {})
    audit.check("visual_baseline_manifest_identity_and_immutable_hash", manifest.get("baseline_id") == baseline_id
                and manifest.get("project_id") == project_id and manifest.get("revision") == revision
                and manifest.get("model_hash") == digest(model) and manifest.get("payload_hash") == payload_hash
                and sha(manifest_path) == indexed.get("sha256") == result.get("manifest_sha256")
                and within(base, indexed["manifest_path"]) == manifest_path)
    audit.check("visual_baseline_public_operation_matches_persisted_index", operation.get("digest") == payload_hash
                and operation.get("result") == result)
    audit.check("visual_baseline_covers_all_six_requested_frames", set(frames) == required_frames
                and set(result.get("frame_ids", [])) == required_frames
                and all(index.get("active_frames", {}).get(frame) == baseline_id for frame in required_frames))
    accepted = {job["job_id"]: job for job in final_jobs if job["kind"] == "preview"}
    first = accepted.get(arguments["preview_job_id"])
    audit.check("visual_baseline_registered_from_this_candidate_first_native_preview", first is not None
                and manifest.get("preview_job_id") == arguments["preview_job_id"]
                and manifest.get("code_digest") == (first or {}).get("report", {}).get("code_digest"))
    if first is None:
        return {"status": "failed", "baseline_id": baseline_id, "reason": "Registration preview is not an accepted current-code native job"}
    first_report = first["report"]
    audit.check("first_preview_without_registered_baseline_is_not_counted_as_comparison_passed",
                first_report.get("visual_comparison", {}).get("status") == "not_run")
    audit.check("registered_baseline_binds_first_native_report_bytes", manifest.get("preview_report_sha256") == sha(Path(first["report_path"])))
    first_environment = first_report.get("visual_environment", {})
    first_fingerprint = {key: first_environment.get(key) for key in ("sdk_version", "renderer", "resolution", "capture_resolution", "font_sha256", "language")}
    first_fingerprint["capture_resolution_verified"] = first_environment.get("capture_resolution_verified") is True
    audit.check("registered_baseline_binds_first_report_native_fingerprint", first_fingerprint == manifest.get("environment")
                and first_fingerprint["capture_resolution_verified"] is True
                and first_fingerprint["resolution"] == [model["canvas"]["width"], model["canvas"]["height"]])
    first_images = {item["frame_id"]: item for item in first_report.get("screenshots", [])}
    baseline_paths = {}
    for frame, item in frames.items():
        path = within(base, item["image_path"])
        baseline_paths[frame] = path
        audit.record("visual_baseline_image:" + frame, path)
        audit.check("visual_baseline_actual_image_matches_first_preview:" + frame,
                    sha(path) == item.get("sha256") == first_images.get(frame, {}).get("sha256")
                    and item.get("frame_hash") == digest(model["frames"][frame])
                    and png_capture_resolution(path) == manifest.get("environment", {}).get("capture_resolution"))
    comparison_jobs = [job for job in accepted.values() if job["job_id"] != first["job_id"]
                       and job["report"].get("visual_comparison", {}).get("status") == "passed"]
    audit.check("second_native_preview_performed_default_baseline_comparison", bool(comparison_jobs))
    checked_jobs = []
    for job in comparison_jobs:
        report, job_id = job["report"], job["job_id"]
        comparison = report.get("visual_comparison", {})
        entries = comparison.get("comparisons", [])
        actual_images = {item["frame_id"]: item for item in report.get("screenshots", [])}
        starters = [call for call in calls if call.get("tool") == "render_preview" and public_result(call).get("job_id") == job_id]
        audit.check("second_preview_started_after_exact_registration_retry:" + job_id,
                    bool(starters) and all(calls.index(starter) > calls.index(repeated[-1]) for starter in starters))
        audit.check("comparison_did_not_replace_registered_baseline:" + job_id,
                    comparison.get("ok") is True and comparison.get("baseline_updated") is False
                    and comparison.get("capture_resolution_verified") is True
                    and not comparison.get("unregistered_frame_ids")
                    and {entry.get("frame_id") for entry in entries} == required_frames and len(entries) == len(required_frames))
        environment = report.get("visual_environment", {})
        normalized = {key: environment.get(key) for key in ("sdk_version", "renderer", "resolution", "capture_resolution", "font_sha256", "language")}
        normalized["capture_resolution_verified"] = environment.get("capture_resolution_verified") is True
        capture = normalized["capture_resolution"]
        audit.check("comparison_actual_native_environment_matches_immutable_baseline:" + job_id,
                    normalized == manifest.get("environment") and normalized["sdk_version"] == report.get("sdk_version")
                    and normalized["resolution"] == [model["canvas"]["width"], model["canvas"]["height"]]
                    and isinstance(capture, list) and len(capture) == 2
                    and all(type(n) is int and n > 0 for n in capture)
                    and isinstance(normalized["renderer"], dict)
                    and all(isinstance(normalized["renderer"].get(key), str) and normalized["renderer"][key].strip()
                            for key in ("backend", "vendor", "renderer", "version")))
        # Native hook evidence itself is preserved, rather than only accepting
        # a caller-supplied renderer or report summary.
        engine_log_path = within(Path(job["report_path"]).parent, "native-tests.engine-log.txt")
        audit.record("native_visual_environment_log:" + job_id, engine_log_path)
        engine_log = engine_log_path.read_text(encoding="utf-8-sig")
        logged_renderer = native_renderer_from_log(engine_log)
        font = within(directory, Path(report["generation"]["project_path"]) / "game/SourceHanSansLite.ttf")
        audit.record("native_visual_environment_font:" + job_id, font)
        audit.check("comparison_environment_bound_to_actual_sdk_log_and_font_bytes:" + job_id,
                    logged_renderer == normalized["renderer"] and sha(font) == normalized["font_sha256"])
        for entry in entries:
            frame = entry["frame_id"]
            actual = actual_images.get(frame, {})
            actual_path = within(directory, entry["actual_path"])
            audit.check("default_comparison_true_zero_pixel_difference:" + job_id + ":" + frame,
                        entry.get("status") == "passed" and entry.get("baseline_id") == baseline_id
                        and entry.get("baseline_revision") == revision
                        and entry.get("baseline_sha256") == frames[frame]["sha256"]
                        and entry.get("actual_sha256") == actual.get("sha256") == sha(actual_path)
                        and Path(actual.get("path", "")).resolve() == actual_path
                        and within(base, entry["baseline_path"]) == baseline_paths[frame]
                        and entry.get("metrics", {}).get("changed_pixels") == 0
                        and entry.get("metrics", {}).get("changed_fraction") == 0
                        and entry.get("metrics", {}).get("allowed_changed_pixels") == 0
                        and png_capture_resolution(actual_path) == capture
                        and same_rgba_pixels(baseline_paths[frame], actual_path))
        checked_jobs.append(job_id)
    checks_passed = all(check["status"] == "passed" for check in audit.checks[initial_check_count:])
    return {"status": "passed" if checked_jobs and checks_passed else "failed", "baseline_id": baseline_id,
            "operation_id": arguments["operation_id"], "identical_public_registration_calls": [call["id"] for call in repeated],
            "manifest_path": str(manifest_path), "manifest_sha256": sha(manifest_path), "revision": revision,
            "first_preview_job_id": first["job_id"], "comparison_preview_job_ids": checked_jobs,
            "frame_ids": sorted(required_frames), "content_revision_changed": False}


def audit_release(audit, report: dict, directory: Path, evidence: Path, job_id: str):
    """Bind the real temporary command trace to identical persistent release bytes."""
    release, execution, stage = report.get("release", {}), report.get("execution", {}), report.get("release_staging", {})
    package = within(directory, release["path"])
    published_exe = within(directory, release["executable"])
    published_root = within(directory, package.parent / "independent")
    for label, target, expected_hash in (("path", package, release.get("sha256")),
                                         ("executable", published_exe, release.get("executable_sha256"))):
        audit.record("release:" + job_id + ":" + label, target)
        audit.check("release_bytes:" + job_id + ":" + label, sha(target) == expected_hash)
    names, actual_members = archive_members(package)
    audit.check("release_zip_size_and_member_count:" + job_id,
                package.stat().st_size == release.get("bytes") and len(names) == release.get("entries"))
    manifests = {}
    for label, entry in (("members", execution.get("package_members_manifest", {})),
                         ("tested", execution.get("tested_tree_manifest", {})),
                         ("published", execution.get("published_tree_manifest", {})),
                         ("source", stage.get("source_manifest", {}))):
        manifest_path = within(evidence, entry["path"])
        manifest = read_json(manifest_path)
        audit.record("release_manifest:" + job_id + ":" + label, manifest_path)
        files = manifest.get("files", {})
        valid = bool(files) and all(isinstance(name, str) and isinstance(item, dict)
                    and bool(re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))))
                    and type(item.get("bytes")) is int and item["bytes"] >= 0 for name, item in files.items())
        audit.check("release_manifest_identity:" + job_id + ":" + label,
                    valid and sha(manifest_path) == entry.get("sha256") and digest(files) == manifest.get("tree_sha256") == entry.get("tree_sha256")
                    and len(files) == entry.get("file_count"))
        manifests[label] = files
    actual_published = regular_tree(published_root)
    audit.check("release_original_zip_members_match_manifest:" + job_id, actual_members == manifests["members"])
    audit.check("release_original_members_unchanged_after_native_test:" + job_id,
                execution.get("package_members_unchanged") is True
                and all(manifests["tested"].get(name) == item for name, item in actual_members.items()))
    audit.check("release_tested_and_actual_published_complete_trees_match:" + job_id,
                execution.get("tested_and_published_identical") is True
                and manifests["tested"] == manifests["published"] == actual_published
                and digest(actual_published) == execution.get("tested_tree_sha256") == execution.get("published_tree_sha256"),
                {"actual_file_count": len(actual_published), "actual_tree_sha256": digest(actual_published)})
    source_root = within(directory, stage["source_directory"])
    audit.check("release_copied_full_generated_and_retained_source_without_mutation:" + job_id,
                stage.get("source_copy_verified") is True and stage.get("source_unchanged") is True
                and regular_tree(source_root) == manifests["source"])
    staged_package = stage.get("staged_package", {})
    audit.check("release_published_zip_matches_actually_staged_zip:" + job_id,
                staged_package.get("sha256") == release.get("sha256") and staged_package.get("bytes") == package.stat().st_size)
    # The short temporary files have been deliberately cleaned up. Their execution
    # is established by the exact command trace and pre/post SHA manifests, not by
    # pretending the long published path was the path actually executed.
    tested_exe = Path(execution["actually_tested_executable"]).resolve()
    tested_directory = Path(execution["actually_tested_directory"]).resolve()
    temporary_root = Path(stage["canonical_directory"]).resolve()
    temporary_independent = Path(stage["independent_directory"]).resolve()
    published_relative = published_exe.relative_to(published_root).as_posix()
    tested_relative = tested_exe.relative_to(temporary_independent).as_posix()
    command = report.get("commands", {}).get("package-tests", {})
    argv = command.get("argv", [])
    audit.check("release_real_package_command_matches_short_tested_executable:" + job_id,
                execution.get("status") == "passed" and tested_directory == tested_exe.parent
                and tested_exe.is_relative_to(temporary_root) and temporary_independent.is_relative_to(temporary_root)
                and len(argv) >= 3 and Path(argv[0]).resolve() == tested_exe and Path(argv[1]).resolve() == tested_directory
                and argv[2] == "test" and Path(command.get("cwd", "")).resolve() == tested_directory
                and command.get("exit_code") == 0 and not command.get("timed_out")
                and "[rpytest] Status: PASSED" in command.get("stdout", "") + command.get("stderr", ""))
    exe_entry = actual_members.get(published_relative, {})
    audit.check("release_same_executable_tested_and_published:" + job_id,
                published_relative == tested_relative and exe_entry.get("sha256") == sha(published_exe)
                == execution.get("actually_tested_executable_sha256") == execution.get("published_executable_sha256")
                and Path(execution.get("published_executable", "")).resolve() == published_exe
                and Path(execution.get("published_directory", "")).resolve() == published_exe.parent
                and Path(release.get("independent_directory", "")).resolve() == published_exe.parent)
    distribute = report.get("commands", {}).get("distribute", {})
    distribute_argv = distribute.get("argv", [])
    audit.check("release_distribute_actually_used_short_staged_project:" + job_id,
                distribute.get("exit_code") == 0 and not distribute.get("timed_out")
                and "distribute" in distribute_argv and stage.get("project_directory") in distribute_argv
                and "--destination" in distribute_argv and stage.get("output_directory") in distribute_argv
                and Path(stage["project_directory"]).resolve().is_relative_to(temporary_root))
    cleanup = stage.get("cleanup", {})
    audit.check("release_short_workspace_cleanup_record_matches_actual_absence:" + job_id,
                cleanup.get("status") == "removed" and cleanup.get("ownership_verified") is True
                and cleanup.get("directory_exists") is False and not temporary_root.exists())
    for screenshot in report.get("package_screenshots", []):
        target = within(published_root, screenshot["path"])
        audit.check("release_package_screenshot_hash:" + job_id + ":" + target.name, sha(target) == screenshot.get("sha256"))


def public_result(call: dict) -> dict:
    result = call.get("result") or {}
    for key in ("structured_content", "structuredContent"):
        if isinstance(result.get(key), dict):
            return result[key]
    for block in result.get("content", []):
        if block.get("type") == "text":
            try:
                value = json.loads(block.get("text", ""))
                if isinstance(value, dict):
                    return value
            except ValueError:
                pass
    return {}


def audit_content_operations(audit, calls: list[dict], head: dict, committed: set[int], project_id: str):
    """Check model commits; visual registration has a separate immutable index."""
    for call in calls:
        if call.get("tool") == "register_visual_baseline":
            continue
        arguments, value = call.get("arguments", {}), public_result(call)
        if arguments.get("project_id") == project_id and value.get("revision") in committed and arguments.get("operation_id"):
            operation = head.get("operations", {}).get(arguments["operation_id"], {})
            audit.check("mcp_operation_matches_persisted_commit:" + arguments["operation_id"],
                        operation.get("result", {}).get("revision") == value["revision"])


def source_substitute_mentioned(command: str) -> bool:
    """Ignore only excluded rg glob tokens; explicit input arguments still count."""
    inspected = command
    if re.search(r'(?:^|\s|["\'])rg(?:\.exe)?\s', command, re.I):
        inspected = re.sub(r'''--glob(?:\s+|=)["'\\]*!(?:\*\*/)?(?:story-spec\.json|voice-lines\.json)(?=["'\\\s]|$)''',
                           "", inspected, flags=re.I)
    return bool(re.search(r"story-spec\.json|voice-lines\.json|probe_pipeline|probe_generator", inspected, re.I))


def native_job_identity(status: dict, report: dict, arguments: dict, tool: str,
                        project_id: str, job_id: str, progress: dict | None = None) -> bool:
    """A failed thin report can document failure, never substitute for success."""
    if not (status.get("job_id") == job_id and status.get("project_id") == project_id
            and arguments.get("project_id") == project_id and status.get("kind") == JOB_TOOLS.get(tool)
            and status.get("revision") == arguments.get("revision")):
        return False
    fields = {"job_id": job_id, "project_id": project_id, "revision": status["revision"]}
    if all(report.get(key) == value for key, value in fields.items()):
        return True
    # The historical exception report deliberately omitted identity fields.
    # Its actual status and independent preserved progress bind the failure;
    # contradictory fields or a success claim must never get this exception.
    return (all(key not in report for key in fields) and status.get("state") == "failed"
            and report.get("status") == "failed" and report.get("ok") is not True
            and isinstance(report.get("error"), dict) and bool(report["error"].get("code"))
            and status.get("error") == report["error"]
            and isinstance(progress, dict) and all(progress.get(key) == value for key, value in fields.items())
            and progress.get("ok") is not True)


def successful(call: dict) -> bool:
    transport_ok = call.get("status") == "completed" and not call.get("error") and not (call.get("result") or {}).get("isError")
    value = public_result(call)
    # Async starters return a persisted job record, without an `ok` envelope.
    # This acknowledges start only; terminal report checks below decide success.
    if call.get("tool") in JOB_TOOLS:
        return transport_ok and value.get("ok") is not False and bool(re.fullmatch(r"job_[0-9a-f]{32}", str(value.get("job_id", ""))))
    return transport_ok and value.get("ok") is True


def read_xlsx(path: Path) -> dict[str, dict[str, object]]:
    """Read the saved source bytes without formulas or a spreadsheet runtime."""
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
        ridns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {r.attrib["Id"]: r.attrib["Target"] for r in relationships.findall(relns + "Relationship")}
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(n.text or "" for n in node.iter("{" + NS["x"] + "}t"))
                      for node in ET.fromstring(archive.read("xl/sharedStrings.xml"))]
        result = {}
        for sheet in workbook.find("x:sheets", NS):
            target = targets[sheet.attrib[ridns]]
            target = target.lstrip("/") if target.startswith("/") else "xl/" + target
            cells = {}
            for cell in ET.fromstring(archive.read(target)).findall(".//x:sheetData/x:row/x:c", NS):
                if cell.find("x:f", NS) is not None or cell.attrib.get("t") == "e":
                    raise ValueError("Demo source must contain literal material rows, without formula/error cells")
                value = cell.find("x:v", NS)
                if cell.attrib.get("t") == "s":
                    parsed = shared[int(value.text)]
                elif cell.attrib.get("t") == "inlineStr":
                    parsed = "".join(node.text or "" for node in cell.findall(".//x:t", NS))
                elif value is not None:
                    parsed = int(value.text) if value.text.isdigit() else value.text
                else:
                    parsed = None
                if parsed is not None:
                    cells[cell.attrib["r"]] = parsed
            result[sheet.attrib["name"]] = cells
        return result


class Audit:
    def __init__(self):
        self.checks = []
        self.inputs = {}

    def check(self, name: str, condition: bool, details=None) -> bool:
        passed = bool(condition)
        self.checks.append({"check": name, "status": "passed" if passed else "failed", "details": details})
        return passed

    def record(self, name: str, path: Path):
        self.inputs[name] = {"path": str(path.resolve()), "sha256": sha(path), "bytes": path.stat().st_size}


def number_for(frame: dict) -> int | None:
    value = frame.get("provenance", {}).get("frame_number")
    if type(value) is int:
        return value
    label = frame.get("source_label") or ""
    match = re.search(r"第\s*(\d+)\s*帧", label)
    return int(match[1]) if match else None


def provenance_ok(value: dict, source_id: str, source_hash: str, row: int | None = None, lines: tuple[int, int] | None = None) -> bool:
    if not isinstance(value, dict) or value.get("source_id") != source_id:
        return False
    if value.get("source_hash", value.get("hash")) != source_hash:
        return False
    location = value.get("location", {}) if isinstance(value.get("location"), dict) else {}
    if lines:
        start = value.get("line_start", location.get("line_start"))
        end = value.get("line_end", location.get("line_end", start))
        return type(start) is int and type(end) is int and lines[0] <= start <= end <= lines[1]
    sheet = value.get("sheet", location.get("sheet"))
    actual_row = value.get("row", location.get("row"))
    cell = value.get("cell", value.get("coordinate", location.get("coordinate", location.get("cell"))))
    if actual_row is None and isinstance(cell, str):
        match = re.search(r"(\d+)$", cell)
        actual_row = int(match[1]) if match else None
    return sheet == "分镜材料" and (row is None or actual_row == row)


def audit_shell(audit: Audit, items: list[dict], root: Path, record_paths: set[Path], project_directory: Path,
                summary_path: Path | None = None, additional_summary_paths: list[Path] | None = None):
    """Conservative trace audit; arbitrary shell programs remain unverified."""
    allowed_files = {root / "evidence/m8-demo/author-summary.json", root / "evidence/m8-demo/followup-summary.json",
                     root / "evidence/m8-demo/chat-modify-summary.json", root / "evidence/m4-equivalent/author-summary.json"}
    allowed_files = {p.resolve() for p in allowed_files}
    declared_summaries = ([summary_path] if summary_path is not None else []) + (additional_summary_paths or [])
    for declared_path in declared_summaries:
        declared_path = declared_path.resolve(strict=True)
        if not declared_path.is_relative_to((root / "evidence").resolve()):
            raise ValueError("Additional external summary must stay inside workspace evidence")
        allowed_files.add(declared_path)
        audit.record("declared_external_summary:" + str(declared_path.relative_to(root)), declared_path)
    reviews = []
    for item in items:
        kind = item.get("type")
        if kind == "file_change":
            changes = item.get("changes", [])
            paths = [Path(c.get("path", "")).resolve() for c in changes if isinstance(c, dict)]
            permitted = bool(paths) and all(p in allowed_files for p in paths)
            audit.check("author_file_change_only_external_summary", permitted, {"id": item.get("id"), "paths": list(map(str, paths))})
            continue
        if kind != "command_execution":
            continue
        command = item.get("command", "")
        flat = command.replace("\\\\", "\\").casefold()
        forbidden = bool(re.search(r"\b(?:from|import)\s+renpy_bridge|\b(?:Store|JobManager)\s*\(|mcp_servers|model[\\/]head\.json|history[\\/]revisions|\.rpy\b", command, re.I))
        substitutes = source_substitute_mentioned(command)
        read_only = bool(re.search(r"\b(?:Get-Content|Get-Item|Test-Path|Resolve-Path|rg|type|cat)\b", command, re.I))
        write_token = bool(re.search(r"\b(?:Set-Content|Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item|Invoke-Expression|Start-Process)\b|write_text|write_bytes|open\([^\n]*['\"](?:w|a|x)", command, re.I))
        private_project_write = write_token and str(project_directory).casefold() in flat
        audit.check("author_shell_has_no_private_core_or_input_substitute", not forbidden and not substitutes and not private_project_write,
                    {"id": item.get("id"), "command": command})
        external_summary_write = write_token and any(str(p).casefold() in flat for p in allowed_files)
        if read_only and not write_token and not forbidden and not substitutes:
            classification = "read_only"
        elif external_summary_write and not forbidden and not substitutes:
            classification = "external_summary_write_requires_manual_review"
        else:
            classification = "unclassified_requires_manual_review"
        reviews.append({"id": item.get("id"), "classification": classification, "command": command})
    return reviews


def verify(args, audit: Audit) -> dict:
    original_workbook = args.workbook.resolve(strict=True)
    workbook = args.updated_workbook.resolve(strict=True) if args.scenario == "reimport" else original_workbook
    materials = args.materials.resolve(strict=True)
    spec = read_json(args.input_spec)
    source_path = args.text_source.resolve(strict=True) if args.scenario == "equivalent" else workbook
    source_hash = sha(source_path)
    audit.record("frozen_original_workbook", original_workbook)
    audit.record("workbook", workbook)
    audit.record("actual_author_source", source_path)
    audit.record("independent_original_row_metadata", args.input_spec)
    sheets = read_xlsx(workbook)
    audit.check("source_has_three_declared_sheets", list(sheets) == ["制作说明", "分镜材料", "素材清单"])
    cells = sheets["分镜材料"]
    source_rows = [{key: cells.get(f"{chr(65 + col)}{number + 6}") for col, key in enumerate(spec["columns"])} for number in range(1, 13)]
    rows = deepcopy(source_rows)
    if args.scenario == "reimport":
        expected_updated = deepcopy(spec["rows"])
        expected_updated[2]["对白或旁白"] = UPDATED_THIRD_TEXT
        expected_updated[11]["对白或旁白"] = UPDATED_END_TEXT
        audit.check("updated_source_has_only_two_authorized_text_changes", source_rows == expected_updated)
        rows[2]["对白或旁白"] = CHAT_THIRD_TEXT
        rows[3]["人物图片"] = "images/linxi-smile.png"
    else:
        audit.check("xlsx_equals_frozen_original_input_rows", rows == spec["rows"])
    if args.scenario == "chat-modify":
        rows[2]["对白或旁白"] = CHAT_THIRD_TEXT
        rows[3]["人物图片"] = "images/linxi-smile.png"
    audit.check("source_frame_sequence_and_acts", [r["帧号"] for r in rows] == list(range(1, 13)) and len({r["幕"] for r in rows}) == 2)
    asset_paths = [sheets["素材清单"].get(f"C{row}") for row in range(7, 17)]
    audit.check("source_has_ten_distinct_material_files", len(set(asset_paths)) == 10 and all(isinstance(p, str) for p in asset_paths))
    expected_hashes = {relative: sha(within(materials, relative)) for relative in asset_paths}
    for relative in asset_paths:
        audit.record("material:" + relative, materials / relative)

    text_lines = {}
    if args.scenario == "equivalent":
        original_text = source_path.read_text(encoding="utf-8-sig")
        raw_lines = original_text.splitlines()
        for index, line in enumerate(raw_lines):
            match = re.fullmatch(r"(?:第一幕|第二幕) 第(\d+)帧", line)
            if match:
                end = index + 1
                while end < len(raw_lines) and raw_lines[end].strip():
                    end += 1
                text_lines[int(match[1])] = (index + 1, end)
        audit.check("text_source_contains_all_original_material_fields", len(text_lines) == 12 and all(
            all(key + "：" + str(row[key]) in "\n".join(raw_lines[text_lines[row["帧号"]][0]-1:text_lines[row["帧号"]][1]])
                for key in spec["columns"][2:]) for row in spec["rows"]))
    calls, shell, host_homes, reports, call_lifecycle = [], [], [], [], []
    for index, given in enumerate(args.author_run):
        report_path = given.resolve(strict=True)
        if report_path.is_dir():
            report_path = report_path / "report.json"
        if report_path.name == "latest.json":
            report_path = Path(read_json(report_path)["report"]).resolve(strict=True)
        harness = read_json(report_path)
        reports.append(harness)
        events_path = report_path.parent / "raw-events.jsonl"
        audit.record(f"author_harness:{index}", report_path)
        audit.record(f"author_raw_events:{index}", events_path)
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        completed = [e["item"] for e in events if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)]
        real_calls = [i for i in completed if i.get("type") == "mcp_tool_call"]
        completed_call_ids = {item.get("id") for item in real_calls}
        unfinished_calls = [{"id": item.get("id"), "server": item.get("server"), "tool": item.get("tool"),
                             "arguments": item.get("arguments", {}), "completion_observed": False,
                             "acceptance": "not_counted_as_completed_or_passed"}
                            for event in events if event.get("type") == "item.started"
                            and isinstance(item := event.get("item"), dict) and item.get("type") == "mcp_tool_call"
                            and item.get("id") not in completed_call_ids]
        call_lifecycle.append({"author_run_index": index, "run_id": harness.get("run_id"),
                               "completed_mcp_call_count": len(real_calls),
                               "started_without_completion": unfinished_calls})
        audit.check(f"author_run_{index}_used_installed_config", harness.get("direct_mcp_configured") is False and harness.get("config_changed") is False
                    and "--ignore-user-config" not in harness.get("argv", []) and not any("mcp_servers" in str(a) for a in harness.get("argv", [])))
        audit.check(f"author_run_{index}_finished", harness.get("exit_code") == 0 and not harness.get("timed_out"))
        audit.check(f"author_run_{index}_no_other_mcp", all(c.get("server") == "renpy_bridge" for c in real_calls))
        calls.extend({**call, "_audit_run_index": index} for call in real_calls)
        shell.extend(i for i in completed if i.get("type") in {"command_execution", "file_change"})
    plugin_calls = [c for c in calls if c.get("server") == "renpy_bridge"]
    good = [c for c in plugin_calls if successful(c)]
    schema = read_json(args.tool_schema)
    allowed_tools = {t["name"] for t in schema["tools"]}
    audit.record("public_tool_schema", args.tool_schema)
    audit.check("schema_snapshot_has_current_26_explicit_public_tools", allowed_tools == PUBLIC_TOOLS,
                {"count": len(allowed_tools), "missing": sorted(PUBLIC_TOOLS - allowed_tools), "unexpected": sorted(allowed_tools - PUBLIC_TOOLS)})
    audit.check("real_author_tool_names_are_public", bool(good) and all(c.get("tool") in allowed_tools for c in plugin_calls))
    invoked = {c["tool"] for c in good}
    required_tools = REQUIRED_TOOLS | ({"create_checkpoint", "patch_frame", "plan_reimport", "apply_reimport", "restore_checkpoint", "build_release"} if args.scenario == "reimport" else set())
    if args.scenario == "chat-modify":
        required_tools |= {"create_checkpoint", "patch_frame"}
    if args.require_visual_baseline:
        required_tools |= {"register_visual_baseline"}
    audit.check("author_completed_required_public_steps", required_tools <= invoked, {"missing": sorted(required_tools - invoked), "invoked": sorted(invoked)})
    for call in good:
        value = public_result(call)
        if call["tool"] == "get_environment" and isinstance(value.get("home"), str):
            host_homes.append(Path(value["home"]).resolve())
    audit.check("production_home_came_from_real_plugin", bool(host_homes) and len(set(host_homes)) == 1)
    home = args.bridge_home.resolve(strict=True) if args.bridge_home else host_homes[-1]
    audit.check("selected_home_equals_real_host", all(home == p for p in host_homes), str(home))
    created = [public_result(c).get("project_id") for c in good if c["tool"] == "create_project"]
    project_id = args.project_id or (created[-1] if created else None)
    if not project_id or not re.fullmatch(r"p_[0-9a-f]{20}", project_id):
        raise ValueError("Need an actual project_id returned by the author or supplied with --project-id")
    audit.check("one_project_created_by_author", set(created) == {project_id}, created)
    directory = within(home, "projects/" + project_id)
    head_path = within(directory, "model/head.json")
    head = read_json(head_path)
    revision = args.revision if args.revision is not None else head["revision"]
    model_path = within(directory, f"history/revisions/{revision:08d}.json")
    model = read_json(model_path)
    audit.record("project_head", head_path)
    audit.record("committed_revision", model_path)
    audit.check("project_version_identity", model.get("project_id") == project_id and model.get("revision") == revision and model.get("schema_version") == "1.0")
    if args.scenario == "chat-modify":
        audit.check("chat_revision_is_saved_draft", head.get("revision") == revision and isinstance(head.get("runnable_revision"), int)
                    and head["runnable_revision"] <= revision)
    else:
        audit.check("latest_revision_runnable", head.get("revision") == revision and head.get("runnable_revision") == revision)
    committed = {public_result(c).get("revision") for c in good if c.get("arguments", {}).get("project_id") == project_id
                 and c["tool"] in {"register_assets", "commit_import", "apply_frame_batch", "patch_frame", "apply_layout", "update_flow", "apply_reimport", "restore_checkpoint"}}
    audit.check("every_content_revision_has_real_mcp_mutation", set(range(1, revision + 1)) <= committed, {"observed": sorted(r for r in committed if type(r) is int), "revision": revision})
    audit_content_operations(audit, good, head, committed, project_id)

    source_files = {source_path: None if args.scenario == "equivalent" else sheets}
    if args.scenario == "reimport":
        source_files[original_workbook] = read_xlsx(original_workbook)
    extracted_calls = [c for c in good if c["tool"] == "extract_source" and c.get("arguments", {}).get("project_id") == project_id
                       and Path(c.get("arguments", {}).get("source_path", "")).resolve() in source_files]
    extracts = [public_result(c) for c in extracted_calls]
    target_extracts = [public_result(c) for c in extracted_calls if Path(c["arguments"]["source_path"]).resolve() == source_path]
    allowed_source_hashes = {sha(p) for p in source_files}
    audit.check("material_read_via_public_extract_source", bool(target_extracts) and all(e.get("hash") == source_hash for e in target_extracts)
                and all(e.get("hash") in allowed_source_hashes for e in extracts))
    source_ids = {e.get("source_id") for e in extracts}
    if len(source_ids) != 1:
        raise ValueError("The material source must retain one actual source_id across its author turns")
    source_id = next(iter(source_ids))
    cold_source_acceptance = {"status": "not_required", "reason": "Historical imports remain recorded separately"}
    if args.require_visual_baseline:
        latest_run_index = len(args.author_run) - 1
        latest_extracts = [call for call in plugin_calls if call["_audit_run_index"] == latest_run_index and call["tool"] == "extract_source"]
        first_extract = latest_extracts[0] if latest_extracts else None
        first_arguments = (first_extract or {}).get("arguments", {})
        first_result = public_result(first_extract) if first_extract else {}
        saved_result = read_json(within(directory, first_result["record_path"])) if first_result.get("record_path") else {}
        unfinished_extracts = [call for call in call_lifecycle[-1]["started_without_completion"] if call["tool"] == "extract_source"]
        cold_ok = audit.check("latest_real_cold_first_excel_call_completed_full_263_records",
                              bool(first_extract) and successful(first_extract) and not unfinished_extracts
                              and first_arguments.get("project_id") == project_id
                              and Path(first_arguments.get("source_path", "")).resolve() == source_path
                              and first_arguments.get("source_id") == source_id
                              and first_arguments.get("offset") == 0 and first_arguments.get("limit") == 1000
                              and first_result.get("hash") == source_hash and first_result.get("offset") == 0
                              and first_result.get("total_records") == 263 and len(first_result.get("records", [])) == 263
                              and first_result.get("records") == saved_result.get("records"),
                              {"author_run_index": latest_run_index, "call_id": (first_extract or {}).get("id"),
                               "completed_extract_call_count": len(latest_extracts), "unfinished_extracts": unfinished_extracts})
        cold_source_acceptance = {"status": "passed" if cold_ok else "failed", "author_run_index": latest_run_index,
                                  "call_id": (first_extract or {}).get("id"), "total_records": first_result.get("total_records"),
                                  "returned_records": len(first_result.get("records", [])), "source_hash": first_result.get("hash"),
                                  "started_without_completion": unfinished_extracts}
    record_paths = {within(directory, e["record_path"]) for e in extracts}
    source_by_hash = {sha(p): (p, s) for p, s in source_files.items()}
    for record_path in record_paths:
        extracted = read_json(record_path)
        audit.record("extracted_source:" + record_path.name, record_path)
        record_source, record_sheets = source_by_hash[extracted["hash"]]
        if record_sheets is not None:
            saved_cells = {(sheet, coordinate): value for sheet, values in record_sheets.items() for coordinate, value in values.items()}
            observed = {(r.get("sheet"), r.get("coordinate")): r.get("value") for r in extracted.get("records", []) if r.get("kind") == "cell"}
            audit.check("saved_source_records_equal_xlsx:" + record_path.name, observed == saved_cells)
        else:
            raw_lines = record_source.read_text(encoding="utf-8-sig").splitlines()
            paragraphs = extracted.get("records", [])
            audit.check("saved_source_paragraphs_equal_actual_text", bool(paragraphs) and all(
                r.get("kind") == "paragraph" and r.get("text") == "\n".join(raw_lines[r["line_start"]-1:r["line_end"]]) for r in paragraphs))
        saved_original = within(directory, "source_inputs/" + extracted["hash"] + record_source.suffix.lower())
        audit.record("saved_original_source:" + extracted["hash"], saved_original)
        audit.check("saved_original_source_hash_matches:" + extracted["hash"], sha(saved_original) == extracted["hash"])
    shell_reviews = audit_shell(audit, shell, ROOT, record_paths, directory, args.summary, args.allowed_external_summary)

    frames = model.get("frames", {})
    mapping = {number_for(frame): frame_id for frame_id, frame in frames.items()}
    audit.check("twelve_frames_keep_source_number_identity", len(frames) == 12 and set(mapping) == set(range(1, 13)))
    if set(mapping) != set(range(1, 13)):
        raise ValueError("Cannot unambiguously map the twelve committed frames to original Excel rows")
    audit.check("entry_and_acts_match_source", model.get("entry_frame_id") == mapping[1] and len(model.get("acts", {})) == 2
                and all(model["acts"][frames[mapping[n]]["act_id"]]["name"] == rows[n-1]["幕"] for n in range(1, 13)))
    assets = model.get("assets", {})
    audit.check("exactly_ten_registered_assets", len(assets) == 10)
    managed_hashes = {}
    for asset_id, asset in assets.items():
        asset_path = within(directory, asset["path"])
        managed_hashes[asset_id] = sha(asset_path)
        audit.record("managed_asset:" + asset_id, asset_path)
        audit.check("asset_content_and_registered_hash:" + asset_id, managed_hashes[asset_id] == asset["sha256"])
        matching = [relative for relative, expected_hash in expected_hashes.items() if expected_hash == managed_hashes[asset_id]]
        origin = asset.get("provenance", {})
        audit.check("asset_type_and_original_material_path:" + asset_id, len(matching) == 1
                    and asset.get("kind") == ("image" if matching[0].startswith("images/") else "audio")
                    and Path(origin.get("source_path", "")).resolve() == (materials / matching[0]).resolve()
                    and origin.get("source_id") == source_id)
    audit.check("managed_assets_equal_material_bytes", sorted(managed_hashes.values()) == sorted(expected_hashes.values()))
    characters = model.get("characters", {})
    audit.check("one_named_adult_heroine", len(characters) == 1 and next(iter(characters.values()), {}).get("name") == "林汐")
    def same_asset(asset_id, relative):
        return asset_id is None if relative == "无" else managed_hashes.get(asset_id) == expected_hashes.get(relative)
    baseline = model.get("metadata", {}).get("source_baselines", {}).get(source_id, {}).get("frames", {})
    audit.check("source_baseline_contains_all_stable_frames", set(baseline) == set(frames))
    expected_edges = set()
    for number in range(1, 13):
        row, frame = rows[number - 1], frames[mapping[number]]
        fid, excel_row = mapping[number], number + 6
        def frame_provenance(value):
            return provenance_ok(value, source_id, source_hash, excel_row, text_lines.get(number))
        audit.check(f"frame_{number}_original_provenance", frame_provenance(frame.get("provenance", {}))
                    and frame.get("provenance", {}).get("frame_number") == number)
        bg = frame.get("bg")
        audit.check(f"frame_{number}_background", isinstance(bg, dict) and bg.get("kind") == "background" and same_asset(bg.get("asset_id"), row["背景图片"]))
        people = frame.get("characters", [])
        expected_person = row["人物图片"]
        audit.check(f"frame_{number}_character_variant", (not people if expected_person == "无" else len(people) == 1
                    and people[0].get("kind") == "character" and people[0].get("character_id") in characters and same_asset(people[0].get("asset_id"), expected_person)))
        decorations = frame.get("decorations", [])
        expected_decoration = row["附加图片"]
        audit.check(f"frame_{number}_only_declared_decoration", (not decorations if expected_decoration == "无" else len(decorations) == 1
                    and decorations[0].get("kind") == "image" and same_asset(decorations[0].get("asset_id"), expected_decoration)))
        dialogue = frame.get("dialogue")
        if row["对白或旁白"] == "无":
            audit.check(f"frame_{number}_dialogue_cleared", dialogue is None)
        else:
            audit.check(f"frame_{number}_dialogue_exact", isinstance(dialogue, dict) and dialogue.get("text") == row["对白或旁白"])
            speaker = dialogue.get("speaker_id") if isinstance(dialogue, dict) else None
            audit.check(f"frame_{number}_speaker", speaker is None if row["说话人"] == "旁白" else characters.get(speaker, {}).get("name") == row["说话人"])
            audit.check(f"frame_{number}_dialogue_provenance", frame_provenance((dialogue or {}).get("provenance", {})))
        audit.check(f"frame_{number}_voice_hash", same_asset(frame.get("voice"), row["配音文件"]))
        audit.check(f"frame_{number}_bgm_hash", same_asset(frame.get("bgm"), row["背景音乐"]))
        sfx = frame.get("sfx", [])
        audit.check(f"frame_{number}_sfx_hash", not sfx if row["音效"] == "无" else len(sfx) == 1 and same_asset(sfx[0], row["音效"]))
        transition = "fade" if row["画面衔接"] == "淡入" else "dissolve"
        audit.check(f"frame_{number}_transition", frame.get("transition", {}).get("kind") == transition)
        for component in ([bg] if isinstance(bg, dict) else []) + people + decorations:
            audit.check(f"frame_{number}_component_provenance:{component.get('component_id')}", frame_provenance(component.get("provenance", {})))
        flow = frame.get("flow", {})
        if number == 5:
            choices = flow.get("choices", [])
            captions = re.findall(r"选项\d+[:：]\s*(.*?)，进入第(\d+)帧", row["下一帧或选项"])
            audit.check("three_exact_choice_captions_and_destinations", flow.get("kind") == "choices" and len(choices) == 3 and len(captions) == 3
                        and all(choice.get("text") == caption and choice.get("target_frame_id") == mapping[int(target)]
                                for choice, (caption, target) in zip(choices, captions)))
            for choice in choices:
                expected_edges.add((fid, choice.get("choice_id"), choice.get("target_frame_id")))
                audit.check("choice_original_provenance:" + str(choice.get("choice_id")), frame_provenance(choice.get("provenance", {})))
        elif number == 12:
            audit.check("frame_12_end", flow.get("kind") == "end" and not flow.get("choices") and flow.get("target_frame_id") is None)
        else:
            destination = 9 if number in {6, 7, 8} else number + 1
            audit.check(f"frame_{number}_next", flow.get("kind") == "next" and flow.get("target_frame_id") == mapping[destination])
            expected_edges.add((fid, "next", mapping[destination]))
        base = baseline.get(fid, {})
        base_text = (base.get("dialogue") or {}).get("text")
        expected_base_text = None if source_rows[number-1]["对白或旁白"] == "无" else source_rows[number-1]["对白或旁白"]
        audit.check(f"frame_{number}_baseline_actual_source_text_and_flow", base_text == expected_base_text and base.get("flow") == frame.get("flow"))
        def component_ids(item):
            return [c["component_id"] for c in ([item["bg"]] if item.get("bg") else []) + item.get("characters", []) + item.get("decorations", [])]
        audit.check(f"frame_{number}_stable_component_identities", component_ids(base) == component_ids(frame))

    chat_details = None
    if args.scenario == "chat-modify":
        checkpoints = [public_result(c) for c in good if c["tool"] == "create_checkpoint"
                       and c.get("arguments", {}).get("project_id") == project_id]
        audit.check("chat_change_has_real_checkpoint", bool(checkpoints))
        if not checkpoints:
            raise ValueError("Need the actual pre-change public checkpoint")
        checkpoint = checkpoints[-1]
        checkpoint_path = within(directory, "checkpoints/" + checkpoint["snapshot_id"] + ".json")
        saved_checkpoint = read_json(checkpoint_path)
        audit.record("chat_checkpoint", checkpoint_path)
        before_path = within(directory, f"history/revisions/{checkpoint['revision']:08d}.json")
        before = read_json(before_path)
        audit.record("chat_checkpoint_committed_revision", before_path)
        audit.check("checkpoint_matches_actual_before_model", saved_checkpoint == {k: checkpoint[k] for k in saved_checkpoint}
                    and checkpoint.get("model_hash") == digest(before) and checkpoint["revision"] < revision)
        ticket_index = next(i for i, c in enumerate(before["frames"][mapping[4]]["decorations"])
                            if same_asset(c.get("asset_id"), "images/ticket.png"))
        prefix3 = "/frames/" + mapping[3] + "/dialogue"
        prefix4 = "/frames/" + mapping[4]
        allowed = {"/revision", prefix3 + "/text", prefix4 + "/characters/0/asset_id",
                   prefix4 + f"/decorations/{ticket_index}/layout/x"}
        changes = difference_paths(before, model)
        unexpected = {p for p in changes if p not in allowed and not p.startswith(prefix3 + "/provenance/")}
        audit.check("chat_changed_only_authorized_fields", not unexpected and allowed <= changes,
                    {"actual_paths": sorted(changes), "unexpected": sorted(unexpected)})
        before_ticket = before["frames"][mapping[4]]["decorations"][ticket_index]
        after_ticket = frames[mapping[4]]["decorations"][ticket_index]
        audit.check("chat_ticket_only_moved_right", after_ticket["layout"]["x"] > before_ticket["layout"]["x"]
                    and after_ticket["component_id"] == before_ticket["component_id"])
        continuation_calls = [c for c in good if c.get("_audit_run_index", 0) > 0]
        audit.check("chat_stage_did_not_start_reimport_or_updated_source", not any(c["tool"] in {"plan_reimport", "apply_reimport", "begin_import", "commit_import"}
                    or (c["tool"] == "extract_source" and Path(c.get("arguments", {}).get("source_path", "")).resolve() == args.updated_workbook.resolve())
                    for c in continuation_calls))
        chat_details = {"checkpoint": checkpoint, "before_revision": checkpoint["revision"], "after_revision": revision,
                        "changed_paths": sorted(changes), "ticket_before_x": before_ticket["layout"]["x"], "ticket_after_x": after_ticket["layout"]["x"]}

    reimport_details = None
    checkpoint_restore_details = []
    if args.scenario == "reimport":
        plans = [c for c in good if c["tool"] == "plan_reimport" and c.get("arguments", {}).get("project_id") == project_id]
        applications = [c for c in good if c["tool"] == "apply_reimport" and c.get("arguments", {}).get("project_id") == project_id]
        actual_conflicts = []
        for call in plans:
            value = public_result(call)
            conflict_path = "/frames/" + mapping[3] + "/dialogue/text"
            conflicts = [c for c in value.get("conflicts", []) if c.get("path") == conflict_path]
            applied = [a for a in applications if a.get("arguments", {}).get("plan_id") == value.get("plan_id")]
            for conflict in conflicts:
                audit.check("reimport_detected_real_three_way_dialogue_conflict", conflict.get("baseline") == spec["rows"][2]["对白或旁白"]
                            and conflict.get("current") == CHAT_THIRD_TEXT and conflict.get("incoming") == UPDATED_THIRD_TEXT)
                audit.check("reimport_public_resolution_kept_current", bool(applied) and all(a["arguments"].get("resolutions", {}).get(conflict_path) == "current" for a in applied))
                actual_conflicts.append({"plan_id": value["plan_id"], "path": conflict_path, "baseline": conflict.get("baseline"),
                                         "current": conflict.get("current"), "incoming": conflict.get("incoming"), "selection": "current"})
                before_import_path = within(directory, f"history/revisions/{value['expected_revision']:08d}.json")
                before_import = read_json(before_import_path)
                audit.record("chat_state_before_reimport", before_import_path)
                expected_final_semantics = semantic_project(before_import)
                actual_final_semantics = semantic_project(model)
                for item in (expected_final_semantics, actual_final_semantics):
                    item.pop("revision", None)
                    item.get("metadata", {}).pop("source_baselines", None)
                expected_final_semantics["frames"][mapping[12]]["dialogue"]["text"] = UPDATED_END_TEXT
                audit.check("reimport_changed_only_authorized_source_text_and_provenance",
                            actual_final_semantics == expected_final_semantics,
                            {"unexpected_semantic_paths": sorted(difference_paths(expected_final_semantics, actual_final_semantics))})
                first_mutations = [public_result(c).get("revision", 0) for c in good if c.get("_audit_run_index") == 0
                                   and c.get("arguments", {}).get("project_id") == project_id and c["tool"] in {"register_assets", "commit_import", "apply_frame_batch", "patch_frame", "apply_layout", "update_flow"}]
                original_revision = max(first_mutations, default=0)
                original_model_path = within(directory, f"history/revisions/{original_revision:08d}.json")
                original_model = read_json(original_model_path)
                audit.record("original_author_committed_model", original_model_path)
                previous_four = before_import["frames"][mapping[4]]
                final_four = frames[mapping[4]]
                initial_four = original_model["frames"][mapping[4]]
                def ticket_component(frame):
                    return next(c for c in frame["decorations"] if same_asset(c.get("asset_id"), "images/ticket.png"))
                old_ticket, chat_ticket, new_ticket = map(ticket_component, (initial_four, previous_four, final_four))
                audit.check("chat_ticket_moved_right_before_reimport", chat_ticket["layout"]["x"] > old_ticket["layout"]["x"])
                audit.check("reimport_preserved_chat_ticket_layout", new_ticket["component_id"] == chat_ticket["component_id"] == old_ticket["component_id"]
                            and new_ticket["layout"] == chat_ticket["layout"])
                audit.check("reimport_preserved_chat_character_replacement", same_asset(previous_four["characters"][0]["asset_id"], "images/linxi-smile.png")
                            and final_four["characters"][0]["asset_id"] == previous_four["characters"][0]["asset_id"]
                            and final_four["characters"][0]["component_id"] == initial_four["characters"][0]["component_id"])
                audit.check("reimport_kept_stable_project_identities_and_save_namespace", model["save_namespace"] == original_model["save_namespace"]
                            and all(set(model[key]) == set(original_model[key]) for key in ("frames", "acts", "characters", "assets")))
        audit.check("actual_conflict_plan_and_application_exist", bool(actual_conflicts))
        reimport_details = {"conflicts": actual_conflicts, "final_third_text": (frames[mapping[3]].get("dialogue") or {}).get("text"),
                            "final_ending_text": (frames[mapping[12]].get("dialogue") or {}).get("text")}
        restores = [c for c in good if c["tool"] == "restore_checkpoint" and c.get("arguments", {}).get("project_id") == project_id]
        audit.check("reimport_has_real_public_checkpoint_restore", bool(restores))
        for restore in restores:
            arguments, restored_result = restore["arguments"], public_result(restore)
            snapshot_id = arguments.get("snapshot_id")
            if not re.fullmatch(r"snap_[0-9a-f]{16}", str(snapshot_id)):
                raise ValueError("Checkpoint identity must come from a real public restore")
            created_checkpoints = [c for c in good if c["tool"] == "create_checkpoint"
                                   and c.get("arguments", {}).get("project_id") == project_id
                                   and public_result(c).get("snapshot_id") == snapshot_id and good.index(c) < good.index(restore)]
            audit.check("restored_checkpoint_has_prior_public_creation:" + snapshot_id, bool(created_checkpoints))
            if not created_checkpoints:
                continue
            creation = created_checkpoints[-1]
            checkpoint = public_result(creation)
            snapshot_path = within(directory, "checkpoints/" + snapshot_id + ".json")
            saved_checkpoint = read_json(snapshot_path)
            audit.record("restored_checkpoint:" + snapshot_id, snapshot_path)
            before_path = within(directory, f"history/revisions/{checkpoint['revision']:08d}.json")
            temporary_path = within(directory, f"history/revisions/{arguments['expected_revision']:08d}.json")
            restored_path = within(directory, f"history/revisions/{restored_result['revision']:08d}.json")
            before, temporary, restored = map(read_json, (before_path, temporary_path, restored_path))
            for label, path in (("checkpoint", before_path), ("temporary", temporary_path), ("restored", restored_path)):
                audit.record("checkpoint_restore_revision:" + snapshot_id + ":" + label, path)
            audit.check("checkpoint_record_matches_actual_committed_bytes:" + snapshot_id,
                        saved_checkpoint == {key: checkpoint[key] for key in saved_checkpoint}
                        and checkpoint.get("model_hash") == digest(before))
            discovered = [c for c in good if c["tool"] == "inspect_project" and c.get("arguments", {}).get("project_id") == project_id
                          and c.get("arguments", {}).get("section") == "checkpoints"
                          and good.index(creation) < good.index(c) < good.index(restore)
                          and any(item.get("snapshot_id") == snapshot_id and item.get("model_hash") == digest(before)
                                  for item in public_result(c).get("items", []))]
            audit.check("checkpoint_discovered_through_public_inspection_before_restore:" + snapshot_id, bool(discovered))
            changes = difference_paths(before, temporary)
            temporary_text = (temporary["frames"][mapping[3]].get("dialogue") or {}).get("text")
            audit.check("temporary_revision_has_only_the_intended_dialogue_change:" + snapshot_id,
                        changes == {"/revision", "/frames/" + mapping[3] + "/dialogue/text"}
                        and temporary_text != CHAT_THIRD_TEXT and before["frames"][mapping[3]]["dialogue"]["text"] == CHAT_THIRD_TEXT,
                        {"actual_changed_paths": sorted(changes), "temporary_text": temporary_text})
            restored_without_revision, before_without_revision = deepcopy(restored), deepcopy(before)
            restored_without_revision.pop("revision", None)
            before_without_revision.pop("revision", None)
            audit.check("restore_exactly_recovered_checkpoint_content:" + snapshot_id,
                        restored_result.get("restored_snapshot") == snapshot_id
                        and restored["revision"] == temporary["revision"] + 1
                        and restored_without_revision == before_without_revision,
                        {"unexpected_paths": sorted(difference_paths(before_without_revision, restored_without_revision))})
            audit.check("temporary_text_absent_from_final_dialogues:" + snapshot_id,
                        all((frame.get("dialogue") or {}).get("text") != temporary_text for frame in frames.values()))
            checkpoint_restore_details.append({"snapshot_id": snapshot_id, "checkpoint_revision": before["revision"],
                                               "checkpoint_model_hash": digest(before), "temporary_revision": temporary["revision"],
                                               "temporary_text": temporary_text, "restored_revision": restored["revision"],
                                               "restored_model_hash": digest(restored), "restoration_operation_id": arguments.get("operation_id"),
                                               "public_discovery_call_ids": [c["id"] for c in discovered],
                                               "exact_content_recovery_excluding_revision": restored_without_revision == before_without_revision})

    jobs = []
    for call in good:
        if call["tool"] not in JOB_TOOLS or call.get("arguments", {}).get("project_id") != project_id:
            continue
        value = public_result(call)
        job_id = value.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(r"job_[0-9a-f]{32}", job_id):
            audit.check("job_id_from_real_call", False, job_id)
            continue
        status_path = within(home, "jobs/" + job_id + "/status.json")
        status = read_json(status_path)
        report_path = within(home, status["report_path"])
        report = read_json(report_path)
        audit.record("job_status:" + job_id, status_path)
        audit.record("job_report:" + job_id, report_path)
        queries = [public_result(c) for c in good if c["tool"] == "get_job" and c.get("arguments", {}).get("job_id") == job_id]
        identity_missing = any(key not in report for key in ("job_id", "project_id", "revision"))
        progress = None
        if identity_missing and status.get("state") == "failed":
            progress_path = within(home, "jobs/" + job_id + "/pipeline-progress.json")
            progress = read_json(progress_path)
            audit.record("historical_failed_job_identity_progress:" + job_id, progress_path)
        identity_ok = native_job_identity(status, report, call["arguments"], call["tool"], project_id, job_id, progress)
        identity_ok = identity_ok and (not identity_missing or report_path.parent == status_path.parent)
        audit.check("job_identity:" + job_id, identity_ok,
                    {"mode": "historical_failed_thin_report" if identity_missing else "complete_report_identity",
                     "acceptance": "historical_failed_not_current_proof" if identity_missing else "requires_terminal_success_and_candidate_digest_for_current_proof",
                     "report_identity_fields_not_rewritten": True})
        jobs.append({"job_id": job_id, "kind": status["kind"], "revision": status["revision"], "state": status["state"],
                     "report_path": str(report_path), "report": report, "status": status, "queries": queries})
    final_jobs = [j for j in jobs if j["revision"] == revision and j["state"] == "succeeded" and j["report"].get("ok") is True and j["report"].get("status") == "passed"]
    if args.required_generator_version:
        environments = [public_result(c) for c in good if c["tool"] == "get_environment"]
        audit.check("latest_real_plugin_environment_has_required_version", bool(environments)
                    and environments[-1].get("version") == args.required_generator_version,
                    {"required": args.required_generator_version, "latest": environments[-1].get("version") if environments else None})
        final_jobs = [j for j in final_jobs if j["report"].get("generator_version") == args.required_generator_version]
    if args.required_code_digest:
        if not re.fullmatch(r"[0-9a-f]{64}", args.required_code_digest):
            raise ValueError("Required code digest must be an actual 64-digit lowercase SHA256")
        final_jobs = [j for j in final_jobs if j["report"].get("code_digest") == args.required_code_digest]
    required_kinds = ({"check", "preview"} if args.scenario == "chat-modify" else {"check", "routes", "preview"}) | ({"build"} if args.require_build or args.scenario == "reimport" else set())
    audit.check("final_revision_has_all_required_successful_jobs", required_kinds <= {j["kind"] for j in final_jobs},
                {"required": sorted(required_kinds), "accepted": sorted({j["kind"] for j in final_jobs}),
                 "unsuccessful_current_revision_jobs": [{"job_id": j["job_id"], "kind": j["kind"], "state": j["state"],
                                                          "error": j["report"].get("error")}
                                                         for j in jobs if j["revision"] == revision and j["state"] != "succeeded"]})
    all_screenshots = {}
    preview_coverage = set()
    generated_media_paths = []
    for job in final_jobs:
        report, status = job["report"], job["status"]
        jid = job["job_id"]
        audit.check("job_final_state_observed_through_mcp:" + jid, any(q.get("state") == "succeeded" and q.get("revision") == revision for q in job["queries"]))
        audit.check("job_report_model_hash:" + jid, report.get("model_hash") == digest(model))
        audit.check("job_has_real_engine_version_and_code_digest:" + jid, bool(re.fullmatch(r"[0-9a-f]{64}", str(report.get("code_digest", ""))))
                    and bool(report.get("sdk_version")) and bool(report.get("generator_version")) and report.get("layout", {}).get("ok") is True)
        generation = report.get("generation", {})
        manifest_path = within(directory, generation["manifest_path"])
        manifest = read_json(manifest_path)
        audit.record("generation_manifest:" + jid, manifest_path)
        generated_root = within(directory, generation["project_path"])
        audit.check("generated_manifest_matches_native_job:" + jid, manifest.get("project_id") == project_id and manifest.get("revision") == revision
                    and manifest.get("generator_version") == report.get("generator_version") and manifest.get("sdk_version") == report.get("sdk_version"))
        for asset_id, item in manifest.get("assets", {}).items():
            target = within(generated_root / "game", item["game_path"])
            generated_media_paths.append({"job_id": jid, "asset_id": asset_id, "path": str(target), "path_length": len(str(target)), "sha256": sha(target)})
            audit.record("generated_media:" + jid + ":" + asset_id, target)
            audit.check("generated_media_bytes_match_registered_material:" + jid + ":" + asset_id, sha(target) == item["sha256"] == managed_hashes.get(asset_id))
        audit.check("job_real_compile_and_lint:" + jid, all(report.get("commands", {}).get(name, {}).get("exit_code") == 0
                    and not report["commands"][name].get("timed_out") and report["commands"][name].get("argv") for name in ("compile", "lint")))
        if job["kind"] in {"routes", "build"}:
            coverage = report.get("route_coverage", {})
            expected_routes = [[mapping[n] for n in [1, 2, 3, 4, 5, branch, 9, 10, 11, 12]] for branch in (6, 7, 8)]
            actual_routes = [[step["frame_id"] for step in route] for route in coverage.get("routes", [])]
            audit.check("native_three_complete_routes:" + jid, coverage.get("route_count") == 3 and sorted(actual_routes) == sorted(expected_routes)
                        and {tuple(edge) for edge in coverage.get("edges", [])} == expected_edges and set(coverage.get("frame_ids", [])) == set(frames)
                        and coverage.get("entry_mode") == "Start" and coverage.get("complete_for_finite_graph") is True)
            interaction = report.get("interaction_verification", {})
            audit.check("native_interactions_actually_passed:" + jid, status.get("options", {}).get("include_interactions") is True
                        and interaction.get("requested") is True and interaction.get("status") == "passed"
                        and set(interaction.get("cases", [])) == {"save_load_rollback", "history_and_preferences", "auto_forward", "skip"})
        if job["kind"] == "build":
            release = report.get("release", {})
            audit.check("independent_package_verified:" + jid, report.get("independent_release_verified") is True
                        and report.get("interaction_verification", {}).get("independent_package_status") == "passed"
                        and report.get("commands", {}).get("package-tests", {}).get("exit_code") == 0)
            audit_release(audit, report, directory, within(home, "jobs/" + jid), jid)
        for screenshot in report.get("screenshots", []):
            target = within(directory, screenshot["path"])
            audit.check("native_screenshot_hash:" + target.name, sha(target) == screenshot["sha256"] and screenshot.get("revision") == revision)
            all_screenshots[str(target)] = screenshot
            if job["kind"] == "preview":
                preview_coverage.add(number_for(frames[screenshot["frame_id"]]))
    baseline_acceptance = audit_visual_baseline(audit, good, final_jobs, directory, project_id, revision, model,
                                               {mapping[n] for n in (3, 4, 5, 9, 10, 12)}) if args.require_visual_baseline else {"status": "not_run", "reason": "Not required for this historical scope"}
    if args.require_visual_baseline:
        audit.check("this_candidate_visual_baseline_and_default_comparison_verified", baseline_acceptance.get("status") == "passed")
    required_previews = {3, 4} if args.scenario == "chat-modify" else {3, 4, 5, 9, 12} if args.scenario == "reimport" else PREVIEW_NUMBERS
    audit.check("requested_key_frames_have_native_preview", required_previews <= preview_coverage, sorted(preview_coverage))
    visual = {"status": "unverified", "reason": "Native screenshot existence and the author's claim do not prove actual image inspection."}
    if args.visual_review:
        audit.record("external_visual_review", args.visual_review)
        review = read_json(args.visual_review)
        reviewed_numbers = set()
        valid = review.get("project_id") == project_id and review.get("revision") == revision and review.get("status") in {"passed", "failed"} and bool(review.get("reviewer"))
        failed_images = []
        for item in review.get("screenshots", []):
            recorded = all_screenshots.get(str(Path(item.get("path", "")).resolve()))
            valid = valid and recorded is not None and item.get("sha256") == (recorded or {}).get("sha256") and item.get("status") in {"passed", "failed"} and bool(item.get("observation"))
            if recorded:
                reviewed_numbers.add(number_for(frames[recorded["frame_id"]]))
                if item.get("status") == "failed":
                    failed_images.append({"frame_id": recorded["frame_id"], "path": str(Path(item["path"]).resolve()), "observation": item["observation"]})
        audit.check("external_visual_review_matches_native_images", valid and bool(review.get("screenshots")))
        if args.require_visual_baseline:
            comparison_ids = baseline_acceptance.get("comparison_preview_job_ids", [])
            second = next((job for job in final_jobs if comparison_ids and job["job_id"] == comparison_ids[-1]), None)
            second_paths = {str(Path(item["path"]).resolve()) for item in (second or {}).get("report", {}).get("screenshots", [])}
            review_paths = {str(Path(item.get("path", "")).resolve()) for item in review.get("screenshots", [])}
            provenance = review.get("inspection_provenance", {})
            audit.check("independent_six_frame_review_binds_second_comparison_preview",
                        bool(second) and review_paths == second_paths and len(review.get("screenshots", [])) == 6
                        and provenance.get("tool") == "view_image" and provenance.get("direct_independent_review") is True
                        and provenance.get("all_recorded_images_directly_viewed") is True
                        and provenance.get("native_report_sha256") == sha(Path(second["report_path"]))
                        and all(item.get("inspection_origin") == "independent_view_image" for item in review.get("screenshots", [])))
        visual_failed = bool(failed_images) or review.get("status") == "failed"
        audit.check("visual_required_media_and_layout_passed", valid and not visual_failed and required_previews <= reviewed_numbers,
                    {"failed_images": failed_images, "review_diagnostics": review.get("diagnostics", [])})
        if args.require_clean_interlude:
            interlude = [item for item in review.get("screenshots", [])
                         if item.get("frame_number") == 9 and item.get("inspection_origin") == "independent_view_image"]
            restoration = [item for item in review.get("screenshots", [])
                           if item.get("frame_number") == 10 and item.get("inspection_origin") == "independent_view_image"]
            audit.check("strict_background_only_interlude_directly_reviewed", valid and any(
                item.get("status") == "passed" and item.get("no_system_menu_text") is True for item in interlude))
            audit.check("following_dialogue_and_menu_restoration_directly_reviewed", valid and any(
                item.get("status") == "passed" and item.get("system_menu_restored") is True for item in restoration))
        visual = {"status": "passed" if valid and not visual_failed and required_previews <= reviewed_numbers else "failed",
                  "path": str(args.visual_review.resolve()), "reviewed_frames": sorted(reviewed_numbers), "failed_images": failed_images,
                  "diagnostics": review.get("diagnostics", []), "inspection_provenance": review.get("inspection_provenance"),
                  "direct_independent_sample_frames": review.get("direct_independent_sample_frames", [])}
    default_summary = {"original": "evidence/m8-demo/author-summary.json", "reimport": "evidence/m8-demo/followup-summary.json", "equivalent": "evidence/m4-equivalent/author-summary.json", "chat-modify": "evidence/m8-demo/chat-modify-summary.json"}
    summary_path = args.summary or ROOT / default_summary[args.scenario]
    if summary_path.is_file():
        summary = read_json(summary_path)
        audit.record("external_author_summary", summary_path)
        summary_project = summary.get("project", {}) if isinstance(summary.get("project"), dict) else {}
        summary_project_id = summary.get("project_id", summary_project.get("project_id"))
        summary_revision = summary.get("revision", summary.get("final_revision", summary_project.get("revision", summary_project.get("final_revision"))))
        audit.check("external_author_summary_identity", summary_project_id == project_id and summary_revision == revision)
        failed_gates = [gate for gate in summary.get("gates", []) if gate.get("status") == "failed"]
        audit.check("author_summary_has_no_failed_final_acceptance_gates", not failed_gates, failed_gates)
        if failed_gates and visual.get("status") == "unverified":
            visual = {"status": "failed", "source": "author_reported_failure_requires_independent_review", "failed_gates": failed_gates}
    else:
        audit.check("external_author_summary_exists", False, str(summary_path))
    if args.require_clean_interlude and not args.visual_review:
        audit.check("strict_background_only_interlude_directly_reviewed", False, "Needs actual independent image observations")
    environment_versions = sorted({str(public_result(c).get("version")) for c in good if c["tool"] == "get_environment"})
    generator_versions = sorted({str(j["report"].get("generator_version")) for j in jobs})
    sdk_versions = sorted({str(j["report"].get("sdk_version")) for j in jobs})
    metadata_mismatch = set(environment_versions) != set(generator_versions)
    return {"scenario": args.scenario, "project_id": project_id, "production_home": str(home), "revision": revision, "runnable_revision": head.get("runnable_revision"),
            "source_id": source_id, "source_hash": source_hash, "model_hash": digest(model), "frame_number_to_id": mapping,
            "public_schema_tool_count": len(allowed_tools), "real_plugin_call_count": len(plugin_calls), "failed_plugin_call_count": len(plugin_calls)-len(good),
            "author_mcp_lifecycle": call_lifecycle,
            "jobs": [{key: j[key] for key in ("job_id", "kind", "revision", "state", "report_path")} for j in jobs],
            "accepted_final_job_ids": [j["job_id"] for j in final_jobs],
            "required_generator_version": args.required_generator_version,
            "required_code_digest": args.required_code_digest,
            "strict_clean_interlude_required": args.require_clean_interlude,
            "versions": {"environment_reported_plugin": environment_versions, "job_generator": generator_versions, "job_sdk": sdk_versions,
                         "public_schema_snapshot_plugin": schema.get("plugin_version"), "metadata_mismatch": metadata_mismatch,
                         "metadata_mismatch_note": "Reported plugin metadata differs from the actual job generator; native acceptance uses the job's model hash, code digest, commands and persisted reports." if metadata_mismatch else None},
            "reimport_acceptance": reimport_details,
            "checkpoint_restore_acceptance": checkpoint_restore_details,
            "cold_source_acceptance": cold_source_acceptance,
            "visual_baseline_acceptance": baseline_acceptance,
            "chat_modify_acceptance": chat_details,
            "generated_media": {"files": generated_media_paths, "max_absolute_path_length": max((p["path_length"] for p in generated_media_paths), default=0),
                                "path_length_is_diagnostic_only": True,
                                "file_exists_hash_and_layer_tags_do_not_prove_native_media_loaded": True},
            "visual_review": visual, "audio_file_binding": "checked", "system_audio_output": "unverified",
            "desktop_current_chat_tools": "unverified", "shell_review": shell_reviews,
            "all_shell_programs_automatically_proven_read_only": all(r["classification"] == "read_only" for r in shell_reviews)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--author-run", type=Path, action="append", required=True, help="Unique author run directory, its report.json, or latest.json; repeat for a continuation")
    parser.add_argument("--scenario", choices=("original", "reimport", "equivalent", "chat-modify"), default="original")
    parser.add_argument("--bridge-home", type=Path, help="Optional; must equal the real get_environment result")
    parser.add_argument("--project-id")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--workbook", type=Path, default=ROOT / "outputs/formal-demo/demo-materials.xlsx")
    parser.add_argument("--updated-workbook", type=Path, default=ROOT / "outputs/formal-demo/demo-materials-updated.xlsx")
    parser.add_argument("--text-source", type=Path, default=ROOT / "evidence/m4-equivalent/materials.txt")
    parser.add_argument("--materials", type=Path, default=ROOT / "evidence/m8-demo/materials")
    parser.add_argument("--input-spec", type=Path, default=ROOT / "evidence/m8-demo/materials/story-spec.json")
    parser.add_argument("--tool-schema", type=Path, default=ROOT / "docs/tools.schema.json")
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--allowed-external-summary", type=Path, action="append", default=[],
                        help="Explicit additional external evidence summary written by a prior included author run; repeat as needed")
    parser.add_argument("--visual-review", type=Path, help="Independent actual screenshot-review record; absence stays unverified")
    parser.add_argument("--required-generator-version", help="Only jobs from this actual generator version may satisfy final acceptance")
    parser.add_argument("--required-code-digest", help="Only jobs whose native report has this exact actual code SHA256 may satisfy final acceptance")
    parser.add_argument("--require-clean-interlude", action="store_true", help="Require directly viewed frame 9 without menu text and frame 10 menu restoration")
    parser.add_argument("--require-visual-baseline", action="store_true", help="Require actual explicit baseline registration/retry and a second native preview with unchanged baseline and zero actual pixel difference")
    parser.add_argument("--require-build", action="store_true")
    parser.add_argument("--output-root", type=Path, default=ROOT / "evidence/m8-demo/verification")
    args = parser.parse_args()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.output_root.resolve() / "runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    audit = Audit()
    audit.record("verifier_implementation", Path(__file__).resolve())
    error = None
    result = {}
    try:
        result = verify(args, audit)
    except Exception as exc:
        error = {"type": type(exc).__name__, "message": str(exc)}
        audit.check("verification_completed_without_missing_or_corrupt_evidence", False, error)
    snapshot_root = output / "inputs"
    snapshot_root.mkdir()
    for name, record in audit.inputs.items():
        original = Path(record["path"])
        suffix = original.suffix.lower() if re.fullmatch(r"\.[a-z0-9]+", original.suffix.lower()) else ".bin"
        target = snapshot_root / (record["sha256"] + suffix)
        try:
            if not target.exists():
                with original.open("rb") as source, target.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
            preserved = sha(target) == record["sha256"]
            audit.check("input_snapshot_matches_verified_bytes:" + name, preserved)
            record["immutable_snapshot_path"] = str(target)
        except OSError as exc:
            audit.check("input_snapshot_matches_verified_bytes:" + name, False, str(exc))
    failed = [c for c in audit.checks if c["status"] == "failed"]
    report = {"scope": "formal_demo_read_only_authoring_and_native_job_acceptance", "run_id": run_id,
              "created_at": datetime.now(timezone.utc).isoformat(), "status": "failed" if failed else "passed",
              "input_evidence": audit.inputs, "checks": audit.checks, "failed_check_count": len(failed), "error": error,
              "engine_or_model_mutated": False, "other_unverified_gates_do_not_become_passed": True, **result}
    report_path = output / "report.json"
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    visual = result.get("visual_review", {"status": "unverified"})
    visual_summary = {key: visual[key] for key in ("status", "path", "reviewed_frames", "direct_independent_sample_frames") if key in visual}
    print(json.dumps({"status": report["status"], "report": str(report_path), "failed_check_count": len(failed),
                      "visual_review": visual_summary}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
