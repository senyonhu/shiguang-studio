"""Real PNG and commit-boundary tests, with synthetic native-job metadata.

These tests verify baseline contracts, not actual SDK rendering. The native
integration is separately exercised through public render_preview jobs.
"""
from copy import deepcopy
from pathlib import Path
import os
import subprocess
import uuid

from PIL import Image, ImageDraw
import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import Store, atomic_json, digest, file_hash, read_json
import renpy_bridge.visual_regression as visual


@pytest.fixture
def story(tmp_path):
    store = Store(tmp_path / "中文制作")
    pid = store.create("已审画面", "create", width=320, height=240)["project_id"]
    store.apply_batch(pid, 0, "frames", [
        {"frame_id": "Opening-1", "act_id": "act_one", "order": 0,
         "dialogue": {"text": "请审查人物位置和对白。"},
         "flow": {"kind": "next", "target_frame_id": "ending"}},
        {"frame_id": "ending", "act_id": "act_one", "order": 1,
         "dialogue": {"text": "结束画面"}, "flow": {"kind": "end"}},
    ], acts=[{"act_id": "act_one", "name": "第一幕"}])
    return store, pid


def preview(story, *, move_character=False, hide_dialogue=False, renderer="QA GPU",
            font=b"fixture font bytes, not an actual font", sdk="8.5.3-test", alpha=255,
            capture_resolution=(320, 240)):
    """Persist a complete fixture report and honest real PNG/hash evidence."""
    store, pid = story
    project = store.load(pid)
    job_id = "job_" + uuid.uuid4().hex
    target = store.directory(pid) / f"generated/r{project.revision:08d}/{job_id}"
    screenshot_dir = target / "tests/screenshots"
    screenshot_dir.mkdir(parents=True)
    (target / "game").mkdir()
    font_path = target / "game/SourceHanSansLite.ttf"
    font_path.write_bytes(font)
    atomic_json(target / "bridge-manifest.json", {
        "project_id": pid, "revision": project.revision, "sdk_version": sdk,
        "chinese_font_sha256": file_hash(font_path),
    })
    screenshots = []
    for frame in project.frames:
        image = Image.new("RGBA", capture_resolution, (20, 40, 60, alpha))
        draw = ImageDraw.Draw(image)
        x = 180 if move_character else 45
        draw.rectangle((x, 30, x + 40, 180), fill=(230, 120, 60, alpha))
        if not hide_dialogue:
            draw.rectangle((10, 190, 309, 230), fill=(245, 245, 245, alpha))
            draw.text((20, 200), "visible dialogue " + frame, fill=(0, 0, 0, alpha))
        path = screenshot_dir / f"native_{digest(frame)[:24]}.png"
        image.save(path)
        image.close()
        screenshots.append({"frame_id": frame, "revision": project.revision,
                            "path": str(path), "sha256": file_hash(path)})
    report = {"job_id": job_id, "kind": "preview", "project_id": pid,
              "revision": project.revision, "model_hash": digest(project.model_dump(mode="json")),
              "ok": True, "status": "passed", "sdk_version": sdk, "generator_version": "0.1.3",
              "code_digest": "a" * 64, "generation": {"project_path": str(target)},
              "commands": {name: {"exit_code": 0, "timed_out": False, "cleanup_complete": True}
                           for name in ("compile", "lint", "native-tests")},
              "screenshots": screenshots,
              "visual_environment": {"sdk_version": sdk, "language": project.language,
                  "renderer": {"backend": "gl2", "vendor": "QA", "renderer": renderer, "version": "4.6"},
                  "resolution": [320, 240], "capture_resolution": list(capture_resolution),
                  "font_sha256": file_hash(font_path)}}
    job_dir = store.home / "jobs" / job_id
    job_dir.mkdir(parents=True)
    atomic_json(job_dir / "status.json", {"job_id": job_id, "kind": "preview", "project_id": pid,
        "revision": project.revision, "state": "succeeded"})
    atomic_json(job_dir / "report.json", report)
    return report


def register(story, report, operation="review-first", **kwargs):
    store, pid = story
    return visual.register_visual_baseline(store, pid, report["revision"], operation,
        report["job_id"], kwargs.pop("review_note", "已逐张审查人物及对白，首次登记此画面。"), **kwargs)


