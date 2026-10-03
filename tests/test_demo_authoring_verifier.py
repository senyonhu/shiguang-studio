"""Read-only evidence boundaries and native pixels, without an engine/model stub."""
import hashlib
import json
from pathlib import Path
import stat
import zipfile

import pytest

from tools.verify_demo_authoring import (Audit, archive_members, audit_shell, audit_content_operations, audit_visual_baseline,
                                        native_job_identity, source_substitute_mentioned,
                                        native_renderer_from_log, png_capture_resolution, same_rgba_pixels)


def make_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "evidence").mkdir(parents=True)
    return workspace


@pytest.mark.parametrize("path_kind", ["outside", "traversal", "similar_prefix"])
def test_declared_summary_rejects_external_resolved_paths_without_reading_bytes(tmp_path, path_kind):
    workspace = make_workspace(tmp_path)
    if path_kind == "similar_prefix":
        target = workspace / "evidence-extra" / "summary.json"
        supplied = target
    else:
        target = tmp_path / "summary.json"
        supplied = target if path_kind == "outside" else workspace / "evidence" / ".." / ".." / "summary.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    content = b'{"outside_evidence": true}\n'
    target.write_bytes(content)
    audit = Audit()
    with pytest.raises(ValueError, match="inside workspace evidence"):
        audit_shell(audit, [], workspace, set(), workspace / "project", additional_summary_paths=[supplied])
    assert audit.inputs == {}
    assert target.read_bytes() == content


def test_explicit_summary_authorizes_only_that_file_and_records_actual_hash(tmp_path):
    workspace = make_workspace(tmp_path)
    approved = workspace / "evidence" / "approved.json"
    unapproved = workspace / "evidence" / "other.json"
    approved.write_bytes(b'{"explicit_summary": true}\n')
    unapproved.write_bytes(b'{"different_file": true}\n')
    audit = Audit()
    events = [
        {"type": "file_change", "id": "approved", "changes": [{"path": str(approved)}]},
        {"type": "file_change", "id": "unapproved", "changes": [{"path": str(unapproved)}]},
    ]
    audit_shell(audit, events, workspace, set(), workspace / "project", additional_summary_paths=[approved])
    checks = [check for check in audit.checks if check["check"] == "author_file_change_only_external_summary"]
    assert [(check["details"]["id"], check["status"]) for check in checks] == [("approved", "passed"), ("unapproved", "failed")]
    record = next(iter(audit.inputs.values()))
    assert Path(record["path"]) == approved.resolve()
    assert record["sha256"] == hashlib.sha256(approved.read_bytes()).hexdigest()
    assert all(Path(item["path"]) != unapproved.resolve() for item in audit.inputs.values())


@pytest.mark.parametrize("member", ["../outside.txt", "/absolute.txt", "C:/outside.txt", "game\\outside.txt", "game/../outside.txt"])
def test_release_zip_rejects_unsafe_paths_without_extracting(tmp_path, member):
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(member, b"untrusted bytes")
    if "\\" in member:
        # ZipInfo normalizes Windows separators when writing. Restore the raw
        # archive member spelling in both headers to exercise hostile input.
        package.write_bytes(package.read_bytes().replace(member.replace("\\", "/").encode(), member.encode()))
    original = package.read_bytes()
    with pytest.raises(ValueError, match="unsafe member path"):
        archive_members(package)
    assert package.read_bytes() == original
    assert list(tmp_path.iterdir()) == [package]


def test_release_zip_rejects_windows_case_collision(tmp_path):
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("game/voice.wav", b"one")
        archive.writestr("game/VOICE.wav", b"different")
    with pytest.raises(ValueError, match="colliding member paths"):
        archive_members(package)
    assert list(tmp_path.iterdir()) == [package]


def test_release_zip_rejects_link_members(tmp_path):
    package = tmp_path / "package.zip"
    link = zipfile.ZipInfo("game/asset")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(link, "../../outside")
    with pytest.raises(ValueError, match="unsafe member path"):
        archive_members(package)
    assert list(tmp_path.iterdir()) == [package]


def test_native_pixel_comparison_accepts_same_pixels_with_different_png_compression(tmp_path):
    from PIL import Image
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    image = Image.new("RGBA", (17, 9), (23, 45, 67, 89))
    image.save(first, compress_level=0)
    image.save(second, compress_level=9)
    image.close()
    assert first.read_bytes() != second.read_bytes()
    before = {path: path.read_bytes() for path in (first, second)}
    assert same_rgba_pixels(first, second)
    assert {path: path.read_bytes() for path in (first, second)} == before


