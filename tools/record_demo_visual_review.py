"""Record actual human/model image observations against immutable native screenshots.

This does not inspect images or infer visual success. The caller must actually
view each image and provide observations. It only binds those observations to
the native report and current bytes, then saves a new evidence directory.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import uuid


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--observations", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--revision", required=True, type=int)
    args = parser.parse_args()
    report_path, observations_path = args.report.resolve(strict=True), args.observations.resolve(strict=True)
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    observations = json.loads(observations_path.read_text(encoding="utf-8-sig"))
    if report.get("project_id") != args.project_id or report.get("revision") != args.revision:
        raise ValueError("Report identity does not match the reviewed project and revision")
    if not observations.get("actual_images_viewed") or observations.get("inspection_tool") != "view_image":
        raise ValueError("The record must explicitly attest actual view_image inspection")
    screenshots = {item["frame_id"]: item for item in report.get("screenshots", [])}
    entries = observations.get("screenshots", [])
    if not entries or len({e["frame_id"] for e in entries}) != len(entries):
        raise ValueError("Need nonempty, unique observations")
    reviewed = []
    for entry in entries:
        actual = screenshots[entry["frame_id"]]
        path = Path(actual["path"]).resolve(strict=True)
        file_hash = sha(path)
        if file_hash != actual["sha256"] or actual.get("revision") != args.revision:
            raise ValueError("Native screenshot bytes/revision changed after the reported capture")
        if entry.get("status") not in {"passed", "failed"} or not entry.get("observation"):
            raise ValueError("Each directly viewed image needs a status and concrete observation")
        reviewed.append({**entry, "path": str(path), "sha256": file_hash,
                         "revision": args.revision, "inspection_origin": "independent_view_image"})
    run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = args.output_root.resolve() / "runs" / run
    output.mkdir(parents=True, exist_ok=False)
    inputs = output / "inputs"
    inputs.mkdir()
    snapshots = []
    for source in [report_path, observations_path, Path(__file__).resolve()] + [Path(i["path"]) for i in reviewed]:
        file_hash = sha(source)
        target = inputs / (file_hash + source.suffix.lower())
        if not target.exists():
            shutil.copyfile(source, target)
        if sha(target) != file_hash:
            raise ValueError("Evidence copy changed")
        snapshots.append({"path": str(source), "sha256": file_hash, "snapshot": str(target), "size_bytes": source.stat().st_size})
    result = {"status": "failed" if any(i["status"] == "failed" for i in reviewed) else "passed",
              "project_id": args.project_id, "revision": args.revision,
              "generator_version": report.get("generator_version"), "sdk_version": report.get("sdk_version"),
              "reviewer": args.reviewer, "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "inspection_provenance": {"tool": "view_image", "direct_independent_review": True,
                                        "all_recorded_images_directly_viewed": True,
                                        "native_report_sha256": sha(report_path),
                                        "caller_observations_sha256": sha(observations_path)},
              "direct_independent_sample_frames": [i["frame_number"] for i in reviewed],
              "screenshots": reviewed, "diagnostics": observations.get("diagnostics", []),
              "limitations": ["Static native previews do not prove audio output or standalone release operation."],
              "inputs": snapshots}
    result_path = output / "visual-review.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "review": str(result_path), "frames": result["direct_independent_sample_frames"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