def compare(story, report):
    store, pid = story
    return visual.compare_preview(store, store.load(pid), report)


def persist(story, report):
    atomic_json(story[0].home / "jobs" / report["job_id"] / "report.json", report)


def assert_error(code, action):
    with pytest.raises(BridgeError) as raised:
        action()
    assert raised.value.code == code


def test_first_preview_is_unreviewed_and_registration_is_durable_without_story_revision(story):
    store, pid = story
    report = preview(story)
    head = store.directory(pid) / "model/head.json"
    original_head = head.read_bytes()
    result = compare(story, report)
    assert result["status"] == "not_run" and result["comparisons"] == []
    assert set(result["unregistered_frame_ids"]) == {"Opening-1", "ending"}
    accepted = register(story, report)
    assert accepted["content_revision_changed"] is False
    assert head.read_bytes() == original_head
    assert store.load(pid).revision == report["revision"]
    reopened = (Store(store.home), pid)
    assert register(reopened, report) == accepted
    later = compare(reopened, preview(reopened))
    assert later["ok"] and later["status"] == "passed"
    assert all(record["metrics"]["changed_pixels"] == 0 for record in later["comparisons"])
    assert_error("IDEMPOTENCY_CONFLICT", lambda: register(story, report, review_note="已审查并希望更改首次登记理由。"))


@pytest.mark.parametrize("change", [{"move_character": True}, {"hide_dialogue": True}, {"alpha": 100}])
def test_bad_layout_or_missing_dialogue_produces_actual_diff_then_requires_review_to_update(story, change):
    original = preview(story)
    accepted = register(story, original)
    old_manifest = Path(accepted["manifest_path"])
    before = {p: p.read_bytes() for p in old_manifest.parent.rglob("*") if p.is_file()}
    index = story[0].directory(story[1]) / "visual_baselines/index.json"
    original_index = index.read_bytes()
    changed = preview(story, **change)
    result = compare(story, changed)
    assert result["status"] == "failed" and not result["ok"] and not result["baseline_updated"]
    assert index.read_bytes() == original_index
    for record in result["comparisons"]:
        assert record["metrics"]["changed_pixels"] > 0 and record["metrics"]["bbox"]
        assert Path(record["actual_copy_path"]).read_bytes() == Path(record["actual_path"]).read_bytes()
        assert file_hash(Path(record["diff_path"])) == record["diff_sha256"]
        assert read_json(Path(record["metrics_path"])) == record["metrics"]
        with Image.open(record["diff_path"]) as diff:
            assert diff.size == (320, 240) and diff.getbbox() is not None
    reviewed = register(story, changed, "review-update", review_note="已审查改变后的画面，因剧情设计调整人物和对白而更新。")
    assert reviewed["baseline_id"] != accepted["baseline_id"]
    assert all(p.read_bytes() == data for p, data in before.items())
    assert compare(story, preview(story, **change))["status"] == "passed"
    assert len(read_json(index)["baselines"]) == 2


@pytest.mark.parametrize("environment", [{"renderer": "Different GPU"}, {"font": b"different actual font"}, {"sdk": "8.6-test"}])
def test_environment_changes_block_comparison_without_pretending_visual_failure(story, environment):
    register(story, preview(story))
    result = compare(story, preview(story, **environment))
    assert not result["ok"] and result["status"] == "blocked"
    assert all(r["code"] == "VISUAL_ENVIRONMENT_CHANGED" and r["differing_fields"] for r in result["comparisons"])
    assert not any("diff_path" in r for r in result["comparisons"])


def test_partial_baseline_does_not_claim_whole_preview_pass(story):
    report = preview(story)
    register(story, report, frame_ids=["Opening-1"])
    result = compare(story, preview(story))
    assert result["status"] == "not_run" and result["unregistered_frame_ids"] == ["ending"]
    assert result["comparisons"][0]["status"] == "passed"


