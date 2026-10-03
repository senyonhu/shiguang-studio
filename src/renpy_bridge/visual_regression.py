"""Reviewed immutable screenshot baselines and comparable native preview checks.

PNG pixel comparison is QA, not authoring image transformation. A native preview
can succeed while this separate visual result fails or has no reviewed baseline.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import uuid

from filelock import FileLock
from PIL import Image, ImageChops, ImageStat

from .errors import BridgeError
from .models import Project
from .storage import Store, atomic_json, digest, file_hash, now, read_json


_SHA = re.compile(r"[0-9a-f]{64}")
_JOB = re.compile(r"job_[0-9a-f]{32}")
_BASELINE = re.compile(r"visual_[0-9a-f]{24}")


def _image_name(frame_id: str) -> str:
    # Model identifiers can legally be CON or 64 characters long. Keep paths
    # portable without changing their stable story identities.
    return "frame_" + digest(frame_id)[:24] + ".png"


def _inside(root: Path, value: str | Path) -> Path:
    try:
        root = Path(os.path.abspath(root))
        path = Path(value)
        if ".." in path.parts:
            raise ValueError("Parent traversal is not a managed path")
        path = Path(os.path.abspath(path if path.is_absolute() else root / path))
        if not path.is_relative_to(root):
            raise ValueError("Path escapes the managed root")
        # Inspect lexical ancestors before resolve so it cannot erase evidence
        # of a junction, including a junction pointing elsewhere inside root.
        for ancestor in reversed([path, *path.parents]):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                continue
            if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                    or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
                raise ValueError("Links, reparse points and special files are forbidden")
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()):
            raise ValueError("Resolved path escapes the managed root")
        return resolved
    except (OSError, ValueError, TypeError) as error:
        raise BridgeError("INVALID_VISUAL_PATH", "视觉文件路径必须留在受管目录且不经过链接或重解析点", path=str(value)) from error


def _read(path: Path, code="VISUAL_BASELINE_CORRUPT") -> dict:
    try:
        value = read_json(path)
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value
    except (OSError, ValueError, TypeError) as error:
        raise BridgeError(code, "无法读取完整视觉记录", path=str(path)) from error


def _environment(report: dict, project: Project) -> dict:
    value = report.get("visual_environment")
    if not isinstance(value, dict):
        raise BridgeError("VISUAL_ENVIRONMENT_MISSING", "预览未记录真实渲染环境，不能登记或比较基准")
    renderer = value.get("renderer")
    resolution = value.get("resolution")
    capture_verified = "capture_resolution" in value
    capture = value.get("capture_resolution", resolution)
    if (not isinstance(value.get("sdk_version"), str) or not value["sdk_version"]
            or value["sdk_version"] != report.get("sdk_version")
            or not isinstance(renderer, dict)
            or any(not isinstance(renderer.get(key), str) or not renderer[key].strip()
                   for key in ("backend", "vendor", "renderer", "version"))
            or not isinstance(resolution, list) or len(resolution) != 2
            or any(type(n) is not int for n in resolution)
            or resolution != [project.canvas.width, project.canvas.height]
            or not isinstance(capture, list) or len(capture) != 2
            or any(type(n) is not int or not 1 <= n <= 16384 for n in capture)
            or capture[0] * capture[1] > 64_000_000
            or not isinstance(value.get("font_sha256"), str) or not _SHA.fullmatch(value["font_sha256"])
            or value.get("language", project.language) != project.language):
        raise BridgeError("INVALID_VISUAL_ENVIRONMENT", "SDK、renderer、画布、字体或语言的预览记录不完整或不一致")
    try:
        # Reject unsupported renderer values, NaN and unserializable objects.
        json.dumps(renderer, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise BridgeError("INVALID_VISUAL_ENVIRONMENT", "renderer 信息必须是有限 JSON 数据") from error
    return {"sdk_version": value["sdk_version"], "renderer": deepcopy(renderer),
            "resolution": list(resolution), "font_sha256": value["font_sha256"],
            "language": project.language, "capture_resolution": list(capture),
            "capture_resolution_verified": capture_verified}


def _png(path: Path, expected_hash: str, resolution: list[int]) -> tuple[bytes, Image.Image]:
    try:
        if not isinstance(expected_hash, str) or not _SHA.fullmatch(expected_hash):
            raise ValueError("Invalid hash")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != expected_hash:
            raise BridgeError("VISUAL_IMAGE_CHANGED", "PNG 与已记录哈希不同", path=str(path))
        with Image.open(io.BytesIO(data)) as opened:
            if opened.format != "PNG" or list(opened.size) != resolution:
                raise ValueError("PNG format or resolution differs")
            opened.load()
            image = opened.convert("RGBA")
        return data, image
    except BridgeError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise BridgeError("INVALID_VISUAL_IMAGE", "视觉图片缺失、损坏或尺寸不符", path=str(path)) from error


def _preview(store: Store, project: Project, report: dict) -> tuple[dict, dict]:
    job_id = report.get("job_id")
    if (not isinstance(job_id, str) or not _JOB.fullmatch(job_id)
            or report.get("kind") != "preview" or report.get("project_id") != project.project_id
            or type(report.get("revision")) is not int or report["revision"] != project.revision
            or report.get("model_hash") != digest(project.model_dump(mode="json"))):
        raise BridgeError("INVALID_VISUAL_PREVIEW", "预览与作品身份、版本或内容哈希不一致")
    commands = report.get("commands")
    if not isinstance(commands, dict) or any(
        not isinstance(commands.get(name), dict)
        or type(commands[name].get("exit_code")) is not int or commands[name]["exit_code"] != 0
        or commands[name].get("timed_out") is not False or commands[name].get("cleanup_complete") is not True
        for name in ("compile", "lint", "native-tests")
    ):
        raise BridgeError("INVALID_VISUAL_PREVIEW", "预览必须具备实际成功完成的原生编译、lint及截图测试记录")
    root = store.directory(project.project_id)
    expected_target = _inside(root, f"generated/r{project.revision:08d}/{job_id}")
    generation = report.get("generation")
    if not isinstance(generation, dict) or not isinstance(generation.get("project_path"), str):
        raise BridgeError("INVALID_VISUAL_PREVIEW", "预览没有受管生成工程")
    target = _inside(root, generation["project_path"])
    if target != expected_target:
        raise BridgeError("INVALID_VISUAL_PATH", "预览生成工程与受管任务目录不一致")
    environment = _environment(report, project)
    manifest = _read(_inside(target, "bridge-manifest.json"), "INVALID_VISUAL_PREVIEW")
    if (manifest.get("project_id") != project.project_id or manifest.get("revision") != project.revision
            or manifest.get("sdk_version") != environment["sdk_version"]
            or manifest.get("chinese_font_sha256") != environment["font_sha256"]):
        raise BridgeError("INVALID_VISUAL_ENVIRONMENT", "预览环境与生成清单不一致")
    font = _inside(target, "game/SourceHanSansLite.ttf")
    try:
        if file_hash(font) != environment["font_sha256"]:
            raise BridgeError("VISUAL_FONT_CHANGED", "预览字体与生成清单哈希不同")
    except OSError as error:
        raise BridgeError("VISUAL_FONT_CHANGED", "预览字体缺失") from error
    screenshots = report.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots:
        raise BridgeError("INVALID_VISUAL_PREVIEW", "预览没有 PNG 截图")
    result = {}
    screenshot_root = _inside(target, "tests/screenshots")
    for entry in screenshots:
        if not isinstance(entry, dict):
            raise BridgeError("INVALID_VISUAL_PREVIEW", "预览截图记录不合法")
        frame_id = entry.get("frame_id")
        if (not isinstance(frame_id, str) or frame_id not in project.frames
                or frame_id in result or entry.get("revision") != project.revision
                or not isinstance(entry.get("path"), str)):
            raise BridgeError("INVALID_VISUAL_PREVIEW", "截图帧身份或版本无效或重复", frame_id=frame_id)
        path = _inside(screenshot_root, entry["path"])
        _, image = _png(path, entry.get("sha256"), environment["capture_resolution"])
        image.close()
        result[frame_id] = {"path": path, "sha256": entry["sha256"]}
    return environment, result


def _index(base: Path, project_id: str) -> dict:
    path = _inside(base, "index.json")
    if not path.exists():
        return {"schema_version": "1.0", "project_id": project_id, "baselines": {}, "active_frames": {}, "operations": {}}
    value = _read(path)
    if (value.get("schema_version") != "1.0" or value.get("project_id") != project_id
            or any(not isinstance(value.get(k), dict) for k in ["baselines", "active_frames", "operations"])):
        raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准索引版本或身份无效")
    return value


def _baseline(base: Path, index: dict, baseline_id: str) -> dict:
    if not isinstance(baseline_id, str) or not _BASELINE.fullmatch(baseline_id):
        raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准索引引用不合法")
    entry = index["baselines"].get(baseline_id)
    if not isinstance(entry, dict):
        raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准索引引用不合法")
    try:
        path = _inside(base, entry["manifest_path"])
        if path != _inside(base, f"runs/{baseline_id}/manifest.json") or file_hash(path) != entry["sha256"]:
            raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准清单哈希或路径不同")
        manifest = _read(path)
        if (manifest.get("baseline_id") != baseline_id or manifest.get("project_id") != index["project_id"]
                or manifest.get("schema_version") != "1.0" or not isinstance(manifest.get("frames"), dict)
                or not isinstance(manifest.get("environment"), dict)):
            raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准清单结构不合法")
        for frame_id, image in manifest["frames"].items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", frame_id):
                raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准帧身份不合法")
            expected = f"runs/{baseline_id}/images/{_image_name(frame_id)}"
            if not isinstance(image, dict) or image.get("image_path") != expected:
                raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准图片路径不合法")
            capture = manifest["environment"].get("capture_resolution", manifest["environment"]["resolution"])
            _, verified = _png(_inside(base, expected), image.get("sha256"), capture)
            verified.close()
        return manifest
    except (KeyError, OSError, TypeError) as error:
        raise BridgeError("VISUAL_BASELINE_CORRUPT", "视觉基准文件缺失或索引不完整") from error


def register_visual_baseline(store: Store, project_id: str, expected_revision: int, operation_id: str,
                             preview_job_id: str, review_note: str, frame_ids: list[str] | None = None) -> dict:
    """Explicitly accept reviewed native PNGs; do not advance content revision."""
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise BridgeError("INVALID_OPERATION_ID", "登记视觉基准需要 operation_id")
    if not isinstance(review_note, str) or len(review_note.strip()) < 8:
        raise BridgeError("VISUAL_REVIEW_REQUIRED", "请明确记录已审查截图及首次登记或更新理由（至少8字符）")
    if type(expected_revision) is not int or expected_revision < 0:
        raise BridgeError("VERSION_CONFLICT", "视觉基准需要有效 expected_revision")
    if not isinstance(preview_job_id, str) or not _JOB.fullmatch(preview_job_id):
        raise BridgeError("INVALID_JOB_ID", "视觉基准需要受管预览任务编号")
    if frame_ids is not None and (not isinstance(frame_ids, list) or not frame_ids
            or any(not isinstance(frame, str) for frame in frame_ids) or len(set(frame_ids)) != len(frame_ids)):
        raise BridgeError("INVALID_VISUAL_FRAMES", "基准帧列表必须非空且没有重复身份")
    payload_hash = digest({"project_id": project_id, "expected_revision": expected_revision,
                           "preview_job_id": preview_job_id, "review_note": review_note,
                           "frame_ids": None if frame_ids is None else sorted(frame_ids)})
    root = store.directory(project_id)
    base = _inside(root, "visual_baselines")
    with FileLock(str(root / ".write.lock")):
        index = _index(base, project_id)
        old = index["operations"].get(operation_id)
        if old is not None:
            if not isinstance(old, dict) or old.get("digest") != payload_hash:
                raise BridgeError("IDEMPOTENCY_CONFLICT", "登记操作编号对应不同视觉审查")
            try:
                _baseline(base, index, old["result"]["baseline_id"])
                return deepcopy(old["result"])
            except (KeyError, TypeError) as error:
                raise BridgeError("VISUAL_BASELINE_CORRUPT", "登记操作记录不完整") from error
        project = store.load(project_id)
        if project.revision != expected_revision:
            raise BridgeError("VERSION_CONFLICT", "作品已改变，请审查当前版本预览", current_revision=project.revision)
        job_dir = _inside(store.home, f"jobs/{preview_job_id}")
        status = _read(_inside(job_dir, "status.json"), "INVALID_VISUAL_PREVIEW")
        if (status.get("job_id") != preview_job_id or status.get("state") != "succeeded"
                or status.get("kind") != "preview" or status.get("project_id") != project_id
                or status.get("revision") != project.revision):
            raise BridgeError("INVALID_VISUAL_PREVIEW", "只能登记当前作品版本实际成功的预览任务")
        report_path = _inside(job_dir, "report.json")
        try:
            report_bytes = report_path.read_bytes()
            report = json.loads(report_bytes)
            if not isinstance(report, dict):
                raise ValueError("JSON object required")
        except (OSError, ValueError, TypeError) as error:
            raise BridgeError("INVALID_VISUAL_PREVIEW", "无法读取完整预览报告") from error
        report_hash = hashlib.sha256(report_bytes).hexdigest()
        if report.get("ok") is not True or report.get("status") != "passed" or report.get("job_id") != preview_job_id:
            raise BridgeError("INVALID_VISUAL_PREVIEW", "预览原生运行未成功")
        environment, screenshots = _preview(store, project, report)
        selected = sorted(screenshots) if frame_ids is None else sorted(frame_ids)
        if any(frame not in screenshots for frame in selected):
            raise BridgeError("FRAME_NOT_PREVIEWED", "登记帧不在此实际预览中", frame_ids=selected)
        baseline_id = "visual_" + digest({"project_id": project_id, "operation_id": operation_id})[:24]
        final = _inside(base, f"runs/{baseline_id}")
        manifest = {"schema_version": "1.0", "baseline_id": baseline_id, "project_id": project_id,
                    "revision": project.revision, "model_hash": report["model_hash"],
                    "preview_job_id": preview_job_id, "preview_report_sha256": report_hash,
                    "code_digest": report.get("code_digest"), "generator_version": report.get("generator_version"),
                    "review_note": review_note, "payload_hash": payload_hash,
                    "environment": environment, "frames": {frame: {
                        "image_path": f"runs/{baseline_id}/images/{_image_name(frame)}", "sha256": screenshots[frame]["sha256"],
                        "frame_hash": digest(project.frames[frame].model_dump(mode="json"))} for frame in selected}}
        if final.exists():
            recovered = _read(_inside(final, "manifest.json"))
            if recovered != manifest:
                raise BridgeError("VISUAL_BASELINE_CORRUPT", "未提交的视觉基准与本次请求不同")
        else:
            stage = _inside(base, f".pending-{baseline_id}-{uuid.uuid4().hex}")
            (stage / "images").mkdir(parents=True, exist_ok=False)
            for frame in selected:
                data, image = _png(screenshots[frame]["path"], screenshots[frame]["sha256"], environment["capture_resolution"])
                image.close()
                with (stage / "images" / _image_name(frame)).open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
            atomic_json(stage / "manifest.json", manifest)
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(stage, final)
        manifest_path = _inside(final, "manifest.json")
        index["baselines"][baseline_id] = {"manifest_path": f"runs/{baseline_id}/manifest.json", "sha256": file_hash(manifest_path)}
        _baseline(base, index, baseline_id)
        if file_hash(report_path) != report_hash:
            raise BridgeError("VISUAL_PREVIEW_CHANGED", "登记期间预览报告发生变化")
        for frame in selected:
            index["active_frames"][frame] = baseline_id
        result = {"ok": True, "project_id": project_id, "revision": project.revision, "baseline_id": baseline_id,
                  "frame_ids": selected, "manifest_path": str(manifest_path), "manifest_sha256": file_hash(manifest_path),
                  "review_note": review_note, "content_revision_changed": False,
                  "capture_resolution_verified": environment["capture_resolution_verified"]}
        index["operations"][operation_id] = {"digest": payload_hash, "result": result}
        index["updated_at"] = now()
        atomic_json(_inside(base, "index.json"), index)
        return result


def compare_preview(store: Store, project: Project, report: dict) -> dict:
    """Compare existing reviewed frames. Never adopt or overwrite a baseline."""
    result = {"ok": True, "status": "not_run", "method": "exact RGBA pixels in matching native environment",
              "comparisons": [], "unregistered_frame_ids": [], "baseline_updated": False}
    environment, screenshots = _preview(store, project, report)
    result["capture_resolution_verified"] = environment["capture_resolution_verified"]
    base = _inside(store.directory(project.project_id), "visual_baselines")
    index = _index(base, project.project_id)
    manifests = {}
    for frame_id, screenshot in screenshots.items():
        baseline_id = index["active_frames"].get(frame_id)
        if baseline_id is None:
            result["unregistered_frame_ids"].append(frame_id)
            continue
        if not isinstance(baseline_id, str) or not _BASELINE.fullmatch(baseline_id):
            raise BridgeError("VISUAL_BASELINE_CORRUPT", "活动视觉基准编号不合法", frame_id=frame_id)
        if baseline_id not in manifests:
            manifests[baseline_id] = _baseline(base, index, baseline_id)
        manifest = manifests[baseline_id]
        old = manifest["frames"].get(frame_id)
        if not isinstance(old, dict):
            raise BridgeError("VISUAL_BASELINE_CORRUPT", "活动视觉基准没有对应帧", frame_id=frame_id)
        record = {"frame_id": frame_id, "baseline_id": baseline_id, "baseline_revision": manifest["revision"],
                  "baseline_sha256": old["sha256"], "actual_path": str(screenshot["path"]), "actual_sha256": screenshot["sha256"]}
        changed_environment = [key for key in environment if environment[key] != manifest["environment"].get(key)]
        if changed_environment or not environment["capture_resolution_verified"]:
            record.update(status="blocked", code="VISUAL_ENVIRONMENT_CHANGED" if changed_environment else "VISUAL_CAPTURE_UNVERIFIED", differing_fields=changed_environment,
                          baseline_environment=manifest["environment"], actual_environment=environment)
            result["comparisons"].append(record)
            continue
        baseline_path = _inside(base, old["image_path"])
        _, expected = _png(baseline_path, old["sha256"], environment["capture_resolution"])
        actual_data, actual = _png(screenshot["path"], screenshot["sha256"], environment["capture_resolution"])
        difference = ImageChops.difference(expected, actual)
        channels = difference.split()
        mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), ImageChops.lighter(channels[2], channels[3]))
        total = expected.width * expected.height
        count = total - mask.histogram()[0]
        metrics = {"changed_pixels": count, "total_pixels": total, "changed_fraction": count / total,
                   "bbox": None if mask.getbbox() is None else list(mask.getbbox()),
                   "mean_absolute_channel_difference": ImageStat.Stat(difference).mean,
                   "maximum_channel_difference": max(high for _, high in difference.getextrema()),
                   "allowed_changed_pixels": 0}
        record.update(status="passed" if count == 0 else "failed", baseline_path=str(baseline_path), metrics=metrics)
        if count:
            evidence = _inside(store.home, f"jobs/{report['job_id']}/visual-regression/{uuid.uuid4().hex}/{_image_name(frame_id)[:-4]}")
            evidence.mkdir(parents=True, exist_ok=False)
            actual_path = evidence / "actual.png"
            with actual_path.open("xb") as stream:
                stream.write(actual_data)
            diff_path = evidence / "diff.png"
            # An opaque RGB diff remains visible in standard image viewers.
            # Include alpha-only changes in every channel instead of hiding them.
            alpha = channels[3]
            visible = Image.merge("RGB", tuple(ImageChops.lighter(channel, alpha) for channel in channels[:3]))
            visible.save(diff_path, format="PNG")
            visible.close()
            record.update(code="VISUAL_DIFFERENCE", actual_copy_path=str(actual_path),
                          diff_path=str(diff_path), diff_sha256=file_hash(diff_path), metrics_path=str(evidence / "metrics.json"))
            atomic_json(evidence / "metrics.json", metrics)
        expected.close()
        actual.close()
        difference.close()
        mask.close()
        for channel in channels:
            channel.close()
        result["comparisons"].append(record)
    statuses = {comparison["status"] for comparison in result["comparisons"]}
    if "failed" in statuses:
        result.update(ok=False, status="failed")
    elif "blocked" in statuses:
        result.update(ok=False, status="blocked")
    elif result["comparisons"] and not result["unregistered_frame_ids"]:
        result["status"] = "passed"
    return result