@pytest.mark.parametrize("channel", [0, 1, 2, 3])
def test_native_pixel_comparison_rejects_a_single_changed_channel_including_alpha(tmp_path, channel):
    from PIL import Image
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    image = Image.new("RGBA", (4, 3), (11, 22, 33, 44))
    image.save(first)
    pixel = list(image.getpixel((2, 1)))
    pixel[channel] += 1
    image.putpixel((2, 1), tuple(pixel))
    image.save(second)
    image.close()
    assert not same_rgba_pixels(first, second)


def test_physical_capture_is_read_without_logical_canvas_resizing(tmp_path):
    from PIL import Image
    capture, logical = tmp_path / "native.png", tmp_path / "logical.png"
    for path, size in ((capture, (1738, 977)), (logical, (1920, 1080))):
        image = Image.new("RGB", size, (100, 120, 140))
        image.save(path)
        image.close()
    before = capture.read_bytes()
    assert png_capture_resolution(capture) == [1738, 977]
    assert png_capture_resolution(logical) == [1920, 1080]
    assert not same_rgba_pixels(capture, logical)
    assert capture.read_bytes() == before


def test_capture_dimensions_reject_other_image_formats(tmp_path):
    from PIL import Image
    path = tmp_path / "claim.png"
    image = Image.new("RGB", (4, 3))
    image.save(path, format="JPEG")
    image.close()
    with pytest.raises(ValueError, match="actual PNG"):
        png_capture_resolution(path)


def test_renderer_parser_reads_actual_sdk_log_lines_and_ignores_unrelated_version(tmp_path):
    log = tmp_path / "engine-log.txt"
    log.write_text("Ren'Py 8.5.2\nVersion 8.5.2\nInitializing gl2 renderer:\nVendor: NVIDIA Corporation\nRenderer: NVIDIA GeForce RTX\nVersion: 4.6.0 NVIDIA 591.11\n", encoding="utf-8")
    original = log.read_bytes()
    assert native_renderer_from_log(log.read_text(encoding="utf-8")) == {
        "backend": "gl2", "vendor": "NVIDIA Corporation", "renderer": "NVIDIA GeForce RTX", "version": "4.6.0 NVIDIA 591.11"}
    assert log.read_bytes() == original


def test_missing_native_renderer_evidence_does_not_infer_backend_or_version():
    assert native_renderer_from_log("Ren'Py 8.5.2\nVendor: NVIDIA Corporation\n") == {
        "backend": None, "vendor": "NVIDIA Corporation", "renderer": None, "version": None}