@pytest.mark.parametrize("state,kind", [("running", "preview"), ("failed", "preview"), ("succeeded", "routes")])
def test_incomplete_or_wrong_kind_job_cannot_be_a_reviewed_baseline(story, state, kind):
    report = preview(story)
    path = story[0].home / "jobs" / report["job_id"] / "status.json"
    status = read_json(path)
    status.update(state=state, kind=kind)
    atomic_json(path, status)
    assert_error("INVALID_VISUAL_PREVIEW", lambda: register(story, report))
    assert not (story[0].directory(story[1]) / "visual_baselines/index.json").exists()


def test_stale_preview_and_forged_model_hash_cannot_register(story):
    report = preview(story)
    forged = deepcopy(report)
    forged["model_hash"] = "b" * 64
    persist(story, forged)
    assert_error("INVALID_VISUAL_PREVIEW", lambda: register(story, forged))
    persist(story, report)
    story[0].patch_frame(story[1], "ending", {"dialogue": {"text": "新的结束"}}, 1, "edit")
    assert_error("VERSION_CONFLICT", lambda: register(story, report))


@pytest.mark.parametrize("frame_ids", [[], ["ending", "ending"], ["missing"]])
def test_invalid_selection_is_refused(story, frame_ids):
    report = preview(story)
    code = "FRAME_NOT_PREVIEWED" if frame_ids == ["missing"] else "INVALID_VISUAL_FRAMES"
    assert_error(code, lambda: register(story, report, frame_ids=frame_ids))


def test_review_note_is_required_and_all_pngs_are_checked_even_when_unselected(story):
    report = preview(story)
    assert_error("VISUAL_REVIEW_REQUIRED", lambda: register(story, report, review_note=""))
    other = next(entry for entry in report["screenshots"] if entry["frame_id"] == "ending")
    Path(other["path"]).write_bytes(b"damaged PNG after preview")
    assert_error("VISUAL_IMAGE_CHANGED", lambda: register(story, report, frame_ids=["Opening-1"]))


def test_outside_png_and_generated_target_paths_are_refused(story, tmp_path):
    report = preview(story)
    outside = tmp_path / "outside.png"
    outside.write_bytes(Path(report["screenshots"][0]["path"]).read_bytes())
    report["screenshots"][0]["path"] = str(outside)
    persist(story, report)
    assert_error("INVALID_VISUAL_PATH", lambda: register(story, report))
    other = preview(story)
    other["generation"]["project_path"] = str(tmp_path)
    persist(story, other)
    assert_error("INVALID_VISUAL_PATH", lambda: register(story, other, "outside-target"))


def test_missing_or_tampered_font_and_incomplete_renderer_are_refused(story):
    report = preview(story)
    font = Path(report["generation"]["project_path"]) / "game/SourceHanSansLite.ttf"
    font.write_bytes(b"changed without manifest")
    assert_error("VISUAL_FONT_CHANGED", lambda: register(story, report))
    other = preview(story)
    del other["visual_environment"]["renderer"]["vendor"]
    persist(story, other)
    assert_error("INVALID_VISUAL_ENVIRONMENT", lambda: register(story, other, "incomplete-renderer"))


def test_image_with_correct_hash_but_wrong_resolution_is_not_valid_evidence(story):
    report = preview(story)
    path = Path(report["screenshots"][0]["path"])
    with Image.new("RGB", (319, 240)) as image:
        image.save(path)
    report["screenshots"][0]["sha256"] = file_hash(path)
    persist(story, report)
    assert_error("INVALID_VISUAL_IMAGE", lambda: register(story, report))


