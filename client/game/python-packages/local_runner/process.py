"""Suspended Windows process launch, Job lifetime, bounded pipe collection."""
import ctypes
from ctypes import wintypes
import os
import subprocess
import threading
import time


class Job:
    def __init__(self):
        if os.name != "nt":
            raise OSError("此构建只支持 Windows x64")
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k = k
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]

        class BASIC(ctypes.Structure):
            _fields_ = [("per_process", ctypes.c_int64), ("per_job", ctypes.c_int64), ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class EXTENDED(ctypes.Structure):
            _fields_ = [("basic", BASIC), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.handle = k.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = EXTENDED()
        limits.basic.flags = 0x2000 | 0x200  # KILL_ON_JOB_CLOSE | JOB_MEMORY
        limits.job_memory = 512 * 1024 * 1024
        if not k.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def attach_resume(self, process):
        if not self.k.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            raise ctypes.WinError(ctypes.get_last_error())
        # Popen closes the primary thread handle. Resume the process only after
        # successful assignment; a failed assignment never runs student code.
        resume = ctypes.WinDLL("ntdll").NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = ctypes.c_long
        if resume(wintypes.HANDLE(int(process._handle))) != 0:
            raise OSError("无法恢复受限子进程")

    def terminate(self):
        if self.handle:
            self.k.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None


def bounded_process(args, stdin, timeout, limit, cancel, cwd):
    start = time.monotonic()
    job, process = Job(), None
    chunks = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    lock = threading.Lock()
    overflow = threading.Event()
    readers = []
    reason = None
    try:
        if cancel.is_set():
            return {"status": "cancelled", "stdout": "", "stderr": "", "exit_code": None, "elapsed_ms": 0, "truncated": False}
        # No inherited WASMTIME_* configuration, credentials or custom PATH.
        environment = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=environment, shell=False, creationflags=0x08000000 | 0x00000004, bufsize=0)
        job.attach_resume(process)

        def collect(name, stream):
            nonlocal total
            while True:
                block = stream.read(2048)
                if not block:
                    return
                with lock:
                    available = max(0, limit - total)
                    chunks[name].extend(block[:available])
                    total += len(block)
                    if total > limit:
                        overflow.set()
                if overflow.is_set():
                    job.terminate()
                    return

        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            reader = threading.Thread(target=collect, args=(name, stream), daemon=True)
            reader.start()
            readers.append(reader)
        try:
            process.stdin.write(stdin)
            process.stdin.close()
        except BrokenPipeError:
            pass
        while process.poll() is None:
            if cancel.is_set():
                reason = "cancelled"
            elif overflow.is_set():
                reason = "output_limit"
            elif time.monotonic() - start > timeout:
                reason = "timeout"
            if reason:
                job.terminate()
                break
            time.sleep(0.01)
        process.wait(timeout=2)
        for reader in readers:
            reader.join(timeout=2)
        text = {name: bytes(value).decode("utf-8", errors="replace") for name, value in chunks.items()}
        if overflow.is_set():
            reason = "output_limit"
        if not reason and process.returncode != 0:
            reason = "timeout" if "fuel" in text["stderr"].lower() else "runtime_error"
        return dict(text, status=reason or "ok", exit_code=process.returncode, elapsed_ms=round((time.monotonic() - start) * 1000), truncated=overflow.is_set())
    finally:
        job.terminate()
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            for reader in readers:
                reader.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
        job.close()
