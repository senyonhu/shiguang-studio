"""Capture the unchanged PLAN 1.0 baseline before HTML runtime work."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed-home", required=True, type=Path)
    args = parser.parse_args()
    started = datetime.now(timezone(timedelta(hours=8)))
    run = ROOT / "evidence/html-h0-baseline/runs" / (
        started.strftime("%Y%m%dT%H%M%S") + "-" + str(time.time_ns())[-8:]
    )
    run.mkdir(parents=True, exist_ok=False)
    report = {
        "run_id": run.name, "started_at": started.isoformat(),
        "scope": "read-only PLAN 1.0 baseline; no HTML runtime verification",
        "status": "failed", "platform": platform.platform(),
        "checks": {}, "commands": {}, "files": {}, "installed_home": str(args.installed_home),
        "product_decisions": {
            "window": {"source": "explicit user reply", "value": "dedicated managed minigame window"},
            "execution": {"source": "user message 执行", "value": "authorized"},
            "platform": {"source": "approved plan default", "value": "Windows x64 offline first"},
            "save": {"source": "approved plan default", "value": "checkpoint for templates; restart floor"},
        },
    }
    from renpy_bridge.pipeline import _code_digest
    from renpy_bridge.storage import Store
    from renpy_bridge.sdk import discover_sdk
    core = ROOT / "src/renpy_bridge"
    report["code_digest"] = _code_digest()
    report["checks"]["original_core_digest"] = (
        report["code_digest"] == "c896aec6fcd166b4565bc21b33c674fe0a4bfb9c968c012518d5caaced1694d0"
    )
    installed_core = args.installed_home / "src/renpy_bridge"
    pairs = []
    for source in sorted(core.rglob("*.py")):
        rel = source.relative_to(core)
        target = installed_core / rel
        pairs.append({"path": rel.as_posix(), "workspace_sha256": sha(source),
                      "installed_sha256": sha(target) if target.is_file() else None})
    report["core_files"] = pairs
    report["checks"]["installed_core_equal"] = all(
        p["workspace_sha256"] == p["installed_sha256"] for p in pairs
    )
    sdk_lock = json.loads((ROOT / "runtimes/sdk-lock.json").read_text(encoding="utf-8"))
    sdk = discover_sdk(ROOT / sdk_lock["relative_path"])
    report["sdk"] = {"version": sdk.version, "root": str(sdk.root),
                     "lock": sdk_lock, "zip_sha256": sha(ROOT / "runtimes/renpy-8.5.3-sdk.zip")}
    report["checks"]["sdk_version"] = sdk.version == sdk_lock["version"]
    report["checks"]["sdk_zip"] = report["sdk"]["zip_sha256"] == sdk_lock["sha256"]
    store = Store(args.installed_home)
    demo_id = "p_5ab42b6ee5762138d14a"
    report["demo_summary"] = store.inspect(demo_id, "summary")
    report["demo_head"] = store.head(demo_id)
    demo = store.load(demo_id)
    report["checks"]["demo_revision"] = demo.revision == 12 and report["demo_head"]["runnable_revision"] == 12
    report["checks"]["demo_frames"] = len(demo.frames) == 12
    old_tasks = json.loads((ROOT / "docs/TASKS.json").read_text(encoding="utf-8"))
    task_items = old_tasks["tasks"]
    report["checks"]["old_tasks_done"] = len(task_items) == 46 and all(t["status"] == "done" for t in task_items)
    for path in [
        ROOT / "docs/PLAN.md", ROOT / "docs/STATUS.md", ROOT / "docs/TASKS.json",
        ROOT / "outputs/delivery/20261002-初版交付",
    ]:
        if path.is_file():
            report["files"][str(path.relative_to(ROOT))] = {"sha256": sha(path), "size": path.stat().st_size}
        elif path.is_dir():
            report["files"][str(path.relative_to(ROOT))] = {
                p.relative_to(path).as_posix(): {"sha256": sha(p), "size": p.stat().st_size}
                for p in path.rglob("*") if p.is_file() and p.suffix.lower() == ".zip"
            }
    for name, argv in {
        "git_head": ["git", "rev-parse", "HEAD"],
        "dotnet_sdks": ["dotnet", "--list-sdks"],
        "dotnet_runtimes": ["dotnet", "--list-runtimes"],
    }.items():
        proc = subprocess.run(argv, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace",
                              timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        report["commands"][name] = {"argv": argv, "exit_code": proc.returncode,
                                     "stdout": proc.stdout, "stderr": proc.stderr}
    report["status"] = "passed" if all(report["checks"].values()) else "failed"
    report["finished_at"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(run / "report.json"),
                      "checks": report["checks"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