def test_failed_atomic_index_commit_is_recoverable_by_same_operation_after_reopen(story, monkeypatch):
    report = preview(story)
    store, pid = story
    head = store.directory(pid) / "model/head.json"
    original = head.read_bytes()
    real_atomic = visual.atomic_json
    def fail_index(path, value):
        if path.name == "index.json":
            raise OSError("injected disk full before baseline index commit")
        return real_atomic(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(visual, "atomic_json", fail_index)
        with pytest.raises(OSError, match="injected"):
            register(story, report)
    assert head.read_bytes() == original
    assert not (store.directory(pid) / "visual_baselines/index.json").exists()
    assert len(list((store.directory(pid) / "visual_baselines/runs").iterdir())) == 1
    reopened = (Store(store.home), pid)
    accepted = register(reopened, report)
    assert register(reopened, report) == accepted
    assert compare(reopened, preview(reopened))["status"] == "passed"
    assert len(list((store.directory(pid) / "visual_baselines/runs").iterdir())) == 1


def test_baseline_tampering_and_invalid_active_index_hard_fail(story):
    accepted = register(story, preview(story))
    manifest = read_json(Path(accepted["manifest_path"]))
    base = story[0].directory(story[1]) / "visual_baselines"
    image_path = base / manifest["frames"]["ending"]["image_path"]
    original = image_path.read_bytes()
    image_path.write_bytes(b"modified reviewed PNG")
    assert_error("VISUAL_IMAGE_CHANGED", lambda: compare(story, preview(story)))
    image_path.write_bytes(original)
    index = read_json(base / "index.json")
    index["active_frames"]["ending"] = {"not": "a valid identity"}
    atomic_json(base / "index.json", index)
    assert_error("VISUAL_BASELINE_CORRUPT", lambda: compare(story, preview(story)))


def test_missing_native_report_is_a_readable_bridge_error(story):
    report = preview(story)
    (story[0].home / "jobs" / report["job_id"] / "report.json").unlink()
    assert_error("INVALID_VISUAL_PREVIEW", lambda: register(story, report))


def test_windows_reserved_frame_id_has_portable_baseline_and_diff_paths(tmp_path):
    store = Store(tmp_path / "reserved-frame")
    pid = store.create("stable CON frame", "create", width=320, height=240)["project_id"]
    store.apply_batch(pid, 0, "con-frame", [
        {"frame_id": "CON", "act_id": "act_one", "order": 0,
         "dialogue": {"text": "合法帧身份"}, "flow": {"kind": "end"}},
    ], acts=[{"act_id": "act_one", "name": "第一幕"}])
    fixture = (store, pid)
    accepted = register(fixture, preview(fixture))
    manifest = read_json(Path(accepted["manifest_path"]))
    assert manifest["frames"]["CON"]["image_path"].split("/")[-1].startswith("frame_")
    assert compare(fixture, preview(fixture))["status"] == "passed"
    result = compare(fixture, preview(fixture, hide_dialogue=True))
    assert result["status"] == "failed" and Path(result["comparisons"][0]["diff_path"]).is_file()


def test_non_native_success_claim_without_successful_sdk_commands_cannot_register(story):
    report = preview(story)
    del report["commands"]["native-tests"]
    persist(story, report)
    assert_error("INVALID_VISUAL_PREVIEW", lambda: register(story, report))
    report = preview(story)
    report["commands"]["native-tests"]["timed_out"] = True
    persist(story, report)
    assert_error("INVALID_VISUAL_PREVIEW", lambda: register(story, report, "timed-out"))


def test_baseline_manifest_pointer_cannot_escape_managed_directory(story, tmp_path):
    accepted = register(story, preview(story))
    base = story[0].directory(story[1]) / "visual_baselines"
    outside = tmp_path / "outside-manifest.json"
    outside.write_bytes(Path(accepted["manifest_path"]).read_bytes())
    index = read_json(base / "index.json")
    index["baselines"][accepted["baseline_id"]]["manifest_path"] = str(outside)
    atomic_json(base / "index.json", index)
    assert_error("INVALID_VISUAL_PATH", lambda: compare(story, preview(story)))


@pytest.mark.parametrize("linked_area", ["screenshots", "baseline_index", "comparison_evidence"])
def test_real_junction_is_refused_without_reading_or_writing_external_tree(story, tmp_path, linked_area):
    first = preview(story)
    if linked_area == "screenshots":
        linked = Path(first["screenshots"][0]["path"]).parent
        # Keep the same hashes in the target to prove that equality cannot
        # excuse traversal through a junction or an in-root symlink.
        outside = tmp_path / "outside-screenshots"
        linked.rename(outside)
        action = lambda: register(story, first)
    elif linked_area == "baseline_index":
        register(story, first)
        linked = story[0].directory(story[1]) / "visual_baselines"
        outside = tmp_path / "outside-baselines"
        linked.rename(outside)
        second = preview(story)
        action = lambda: compare(story, second)
    else:
        register(story, first)
        second = preview(story, hide_dialogue=True)
        linked = story[0].home / "jobs" / second["job_id"] / "visual-regression"
        outside = tmp_path / "outside-evidence"
        outside.mkdir()
        action = lambda: compare(story, second)
    sentinel = outside / "do-not-write.txt"
    sentinel.write_bytes(b"external sentinel")
    before = {p.relative_to(outside).as_posix(): p.read_bytes() for p in outside.rglob("*") if p.is_file()}
    if os.name == "nt":
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(linked), str(outside)], capture_output=True)
        assert created.returncode == 0, created.stderr
    else:
        linked.symlink_to(outside, target_is_directory=True)
    try:
        assert_error("INVALID_VISUAL_PATH", action)
        assert {p.relative_to(outside).as_posix(): p.read_bytes() for p in outside.rglob("*") if p.is_file()} == before
    finally:
        # Remove only the link itself; never recursively remove either tree.
        linked.rmdir() if os.name == "nt" else linked.unlink()


