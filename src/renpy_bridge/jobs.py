"""Persistent jobs and ownership-checked worker lifecycle."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from filelock import FileLock, Timeout
import psutil

from .errors import BridgeError
from .storage import atomic_json, now, read_json


KINDS = frozenset({"check", "preview", "routes", "build", "minigame"})
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
STATES = TERMINAL | {"queued", "running"}
RESERVED_OPTIONS = frozenset({
    "worker_argv", "worker_command", "command", "executable", "shell", "cwd", "env",
})


def _same_creation(actual: float, expected: float) -> bool:
    # The kernel timestamp is stable and JSON preserves its float round trip.
    return actual == expected


class JobManager:
    def __init__(self, store):
        self.store = store
        self.root = (store.home / "jobs").resolve()
        if not self.root.is_relative_to(store.home.resolve()):
            raise BridgeError("INVALID_JOB_ROOT", "Job directory must stay inside the bridge home.")
        self.root.mkdir(parents=True, exist_ok=True)

    def _directory(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or not re.fullmatch(r"job_[0-9a-f]{32}", job_id):
            raise BridgeError("INVALID_JOB_ID", "Invalid job identifier.", job_id=job_id)
        directory = (self.root / job_id).resolve()
        if not directory.is_relative_to(self.root):
            raise BridgeError("INVALID_JOB_PATH", "Job directory escapes the managed job root.")
        if not directory.is_dir():
            raise BridgeError("JOB_NOT_FOUND", "Job does not exist.", job_id=job_id)
        return directory

    @contextmanager
    def _locked(self, job_id: str):
        directory = self._directory(job_id)
        try:
            with FileLock(str(directory / "status.lock"), timeout=10):
                yield directory
        except Timeout as error:
            raise BridgeError("JOB_BUSY", "Job state is locked by another operation.", job_id=job_id) from error

    def _read(self, directory: Path) -> dict:
        try:
            status = read_json(directory / "status.json")
        except (OSError, ValueError) as error:
            raise BridgeError("JOB_STATE_CORRUPT", "Cannot read persisted job state.", job_id=directory.name) from error
        if (
            not isinstance(status, dict)
            or status.get("job_id") != directory.name
            or status.get("state") not in STATES
        ):
            raise BridgeError("JOB_STATE_CORRUPT", "Persisted job state is invalid.", job_id=directory.name)
        return status

    def _save(self, directory: Path, status: dict) -> None:
        status["updated_at"] = now()
        atomic_json(directory / "status.json", status)

    def _worker_argv(self, job_id: str) -> list[str]:
        # Private seam for fixed test fixtures; never read argv from tool options.
        return [sys.executable, "-m", "renpy_bridge.worker", job_id]

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        source_root = str(Path(__file__).resolve().parent.parent)
        inherited = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = source_root + (os.pathsep + inherited if inherited else "")
        environment["RENPY_BRIDGE_HOME"] = str(self.store.home)
        environment["PYTHONUTF8"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    @staticmethod
    def _owned_process(status: dict):
        pid, created = status.get("pid"), status.get("create_time")
        if pid is None or created is None:
            return None
        try:
            process = psutil.Process(int(pid))
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return None
            if not _same_creation(process.create_time(), float(created)):
                return None
            expected = status.get("worker_argv", [])
            actual = process.cmdline()
            # Windows venv redirectors can expose the base interpreter path.
            # Compare executable basename and all remaining job-specific args.
            if (
                not expected or not actual
                or Path(actual[0]).name.lower() != Path(expected[0]).name.lower()
                or actual[1:] != expected[1:]
            ):
                return None
            return process
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return None
        except psutil.AccessDenied as error:
            raise BridgeError("JOB_OWNERSHIP_UNVERIFIED", "Cannot verify ownership of the worker.", pid=pid) from error

    def start(self, kind: str, project_id: str, revision: int, options: dict) -> dict:
        if not isinstance(kind, str) or kind not in KINDS:
            raise BridgeError("INVALID_JOB_KIND", "Unsupported job kind.", kind=kind, allowed=sorted(KINDS))
        if not isinstance(options, dict):
            raise BridgeError("INVALID_JOB_OPTIONS", "Job options must be a JSON object.")
        forbidden = RESERVED_OPTIONS.intersection(options)
        if forbidden:
            raise BridgeError("INVALID_JOB_OPTIONS", "Process launch settings are not public job options.", fields=sorted(forbidden))
        try:
            options = json.loads(json.dumps(options, allow_nan=False))
        except (TypeError, ValueError) as error:
            raise BridgeError("INVALID_JOB_OPTIONS", "Job options must contain only finite JSON data.") from error
        project = self.store.load(project_id, revision)
        job_id = "job_" + uuid.uuid4().hex
        directory = self.root / job_id
        directory.mkdir()
        timestamp = now()
        argv = self._worker_argv(job_id)
        status = {
            "job_id": job_id,
            "state": "queued",
            "kind": kind,
            "project_id": project_id,
            "revision": project.revision,
            "options": options,
            "created_at": timestamp,
            "updated_at": timestamp,
            "started_at": None,
            "finished_at": None,
            "launch_started_unix": time.time(),
            "pid": None,
            "create_time": None,
            "launcher_pid": None,
            "worker_argv": argv,
            "cancel_requested": False,
            "log_path": str(directory / "worker.log"),
            "report_path": str(directory / "report.json"),
        }
        atomic_json(directory / "status.json", status)
        process = None
        try:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            with (directory / "worker.log").open("ab", buffering=0) as log:
                process = subprocess.Popen(
                    argv,
                    cwd=str(self.store.home),
                    env=self._environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
            try:
                created = psutil.Process(process.pid).create_time()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                created = None
            with self._locked(job_id) as directory:
                current = self._read(directory)
                current["launcher_pid"] = process.pid
                # Worker may already have claimed itself or finished. Do not
                # reset state or replace the actual worker's registered PID.
                if current.get("pid") is None and created is not None:
                    current["pid"] = process.pid
                    current["create_time"] = created
                self._save(directory, current)
            if current.get("cancel_requested"):
                return self.cancel(job_id)
        except Exception as error:
            if process is not None and process.poll() is None:
                # Popen returned this exact process; no unverified PID lookup.
                process.terminate()
            with self._locked(job_id) as directory:
                current = self._read(directory)
                if current["state"] not in TERMINAL:
                    current["state"] = "failed"
                    current["finished_at"] = now()
                    current["error"] = {
                        "code": "WORKER_START_FAILED",
                        "message": str(error),
                        "details": {},
                    }
                    self._save(directory, current)
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self._locked(job_id) as directory:
            status = self._read(directory)
            if status["state"] not in TERMINAL:
                try:
                    process = self._owned_process(status)
                except BridgeError as error:
                    result = dict(status)
                    result["verification_error"] = error.as_dict()["error"]
                    return result
                waiting_for_registration = (
                    status.get("pid") is None
                    and time.time() - status.get("launch_started_unix", 0) < 10
                )
                if process is None and not waiting_for_registration:
                    previous = status["state"]
                    status["state"] = "interrupted"
                    status["finished_at"] = now()
                    status["error"] = {
                        "code": "JOB_INTERRUPTED",
                        "message": "Worker exited or its recorded process identity no longer matches.",
                        "details": {"previous_state": previous},
                    }
                    self._save(directory, status)
            return dict(status)

    @staticmethod
    def _terminate_owned_tree(status: dict) -> dict:
        process = JobManager._owned_process(status)
        if process is None:
            return {"state": "not_owned_or_inactive", "terminated_pids": []}
        targets = []
        suspended = False
        errors = []
        try:
            try:
                process.suspend()
                suspended = True
            except psutil.AccessDenied:
                pass
            for child in process.children(recursive=True):
                try:
                    targets.append((child, child.create_time()))
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
            targets.reverse()
            targets.append((process, process.create_time()))
            terminated = []
            for target, created in targets:
                try:
                    target = psutil.Process(target.pid)
                    if not _same_creation(target.create_time(), created):
                        continue
                    target.terminate()
                    terminated.append(target.pid)
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
                except psutil.AccessDenied as error:
                    errors.append(str(error))
            _, alive = psutil.wait_procs([item[0] for item in targets], timeout=2)
            for target in alive:
                expected = next(created for candidate, created in targets if candidate.pid == target.pid)
                try:
                    target = psutil.Process(target.pid)
                    if _same_creation(target.create_time(), expected):
                        target.kill()
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    pass
                except psutil.AccessDenied as error:
                    errors.append(str(error))
            return {
                "state": "terminated" if not errors else "incomplete",
                "terminated_pids": terminated,
                "errors": errors,
            }
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            return {"state": "inactive", "terminated_pids": []}
        except psutil.AccessDenied as error:
            return {"state": "incomplete", "terminated_pids": [], "errors": [str(error)]}
        finally:
            if suspended:
                try:
                    process = psutil.Process(process.pid)
                    if _same_creation(process.create_time(), float(status["create_time"])):
                        process.resume()
                except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
                    pass

    def cancel(self, job_id: str) -> dict:
        with self._locked(job_id) as directory:
            status = self._read(directory)
            if status["state"] in TERMINAL and status["state"] != "cancelled":
                return dict(status)
            status["cancel_requested"] = True
            status["state"] = "cancelled"
            status["finished_at"] = status.get("finished_at") or now()
            self._save(directory, status)
            snapshot = dict(status)
        try:
            cleanup = self._terminate_owned_tree(snapshot)
        except BridgeError as error:
            cleanup = {"state": "unverified", "error": error.as_dict()["error"]}
        with self._locked(job_id) as directory:
            current = self._read(directory)
            current["cancel_cleanup"] = cleanup
            self._save(directory, current)
            return dict(current)

    def _claim(self, job_id: str) -> dict | None:
        """Worker-only transition; tests exercise it in an actual child."""
        process = psutil.Process()
        with self._locked(job_id) as directory:
            status = self._read(directory)
            if status["cancel_requested"] or status["state"] in TERMINAL:
                return None
            if status["state"] == "running" and status.get("pid") != process.pid:
                raise BridgeError("JOB_ALREADY_RUNNING", "Another worker has already claimed this job.")
            status["pid"] = process.pid
            status["create_time"] = process.create_time()
            status["state"] = "running"
            status["started_at"] = now()
            self._save(directory, status)
            return dict(status)

    def _finish(self, job_id: str, state: str, report: dict, error: dict | None = None) -> dict:
        if state not in {"succeeded", "failed"}:
            raise BridgeError("INVALID_JOB_TRANSITION", "Workers may finish only as succeeded or failed.")
        with self._locked(job_id) as directory:
            status = self._read(directory)
            # Cancellation or interruption is durable. A late result cannot
            # rewrite the terminal state or replace its report.
            if status["cancel_requested"] or status["state"] in TERMINAL:
                return dict(status)
            atomic_json(directory / "report.json", report)
            status["state"] = state
            status["finished_at"] = now()
            if error is not None:
                status["error"] = error
            self._save(directory, status)
            return dict(status)

