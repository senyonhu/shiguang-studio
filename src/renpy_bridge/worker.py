"""Managed worker entry point: python -m renpy_bridge.worker JOB_ID."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import traceback

from .errors import BridgeError
from .jobs import JobManager
from .storage import Store


def run(job_id: str) -> int:
    home = os.environ.get("RENPY_BRIDGE_HOME")
    if not home:
        print("RENPY_BRIDGE_HOME is required for a managed worker.", file=sys.stderr, flush=True)
        return 2
    manager = JobManager(Store(Path(home)))
    job = manager._claim(job_id)
    if job is None:
        print(f"{job_id}: terminal state or cancellation already persisted.", flush=True)
        return 0
    print(f"{job_id}: running {job['kind']} at revision {job['revision']}.", flush=True)
    try:
        from .pipeline import execute_job

        report = execute_job(manager.store, job)
        if not isinstance(report, dict):
            raise BridgeError("INVALID_JOB_REPORT", "Pipeline must return a JSON report object.")
        failed = report.get("ok") is False or report.get("status") in {
            "failed", "error", "cancelled", "interrupted", "unverified",
        }
        error = report.get("error") if failed else None
        if failed and error is None:
            error = {
                "code": "PIPELINE_FAILED",
                "message": "Pipeline returned a non-success report.",
                "details": {"status": report.get("status")},
            }
        final = manager._finish(job_id, "failed" if failed else "succeeded", report, error)
    except BridgeError as error:
        print(f"{job_id}: {error.code}: {error.message}", file=sys.stderr, flush=True)
        failure = error.as_dict()["error"]
        final = manager._finish(
            job_id, "failed", {"status": "failed", "error": failure}, failure
        )
    except Exception as error:
        traceback.print_exc()
        failure = {
            "code": "WORKER_ERROR",
            "message": f"{type(error).__name__}: {error}",
            "details": {},
        }
        final = manager._finish(
            job_id, "failed", {"status": "failed", "error": failure}, failure
        )
    print(f"{job_id}: {final['state']}.", flush=True)
    return 0 if final["state"] in {"succeeded", "cancelled"} else 1


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python -m renpy_bridge.worker JOB_ID", file=sys.stderr)
        return 2
    try:
        return run(sys.argv[1])
    except BridgeError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