def test_smaller_native_capture_than_logical_canvas_registers_compares_and_reopens_without_resize(story):
    first = preview(story, capture_resolution=(280, 210))
    before = {entry["path"]: Path(entry["path"]).read_bytes() for entry in first["screenshots"]}
    accepted = register(story, first)
    assert accepted["capture_resolution_verified"] is True
    manifest = read_json(Path(accepted["manifest_path"]))
    assert manifest["environment"]["resolution"] == [320, 240]
    assert manifest["environment"]["capture_resolution"] == [280, 210]
    reopened = (Store(story[0].home), story[1])
    assert register(reopened, first) == accepted
    assert compare(reopened, preview(reopened, capture_resolution=(280, 210)))["status"] == "passed"
    assert all(Path(path).read_bytes() == data for path, data in before.items())
    changed = preview(reopened, hide_dialogue=True, capture_resolution=(280, 210))
    result = compare(reopened, changed)
    assert result["status"] == "failed"
    for record in result["comparisons"]:
        assert record["metrics"]["total_pixels"] == 280 * 210
        with Image.open(record["diff_path"]) as image:
            assert image.size == (280, 210)
    resized_capture = compare(reopened, preview(reopened, capture_resolution=(300, 225)))
    assert resized_capture["status"] == "blocked"
    assert all("capture_resolution" in r["differing_fields"] for r in resized_capture["comparisons"])


@pytest.mark.parametrize("size", [None, [], [320], [True, 240], [0, 240], [-1, 240], ["320", 240], [999999, 240], [16384, 16384]])
def test_invalid_or_unreasonable_capture_resolution_cannot_register(story, size):
    report = preview(story)
    report["visual_environment"]["capture_resolution"] = size
    persist(story, report)
    assert_error("INVALID_VISUAL_ENVIRONMENT", lambda: register(story, report))
    assert not (story[0].directory(story[1]) / "visual_baselines/index.json").exists()


def test_mixed_original_png_sizes_cannot_be_registered_or_compared(story):
    report = preview(story, capture_resolution=(280, 210))
    path = Path(report["screenshots"][1]["path"])
    with Image.new("RGBA", (281, 210)) as image:
        image.save(path)
    report["screenshots"][1]["sha256"] = file_hash(path)
    persist(story, report)
    assert_error("INVALID_VISUAL_IMAGE", lambda: register(story, report))
    assert_error("INVALID_VISUAL_IMAGE", lambda: compare(story, report))


def test_legacy_capture_fallback_is_explicit_and_cannot_claim_current_verified_comparison(story):
    report = preview(story)
    del report["visual_environment"]["capture_resolution"]
    persist(story, report)
    accepted = register(story, report)
    assert accepted["capture_resolution_verified"] is False
    result = compare(story, report)
    assert result["status"] == "blocked" and result["capture_resolution_verified"] is False
    assert all(r["code"] == "VISUAL_CAPTURE_UNVERIFIED" for r in result["comparisons"])
    current = compare(story, preview(story))
    assert current["status"] == "blocked"
    assert all("capture_resolution_verified" in r["differing_fields"] for r in current["comparisons"])
