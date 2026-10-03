"""Lifecycle tests use real managed processes and a fixed fake pipeline.

They verify job semantics, not RenPy rendering or Codex/SDK integration.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from renpy_bridge.errors import BridgeError
from renpy_bridge.jobs import JobManager, TERMINAL
from renpy_bridge.storage import atomic_json, read_json


FIXTURE_WORKER = r'''
import os
from pathlib import Path
import subprocess
import sys
import time
import types
import psutil
from renpy_bridge.errors import BridgeError
from renpy_bridge.storage import atomic_json
from renpy_bridge.worker import run

pipeline = types.ModuleType("renpy_bridge.pipeline")
def execute_job(store, job):
    mode = job["options"].get("mode", "success")
    if mode == "failure":
        raise BridgeError("FIXTURE_FAILURE", "Expected pipeline failure.", frame_id="frame_1")
    if mode == "crash":
        os._exit(17)
    if mode == "sleep":
        time.sleep(20)
    if mode == "child":
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        atomic_json(
            Path(job["report_path"]).parent / "child.json",
            {"pid": child.pid, "create_time": psutil.Process(child.pid).create_time()},
        )
        time.sleep(20)
    return {"status": "passed", "revision": job["revision"], "artifacts": []}
pipeline.execute_job = execute_job
sys.modules["renpy_bridge.pipeline"] = pipeline
raise SystemExit(run(sys.argv[1]))
'''


class FixtureStore:
    def __init__(self, home: Path):
        self.home = home.resolve()
        self.home.mkdir(parents=True, exist_ok=True)

    def load(self, project_id: str, revision: int | None = None):
        if project_id != "fixture_project":
            raise BridgeError("PROJECT_NOT_FOUND", "Fixture project does not exist.")
        return SimpleNamespace(revision=revision if revision is not None else 3)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    script = tmp_path / "fixed_worker.py"
    script.write_text(FIXTURE_WORKER, encoding="utf-8")
    manager = JobManager(FixtureStore(tmp_path / "home"))
    monkeypatch.setattr(manager, "_worker_argv", lambda job_id: [sys.executable, str(script), job_id])
    yield manager
    # Cleanup only recorded, identity-checked processes created by this fixture.
    for directory in manager.root.iterdir():
        if directory.is_dir() and (directory / "status.json").is_file():
            try:
                status = read_json(directory / "status.json")
                manager._terminate_owned_tree(status)
            except BridgeError:
                pass


def wait_state(manager, job_id, states=TERMINAL, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = manager.get(job_id)
        if result["state"] in states:
            return result
        time.sleep(0.025)
    pytest.fail(f"Job did not enter {sorted(states)}: {manager.get(job_id)}")


def same_process_active(pid, created):
    try:
        process = psutil.Process(pid)
        return (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
            and process.create_time() == created
        )
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def test_real_worker_success_persists_pinned_revision_and_report(manager):
    started = manager.start("check", "fixture_project", 7, {})
    result = wait_state(manager, started["job_id"])
    assert result["state"] == "succeeded"
    assert result["revision"] == 7
    assert result["pid"] and result["create_time"]
    assert read_json(Path(result["report_path"]))["revision"] == 7
    assert "running check" in Path(result["log_path"]).read_text(encoding="utf-8")
    reopened = JobManager(FixtureStore(manager.store.home))
    assert reopened.get(result["job_id"])["state"] == "succeeded"


def test_worker_bridge_error_is_structured_and_logged(manager):
    started = manager.start("check", "fixture_project", 3, {"mode": "failure"})
    result = wait_state(manager, started["job_id"])
    assert result["state"] == "failed"
    assert result["error"]["code"] == "FIXTURE_FAILURE"
    assert result["error"]["details"]["frame_id"] == "frame_1"
    assert read_json(Path(result["report_path"]))["status"] == "failed"
    assert "FIXTURE_FAILURE" in Path(result["log_path"]).read_text(encoding="utf-8")


def test_dead_worker_becomes_interrupted_after_manager_restart(manager):
    started = manager.start("check", "fixture_project", 3, {"mode": "crash"})
    reopened = JobManager(FixtureStore(manager.store.home))
    result = wait_state(reopened, started["job_id"])
    assert result["state"] == "interrupted"
    assert result["error"]["code"] == "JOB_INTERRUPTED"
    assert result["finished_at"]


def test_running_worker_is_recognized_after_restart(manager):
    started = manager.start("check", "fixture_project", 3, {"mode": "sleep"})
    wait_state(manager, started["job_id"], {"running"})
    reopened = JobManager(FixtureStore(manager.store.home))
    assert reopened.get(started["job_id"])["state"] == "running"
    assert reopened.cancel(started["job_id"])["state"] == "cancelled"


def test_cancel_terminates_owned_child_and_late_success_cannot_overwrite(manager):
    started = manager.start("check", "fixture_project", 3, {"mode": "child"})
    job_id = started["job_id"]
    child_path = manager.root / job_id / "child.json"
    deadline = time.monotonic() + 8
    while not child_path.is_file() and time.monotonic() < deadline:
        time.sleep(0.025)
    assert child_path.is_file()
    child = read_json(child_path)
    assert same_process_active(child["pid"], child["create_time"])
    cancelled = manager.cancel(job_id)
    assert cancelled["state"] == "cancelled"
    assert cancelled["cancel_requested"] is True
    deadline = time.monotonic() + 3
    while same_process_active(child["pid"], child["create_time"]) and time.monotonic() < deadline:
        time.sleep(0.025)
    assert not same_process_active(child["pid"], child["create_time"])
    final = manager._finish(job_id, "succeeded", {"status": "passed", "late": True})
    assert final["state"] == "cancelled"
    assert not Path(final["report_path"]).exists()
    assert manager.cancel(job_id)["state"] == "cancelled"


def test_identity_mismatch_is_interrupted_and_cancel_does_not_kill_unrelated_process(manager):
    started = manager.start("check", "fixture_project", 3, {"mode": "crash"})
    completed = wait_state(manager, started["job_id"])
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    try:
        created = psutil.Process(unrelated.pid).create_time()
        state_path = manager.root / completed["job_id"] / "status.json"
        status = dict(completed, state="running", pid=unrelated.pid, create_time=created + 1)
        atomic_json(state_path, status)
        assert manager.get(completed["job_id"])["state"] == "interrupted"
        assert unrelated.poll() is None
        # Correct timestamp but wrong worker argv is still not an owned job.
        status.update(state="running", create_time=created, cancel_requested=False)
        atomic_json(state_path, status)
        cancelled = manager.cancel(completed["job_id"])
        assert cancelled["state"] == "cancelled"
        assert cancelled["cancel_cleanup"]["state"] == "not_owned_or_inactive"
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


def test_no_public_process_launch_override_and_invalid_identifiers(manager):
    with pytest.raises(BridgeError) as error:
        manager.start("check", "fixture_project", 3, {"worker_argv": ["unsafe"]})
    assert error.value.code == "INVALID_JOB_OPTIONS"
    with pytest.raises(BridgeError) as error:
        manager.start("unknown", "fixture_project", 3, {})
    assert error.value.code == "INVALID_JOB_KIND"
    with pytest.raises(BridgeError) as error:
        manager.get("../outside")
    assert error.value.code == "INVALID_JOB_ID"
    with pytest.raises(BridgeError) as error:
        manager.start("check", "fixture_project", 3, {"timeout": float("nan")})
    assert error.value.code == "INVALID_JOB_OPTIONS"