def test_visual_operation_cannot_be_misclassified_as_content_commit_or_skip_its_index(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    head_path = project / "head.json"
    head_path.write_text(json.dumps({"revision": 12, "operations": {}}), encoding="utf-8")
    original = head_path.read_bytes()
    head = json.loads(original)
    arguments = {"project_id": "p_" + "a" * 20, "operation_id": "reviewed_visual",
                 "expected_revision": 12, "preview_job_id": "job_" + "b" * 32,
                 "review_note": "Direct original PNG review", "frame_ids": ["frame_09"]}
    result = {"baseline_id": "visual_" + "c" * 24, "revision": 12, "content_revision_changed": False,
              "capture_resolution_verified": True, "manifest_path": str(project / "visual_baselines/manifest.json")}
    call = {"id": "first", "tool": "register_visual_baseline", "arguments": arguments,
            "result": {"structuredContent": result}}
    retry = {**call, "id": "retry"}
    audit = Audit()
    audit_content_operations(audit, [call, retry], head, {12}, arguments["project_id"])
    assert audit.checks == []  # It cannot require a nonexistent content commit.
    assert json.loads(head_path.read_bytes()) == head == {"revision": 12, "operations": {}}
    # Public idempotent replies alone cannot stand in for the dedicated index.
    with pytest.raises(FileNotFoundError):
        audit_visual_baseline(audit, [call, retry], [], project, arguments["project_id"], 12,
                              {"revision": 12}, {"frame_09"})
    assert head_path.read_bytes() == original
    assert list(project.iterdir()) == [head_path]
    mutation = {**call, "tool": "patch_frame"}
    audit_content_operations(audit, [mutation], head, {12}, arguments["project_id"])
    assert audit.checks[-1]["status"] == "failed"  # Other content operations stay strict.


@pytest.mark.parametrize("command,blocked", [
    ("rg --glob '!story-spec.json' --glob '!voice-lines.json' visual .", False),
    ('pwsh -Command "rg -n --glob \'!**/story-spec.json\' --glob=\'!**/voice-lines.json\' visual src tests"', False),
    ('pwsh -Command "rg --glob \'\"\'!story-spec.json\'\"\' visual ."', False),
    ("rg --glob '!story-spec.json' . story-spec.json", True),
    ("rg --glob '!story-spec.json' .; Get-Content story-spec.json", True),
    ("rg --glob '!story-spec.json' --glob 'story-spec.json' .", True),
    ("rg --glob '!**/voice-lines.json' . voice-lines.json", True),
    ("Get-Content story-spec.json", True),
    ("Get-Content --glob '!story-spec.json'", True),
    ("rg --glob '!story-spec.json.backup' .", True),
    ("rg --glob '!story-spec.json' probe_pipeline .", True),
])
def test_negative_rg_glob_exempts_only_its_token_not_explicit_input_reads(command, blocked):
    assert source_substitute_mentioned(command) is blocked


def test_failed_thin_job_is_bound_to_real_status_and_progress_without_rewriting(tmp_path):
    job, project = "job_" + "a" * 32, "p_" + "b" * 20
    status = {"job_id": job, "project_id": project, "revision": 12, "kind": "build", "state": "failed",
              "error": {"code": "PACKAGE_RUNTIME_MUTATION", "details": {}}}
    report = {"status": "failed", "error": status["error"]}
    progress = {"job_id": job, "project_id": project, "revision": 12, "status": "running", "ok": False}
    paths = [tmp_path / (name + ".json") for name in ("status", "report", "pipeline-progress")]
    for path, value in zip(paths, (status, report, progress)):
        path.write_text(json.dumps(value), encoding="utf-8")
    original = {path: path.read_bytes() for path in paths}
    actual = [json.loads(path.read_bytes()) for path in paths]
    assert native_job_identity(actual[0], actual[1], {"project_id": project, "revision": 12}, "build_release", project, job, actual[2])
    assert "revision" not in actual[1] and "code_digest" not in actual[1] and "job_id" not in actual[1]
    assert {path: path.read_bytes() for path in paths} == original


@pytest.mark.parametrize("changed", ["success_status", "success_report", "true_ok", "contradictory_job", "different_error", "wrong_progress", "missing_progress", "wrong_start_revision"])
def test_failed_thin_job_exception_rejects_success_or_unrelated_identity(changed):
    job, project = "job_" + "a" * 32, "p_" + "b" * 20
    status = {"job_id": job, "project_id": project, "revision": 12, "kind": "build", "state": "failed",
              "error": {"code": "PACKAGE_RUNTIME_MUTATION"}}
    report = {"status": "failed", "error": {"code": "PACKAGE_RUNTIME_MUTATION"}}
    progress = {"job_id": job, "project_id": project, "revision": 12, "ok": False}
    arguments = {"project_id": project, "revision": 12}
    if changed == "success_status":
        status["state"] = "succeeded"
    elif changed == "success_report":
        report["status"] = "passed"
    elif changed == "true_ok":
        report["ok"] = True
    elif changed == "contradictory_job":
        report["job_id"] = "job_" + "f" * 32
    elif changed == "different_error":
        report["error"]["code"] = "OTHER_ERROR"
    elif changed == "wrong_progress":
        progress["project_id"] = "p_" + "c" * 20
    elif changed == "missing_progress":
        progress = None
    elif changed == "wrong_start_revision":
        arguments["revision"] = 11
    assert not native_job_identity(status, report, arguments, "build_release", project, job, progress)


def test_successful_job_cannot_use_thin_failure_exception_or_borrow_another_revision():
    job, project = "job_" + "a" * 32, "p_" + "b" * 20
    status = {"job_id": job, "project_id": project, "revision": 12, "kind": "build", "state": "succeeded"}
    report = {"job_id": job, "project_id": project, "revision": 11, "status": "passed", "ok": True}
    progress = {"job_id": job, "project_id": project, "revision": 12, "ok": False}
    assert not native_job_identity(status, report, {"project_id": project, "revision": 12}, "build_release", project, job, progress)
