"""Version-aware access to an installed Ren'Py SDK.

Ren'Py commands execute project initialization code. This adapter owns its
subprocesses and bounds their lifetime; it is not an OS security sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from typing import Mapping, Sequence

import psutil
from filelock import FileLock, Timeout


@dataclass(frozen=True)
class SdkInfo:
    root: Path
    version: str
    python_executable: Path
    entrypoint: Path


@dataclass(frozen=True)
class ProcessResult:
    argv: list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    process_id: int
    process_create_time: float | None
    terminated_process_ids: tuple[int, ...] = ()
    cleanup_complete: bool = True
    cleanup_diagnostics: tuple[str, ...] = ()


class SdkError(RuntimeError):
    """The SDK is missing or a version probe could not identify it."""


def _runtime_python(root: Path) -> Path:
    if sys.platform == "win32":
        return root / "lib" / "py3-windows-x86_64" / "python.exe"
    if sys.platform == "darwin":
        return root / "lib" / "py3-mac-universal" / "python"
    return root / "lib" / "py3-linux-x86_64" / "python"


def discover_sdk(root: Path | str) -> SdkInfo:
    """Validate an SDK directory and obtain its version from its own runtime."""
    root = Path(root).expanduser().resolve()
    python = _runtime_python(root)
    entry = root / "renpy.py"
    if not python.is_file() or not entry.is_file() or not (root / "gui" / "game").is_dir():
        raise SdkError(f"Not an installed Ren'Py SDK for this platform: {root}")
    result = _execute([str(python), str(entry), "--version"], root, 20.0, None)
    if result.timed_out or result.exit_code != 0:
        raise SdkError(f"SDK version probe failed: {result.stderr or result.stdout}")
    match = re.search(r"Ren['’]Py\s+(\d+\.\d+\.\d+(?:\.\d+)?)", result.stdout + result.stderr)
    if not match:
        raise SdkError(f"Unrecognized SDK version response: {result.stdout!r}")
    return SdkInfo(root=root, version=match.group(1), python_executable=python, entrypoint=entry)


def _execute(argv: list[str], cwd: Path, timeout_seconds: float,
             env_changes: Mapping[str, str] | None) -> ProcessResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    child_env = os.environ.copy()
    child_env.update({"RENPY_SIMPLE_EXCEPTIONS": "1", "PYTHONIOENCODING": "utf-8"})
    if env_changes:
        child_env.update(env_changes)
    started = time.monotonic()
    proc = subprocess.Popen(argv, cwd=cwd, env=child_env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        root_process = psutil.Process(proc.pid)
        root_created = root_process.create_time()
    except psutil.NoSuchProcess:
        # A very short command may have already exited. Popen still owns its
        # process handle and can collect the actual exit result safely.
        root_process = None
        root_created = None
    known: dict[int, float] = {proc.pid: root_created} if root_created is not None else {}

    def observe_children() -> None:
        if root_process is None:
            return
        try:
            if root_process.create_time() != root_created:
                return
            for descendant in root_process.children(recursive=True):
                try:
                    known[descendant.pid] = descendant.create_time()
                except psutil.NoSuchProcess:
                    pass
        except psutil.NoSuchProcess:
            pass

    timed_out = False
    killed: list[int] = []
    cleanup_complete = True
    cleanup_diagnostics: list[str] = []
    while True:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            timed_out = True
            # Freeze the owned root before the last descendant snapshot, so it
            # cannot create further children while its tree is being stopped.
            try:
                if root_process is not None and root_process.create_time() == root_created:
                    root_process.suspend()
            except psutil.NoSuchProcess:
                pass
            observe_children()
            for pid, created in reversed(list(known.items())):
                try:
                    owned = psutil.Process(pid)
                    if owned.create_time() == created:
                        owned.kill()
                        killed.append(pid)
                except psutil.NoSuchProcess:
                    pass
            # Popen's owned handle also covers the case where the process
            # exits before psutil can record its creation time.
            if proc.poll() is None:
                proc.kill()
                if proc.pid not in killed:
                    killed.append(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=15)
            except subprocess.TimeoutExpired as error:
                # A process that detached before the observation interval can
                # outlive its parent while retaining inherited pipe handles.
                # Return a bounded, explicitly incomplete cleanup result;
                # never kill an unproven process merely to close those pipes.
                cleanup_complete = False
                cleanup_diagnostics.append("Inherited output pipes remained open after owned process cleanup; an unobserved descendant may still exist.")
                stdout = error.output or ""
                stderr = error.stderr or ""
                if isinstance(stdout, bytes):
                    stdout = stdout.decode("utf-8", errors="replace")
                if isinstance(stderr, bytes):
                    stderr = stderr.decode("utf-8", errors="replace")
                # Windows reader threads may hold a buffered stream's lock.
                # Closing in daemon threads keeps the caller bounded without
                # unsafe raw-fd closure or blocking on an unknown descendant.
                for stream in (proc.stdout, proc.stderr):
                    if stream is not None:
                        def close_stream(pipe=stream):
                            try:
                                pipe.close()
                            except OSError:
                                pass
                        threading.Thread(target=close_stream, daemon=True).start()
            break
        observe_children()
        try:
            stdout, stderr = proc.communicate(timeout=min(0.25, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    return ProcessResult(argv=argv, cwd=str(cwd), exit_code=proc.returncode,
                         stdout=stdout, stderr=stderr,
                         duration_seconds=time.monotonic() - started, timed_out=timed_out,
                         process_id=proc.pid, process_create_time=root_created,
                         terminated_process_ids=tuple(killed), cleanup_complete=cleanup_complete,
                         cleanup_diagnostics=tuple(cleanup_diagnostics))


def run_sdk(sdk: SdkInfo, project: Path | str, command: str,
            args: Sequence[str] = (), *, cwd: Path | None = None,
            timeout_seconds: float = 60.0,
            env: Mapping[str, str] | None = None) -> ProcessResult:
    """Execute an argument-array SDK command, returning complete process evidence.

    ``project='launcher'`` selects SDK launcher commands such as generate_gui
    and distribute. Otherwise project is resolved to an absolute directory.
    Savedir and multipersistent isolation remain explicit caller choices.
    """
    if not command or "\x00" in command:
        raise ValueError("command must be a nonempty command name")
    if isinstance(args, str):
        raise TypeError("args must be a sequence of individual arguments")
    if any(not isinstance(argument, str) or "\x00" in argument for argument in args):
        raise TypeError("args must contain strings without NUL bytes")
    selected_project = "launcher" if project == "launcher" else str(Path(project).expanduser().resolve())
    workdir = Path(cwd).resolve() if cwd is not None else sdk.root
    argv = [str(sdk.python_executable), str(sdk.entrypoint), selected_project, command, *args]
    if project == "launcher":
        # Ren'Py 8.5.3 always adds launcher/game/saves even with --savedir.
        # Serialize launcher initialization/persistent writes across workers.
        try:
            with FileLock(str(sdk.root / ".bridge-launcher.lock"), timeout=300):
                return _execute(argv, workdir, timeout_seconds, env)
        except Timeout as exc:
            raise SdkError("SDK launcher is busy; retry this job after active launcher work completes") from exc
    return _execute(argv, workdir, timeout_seconds, env)
