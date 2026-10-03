"""Portable story-side HTML runtime; native handles never enter Ren'Py saves.

The exported Ren'Py source commits JSON replacements on stable base-context
labels. Only a confirmed synchronous save permits an ACK. The process, job,
threads, queues and protocol session live in this imported module's globals.
"""
from __future__ import annotations

import copy
import ctypes
from ctypes import wintypes
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import queue
import re
import stat
import subprocess
import threading
import time
from uuid import uuid4

from .errors import BridgeError
from .minigame_packages import (
    validate_directory, validate_inputs, validate_manifest, validate_results,
    validate_snapshot, validate_typed_value,
)
from .minigame_protocol import (
    Capabilities, DEFAULT_LIMITS, Phase, ProtocolSession, TimeoutPolicy,
    serialize_message,
)
from . import minigame_windows

QA_ENVIRONMENT = "RENPY_BRIDGE_MINIGAME_QA_JSON"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL = {"completed", "cancelled", "error", "incompatible"}


def _error(code, message, **details):
    raise BridgeError(code, message, **details)


def _copy(value):
    """Detach Ren'Py revertable containers without retaining engine objects."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError, RecursionError) as exc:
        _error("MINIGAME_STATE_INVALID", "小游戏状态必须是有限的 JSON 数据", cause=str(exc))


def _json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()


def stable_label(frame_id):
    return "rb_frame_" + hashlib.sha256(frame_id.encode("utf-8")).hexdigest()[:24]


def variable_schema(definition):
    kind = {"int": "integer", "float": "number", "bool": "boolean", "str": "string"}.get(definition.get("type"))
    if kind is None or definition.get("scope", "save") != "save":
        _error("MINIGAME_VARIABLE_INVALID", "剧情变量类型或保存范围不合法")
    schema = {"type": kind}
    for key in ("minimum", "maximum"):
        if definition.get(key) is not None:
            schema[key] = definition[key]
    if definition.get("allowed_values") is not None:
        schema["enum"] = definition["allowed_values"]
    return schema


def validate_variables(definitions, values):
    values = _copy(values)
    if type(values) is not dict or set(values) != set(definitions):
        _error("MINIGAME_VARIABLE_INVALID", "剧情变量集合与工程定义不一致")
    for key, definition in definitions.items():
        validate_typed_value(variable_schema(definition), values[key])
        if type(values[key]) is int and abs(values[key]) > 2**53 - 1:
            _error("MINIGAME_VARIABLE_INVALID", "整数超出跨运行时精确范围", variable_id=key)
    return values


def resolve_binding(binding, variables, results=None):
    binding = _copy(binding)
    if type(binding) is not dict:
        _error("MINIGAME_BINDING_INVALID", "绑定必须是有类型的对象")
    kind = binding.get("kind")
    field = {"literal": "value", "variable": "variable_id", "result": "field"}.get(kind)
    if field is None or set(binding) != {"kind", field}:
        _error("MINIGAME_BINDING_INVALID", "绑定字段不合法")
    if kind == "literal":
        return _copy(binding["value"])
    source = variables if kind == "variable" else results
    if source is None or binding[field] not in source:
        _error("MINIGAME_BINDING_INVALID", "绑定引用不存在", reference=binding[field])
    return _copy(source[binding[field]])


def evaluate_predicate(predicate, variables, results, *, _depth=0):
    if predicate is None:
        return True
    if _depth > 32 or type(predicate) is not dict:
        _error("MINIGAME_PREDICATE_INVALID", "条件深度或结构不合法")
    op = predicate.get("op")
    conditions = predicate.get("conditions", [])
    if type(conditions) is not list or len(conditions) > 128:
        _error("MINIGAME_PREDICATE_INVALID", "条件子项超出限制")
    if op in {"all", "any", "not"}:
        if not conditions or op == "not" and len(conditions) != 1 or predicate.get("left") is not None or predicate.get("right") is not None:
            _error("MINIGAME_PREDICATE_INVALID", "逻辑条件参数不合法")
        answers = [evaluate_predicate(item, variables, results, _depth=_depth + 1) for item in conditions]
        return all(answers) if op == "all" else any(answers) if op == "any" else not answers[0]
    if conditions or op not in {"eq", "ne", "lt", "lte", "gt", "gte"}:
        _error("MINIGAME_PREDICATE_INVALID", "比较条件参数不合法")
    left = resolve_binding(predicate.get("left"), variables, results)
    right = resolve_binding(predicate.get("right"), variables, results)
    if op in {"eq", "ne"}:
        # bool/int equality must not silently erase the declared types.
        equal = (type(left) is type(right) or type(left) in {int, float} and type(right) in {int, float}) and left == right
        return equal if op == "eq" else not equal
    if not (type(left) in {int, float} and type(right) in {int, float} or type(left) is str and type(right) is str):
        _error("MINIGAME_PREDICATE_INVALID", "排序比较需要兼容的数字或文本")
    return {"lt": lambda: left < right, "lte": lambda: left <= right,
            "gt": lambda: left > right, "gte": lambda: left >= right}[op]()


def plan_finish(configuration, instance_id, variables, payload, *, entry_definition=None, manifest=None):
    """Validate the complete candidate before returning any story replacement."""
    instance = entry_definition or configuration["instances"][instance_id]
    manifest = manifest or configuration["minigames"][instance["game_id"]]["manifest"]
    validated = validate_results(manifest, payload["outcome"], payload["results"])
    original = validate_variables(configuration["variables"], variables)
    candidate = _copy(original)
    selected = None
    for branch in instance["result_branches"]:
        if branch["outcome"] == validated["outcome"] and evaluate_predicate(branch.get("predicate"), original, validated["results"]):
            selected = branch
            break
    if selected:
        effect_ids = set()
        for effect in selected["effects"]:
            if effect["effect_id"] in effect_ids:
                _error("MINIGAME_EFFECT_INVALID", "奖励身份重复")
            effect_ids.add(effect["effect_id"])
            key = effect["variable_id"]
            if key not in candidate:
                _error("MINIGAME_EFFECT_INVALID", "奖励变量不存在", variable_id=key)
            operand = resolve_binding(effect["value"], original, validated["results"])
            if effect["op"] == "set":
                candidate[key] = operand
            elif effect["op"] == "add" and type(candidate[key]) in {int, float} and type(operand) in {int, float}:
                candidate[key] += operand
            else:
                _error("MINIGAME_EFFECT_INVALID", "加法奖励仅接受数字")
        candidate = validate_variables(configuration["variables"], candidate)
    target = selected["target_frame_id"] if selected else instance["fallback_target_frame_id"]
    return candidate, {"outcome": validated["outcome"], "results": validated["results"],
                       "branch_id": selected["branch_id"] if selected else None,
                       "target_frame_id": target, "target_label": configuration["frame_labels"][target]}


def _relative_path(root, relative):
    if type(relative) is not str or "\\" in relative or ":" in relative:
        _error("MINIGAME_RUNTIME_PATH_INVALID", "运行资源路径不合法")
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(part in {".", ".."} for part in parts) or "/".join(parts) != relative:
        _error("MINIGAME_RUNTIME_PATH_INVALID", "运行资源必须使用安全相对路径")
    path = Path(root).joinpath(*parts)
    for ancestor in reversed((path, *path.parents)):
        if not ancestor.exists():
            continue
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _error("MINIGAME_RUNTIME_PATH_INVALID", "运行资源不能包含链接", path=str(ancestor))
    return path


def _hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inventory(root, inventory, required):
    root = Path(root)
    if type(inventory) is not dict or required not in inventory or len(inventory) > 4096:
        _error("MINIGAME_RUNTIME_INVALID", "运行资源清单缺失或过大")
    actual = {}
    folded = set()
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            _relative_path(root, relative)
            info = path.lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or relative.casefold() in folded:
                _error("MINIGAME_RUNTIME_INVALID", "运行资源包含特殊文件或大小写冲突")
            folded.add(relative.casefold())
            if stat.S_ISREG(info.st_mode):
                actual[relative] = {"sha256": _hash_file(path), "bytes": info.st_size}
    if actual != inventory:
        _error("MINIGAME_RUNTIME_CHANGED", "运行资源完整性检查失败")
    return root


class _WindowsJob:
    """Kill-on-close ownership of exactly one native host and its descendants."""
    def __init__(self):
        if os.name != "nt":
            _error("MINIGAME_PLATFORM_UNSUPPORTED", "当前发行仅支持 Windows x64")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel, self.handle = kernel, None
        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class Io(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.CreateJobObjectW(None, None)
        if not handle:
            _error("MINIGAME_PROCESS_OWNERSHIP_FAILED", "无法创建小游戏进程归属")
        self.handle = handle
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            self.close()
            _error("MINIGAME_PROCESS_OWNERSHIP_FAILED", "无法设置小游戏进程清理")

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, wintypes.HANDLE(int(process._handle))):
            _error("MINIGAME_PROCESS_OWNERSHIP_FAILED", "无法绑定小游戏进程归属", winerror=ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 0)
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _resume_owned_process(process):
    """Resume the one suspended child only after its kill job is attached."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    class ThreadEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD)]
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry))
    kernel.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry))
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        _error("MINIGAME_PROCESS_OWNERSHIP_FAILED", "无法恢复已归属的小游戏进程")
    resumed = 0
    try:
        entry = ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        found = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while found:
            if entry.th32OwnerProcessID == process.pid:
                handle = kernel.OpenThread(0x2, False, entry.th32ThreadID)
                if not handle:
                    _error("MINIGAME_PROCESS_OWNERSHIP_FAILED", "无法恢复小游戏执行线程")
                try:
                    if kernel.ResumeThread(handle) == 0xFFFFFFFF:
                        _error("MINIGAME_PROCESS_OWNERSHIP_FAILED", "小游戏执行线程恢复失败")
                    resumed += 1
                finally:
                    kernel.CloseHandle(handle)
            found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    if resumed != 1:
        _error("MINIGAME_PROCESS_OWNERSHIP_FAILED", "小游戏初始线程数量不符合隔离要求")


class NativeTransport:
    """Bounded nonblocking transport. It is never stored in the story store."""
    def __init__(self, command, environment, profile, *, job_factory=_WindowsJob):
        self.messages = queue.Queue(maxsize=128)
        self.writes = queue.Queue(maxsize=32)
        self.failure = None
        self.closing = threading.Event()
        self._failure_lock = threading.Lock()
        self.profile = Path(profile)
        self.profile.mkdir(parents=True, exist_ok=False)
        self.job = job_factory()
        self.process = None
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=environment, cwd=str(Path(command[0]).parent), shell=False, bufsize=0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | (0x4 if os.name == "nt" else 0))
            self.job.assign(self.process)
            if os.name == "nt":
                _resume_owned_process(self.process)
        except BaseException:
            if self.process is not None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.job.close()
            raise
        self.threads = [threading.Thread(target=target, daemon=True, name="rb-minigame-" + name)
                        for name, target in (("stdout", self._read), ("stderr", self._stderr), ("stdin", self._write))]
        for thread in self.threads:
            thread.start()

    def _fail(self, code, message):
        with self._failure_lock:
            if self.failure is None and not self.closing.is_set():
                self.failure = {"code": code, "message": message}

    def _read(self):
        try:
            while not self.closing.is_set():
                line = self.process.stdout.readline(DEFAULT_LIMITS.max_message_bytes + 2)
                if not line:
                    break
                if len(line) > DEFAULT_LIMITS.max_message_bytes + 1 or not line.endswith(b"\n"):
                    self._fail("MINIGAME_MESSAGE_TOO_LARGE", "小游戏返回消息超出限制")
                    break
                try:
                    self.messages.put_nowait(line.rstrip(b"\r\n"))
                except queue.Full:
                    self._fail("MINIGAME_QUEUE_EXCEEDED", "小游戏返回消息过于频繁")
                    break
        except (OSError, ValueError):
            self._fail("MINIGAME_CHANNEL_FAILED", "小游戏返回通道中断")

    def _stderr(self):
        # Keep reading after the file cap so a noisy child cannot block stdout.
        written = 0
        try:
            with (self.profile / "host-stderr.log").open("wb") as stream:
                while not self.closing.is_set():
                    chunk = self.process.stderr.read(4096)
                    if not chunk:
                        break
                    remaining = 2 * 1024 * 1024 - written
                    if remaining > 0:
                        stream.write(chunk[:remaining])
                        stream.flush()
                        written += min(remaining, len(chunk))
        except (OSError, ValueError):
            self._fail("MINIGAME_LOG_FAILED", "小游戏诊断记录无法写入")

    def _write(self):
        try:
            while not self.closing.is_set():
                try:
                    message = self.writes.get(timeout=0.2)
                except queue.Empty:
                    continue
                if message is None:
                    return
                if isinstance(message, threading.Event):
                    message.set()
                else:
                    self.process.stdin.write(message)
                    self.process.stdin.flush()
        except (OSError, ValueError):
            self._fail("MINIGAME_CHANNEL_FAILED", "小游戏控制通道中断")

    def send(self, message):
        if self.closing.is_set():
            _error("MINIGAME_CHANNEL_CLOSED", "小游戏已经关闭")
        try:
            self.writes.put_nowait((serialize_message(message) + "\n").encode("utf-8"))
        except queue.Full:
            _error("MINIGAME_QUEUE_EXCEEDED", "小游戏控制队列已满")

    def flush(self, timeout=2):
        barrier = threading.Event()
        try:
            self.writes.put_nowait(barrier)
        except queue.Full:
            _error("MINIGAME_QUEUE_EXCEEDED", "小游戏控制队列已满")
        if not barrier.wait(timeout) or self.failure:
            _error("MINIGAME_CHANNEL_FAILED", "小游戏确认消息无法发送")

    def close(self, *, graceful=False):
        if self.closing.is_set():
            return
        if graceful:
            try:
                self.process.wait(timeout=0.8)
            except subprocess.TimeoutExpired:
                pass
        self.closing.set()
        self.job.close()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        for thread in self.threads:
            thread.join(timeout=0.3)


class RuntimeManager:
    """Story main-thread API. Transport readers only enqueue immutable bytes."""
    def __init__(self, basedir, savedir, configuration, *, allow_qa=False,
                 transport_factory=NativeTransport, clock=time.monotonic, parent_window_provider=None, settings_provider=None):
        self.basedir, self.savedir = Path(basedir).absolute(), Path(savedir).absolute()
        self.configuration = _copy(configuration)
        self.allow_qa = bool(allow_qa)
        self.transport_factory, self.clock = transport_factory, clock
        self.parent_window_provider = parent_window_provider
        self.settings_provider = settings_provider
        self.owner_thread = threading.get_ident()
        self.epoch = 0
        self.active = None
        self._deferred_parent = None
        self._loaded_menu_entry = None
        self._loaded_menu_terminal = None
        self.quitting = False
        self.qa = None
        self.last_error = None
        self._validate_configuration()
        if self.allow_qa and os.environ.get(QA_ENVIRONMENT):
            qa_path = Path(os.environ[QA_ENVIRONMENT])
            if qa_path.stat().st_size > 16 * 1024:
                _error("MINIGAME_QA_INVALID", "开发诊断设置过大")
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            if (type(qa) is not dict or set(qa) != {"development", "devtools_port", "event_log_path"}
                    or qa["development"] is not True or type(qa["devtools_port"]) is not int
                    or not 1024 <= qa["devtools_port"] <= 65535):
                _error("MINIGAME_QA_INVALID", "开发诊断设置不合法")
            event_path = Path(qa["event_log_path"]).absolute()
            if not event_path.is_relative_to(self.savedir):
                _error("MINIGAME_QA_INVALID", "开发记录必须位于当前游戏保存目录")
            self.qa = {**qa, "event_log_path": str(event_path)}

    def _main(self):
        if threading.get_ident() != self.owner_thread:
            _error("MINIGAME_MAIN_THREAD_REQUIRED", "剧情状态只能在主线程提交")

    def _validate_configuration(self):
        c = self.configuration
        if c.get("schema_version") != "1.0" or not all(type(c.get(key)) is dict for key in ("host", "variables", "minigames", "instances", "frame_labels")):
            _error("MINIGAME_CONFIG_INVALID", "小游戏运行配置不合法")
        if c["host"].get("window_mode") != "embedded_same_top_level_window" or c["host"].get("footer_fraction") != 0.1:
            _error("MINIGAME_CONFIG_INVALID", "小游戏必须嵌入同一剧情窗口并保留操作栏")
        for frame, label in c["frame_labels"].items():
            if type(frame) is not str or label != stable_label(frame):
                _error("MINIGAME_CONFIG_INVALID", "剧情跳转必须使用静态帧映射")
        for key, definition in c["variables"].items():
            if definition.get("variable_id") != key:
                _error("MINIGAME_CONFIG_INVALID", "剧情变量身份不一致")
            validate_typed_value(variable_schema(definition), definition["default"])
        for digest, retained in c.get("retained_games", {}).items():
            if digest != retained.get("package_digest"):
                _error("MINIGAME_CONFIG_INVALID", "保留的小游戏旧包摘要不一致")
        games = list(c["minigames"].items()) + [(value["game_id"], value) for value in c.get("retained_games", {}).values()]
        for key, game in games:
            manifest = validate_manifest(game["manifest"], allow_prototype=self.allow_qa)
            if (game["game_id"] != key or manifest["game_id"] != key or manifest["version"] != game["package_version"]
                    or not _HASH.fullmatch(game["package_digest"])
                    or game["package_root"] != "game/bridge_minigames/" + game["package_digest"]):
                _error("MINIGAME_CONFIG_INVALID", "小游戏注册身份不一致")
        for key, instance in c["instances"].items():
            if instance.get("instance_id") != key or instance.get("kind") != "minigame" or instance.get("game_id") not in c["minigames"]:
                _error("MINIGAME_CONFIG_INVALID", "小游戏调用身份不一致")
            for field in ("cancel_target_frame_id", "error_target_frame_id", "fallback_target_frame_id"):
                if instance.get(field) not in c["frame_labels"]:
                    _error("MINIGAME_CONFIG_INVALID", "小游戏出口帧不存在")
            if instance.get("save_mode") not in {"entry_restart", "checkpoint"}:
                _error("MINIGAME_CONFIG_INVALID", "小游戏保存策略不合法")
            manifest = c["minigames"][instance["game_id"]]["manifest"]
            if not manifest["save_capabilities"][instance["save_mode"]]:
                _error("MINIGAME_CONFIG_INVALID", "小游戏不支持指定保存策略")
            for branch in instance["result_branches"]:
                if branch["target_frame_id"] not in c["frame_labels"] or branch["outcome"] not in manifest["results"]["outcomes"]:
                    _error("MINIGAME_CONFIG_INVALID", "小游戏结果出口不合法")

    def _event(self, kind, **data):
        if self.qa:
            path = Path(self.qa["event_log_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 8 * 1024 * 1024:
                return
            try:
                with path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"type": kind, "time": time.time(), "load_epoch": self.epoch, **_copy(data)}, ensure_ascii=False, allow_nan=False) + "\n")
            except OSError:
                # Diagnostics do not turn an already committed award into an error.
                self.qa = None

    def _state_identity(self, state):
        if type(state) is not dict or state.get("instance_id") not in self.configuration["instances"]:
            _error("MINIGAME_STATE_INVALID", "小游戏逻辑状态缺少调用身份")
        for key in ("logical_entry_id", "attempt_id", "session_id"):
            if type(state.get(key)) is not str or not re.fullmatch(r"[0-9a-f]{32}", state[key]):
                _error("MINIGAME_STATE_INVALID", "小游戏运行身份不合法", field=key)
        if type(state.get("load_epoch")) is not int or state["load_epoch"] < 0:
            _error("MINIGAME_STATE_INVALID", "小游戏读档代次不合法")
        game = self.configuration["minigames"][self.configuration["instances"][state["instance_id"]]["game_id"]]
        if state.get("package_digest") != game["package_digest"]:
            game = self.configuration.get("retained_games", {}).get(state.get("package_digest"))
            if game is None:
                _error("MINIGAME_SAVE_PACKAGE_CHANGED", "存档所需小游戏旧版本未随发行包保留")
        if any(state.get(key) != game[key] for key in ("game_id", "package_digest", "package_version")):
            _error("MINIGAME_SAVE_PACKAGE_CHANGED", "当前小游戏版本与存档不一致")
        definition = state.get("entry_definition")
        if type(definition) is not dict or definition.get("instance_id") != state["instance_id"] or definition.get("game_id") != state["game_id"]:
            _error("MINIGAME_SAVE_FLOW_CHANGED", "存档缺少已冻结的小游戏结果规则")
        if state.get("entry_definition_digest") != _json_digest(definition):
            _error("MINIGAME_SAVE_FLOW_CHANGED", "存档结果规则摘要不一致")
        if state.get("variable_definitions_digest") != _json_digest(self.configuration["variables"]):
            _error("MINIGAME_SAVE_VARIABLES_CHANGED", "剧情变量定义已更新，需要显式存档迁移")
        targets = [definition.get(field) for field in ("cancel_target_frame_id", "error_target_frame_id", "fallback_target_frame_id")]
        targets.extend(branch["target_frame_id"] for branch in definition["result_branches"])
        if any(target not in self.configuration["frame_labels"] for target in targets):
            _error("MINIGAME_SAVE_TARGET_REMOVED", "存档的小游戏出口剧情已移除")
        return game

    def prepare(self, instance_id, state, variables):
        self._main()
        if self.quitting:
            _error("MINIGAME_RUNTIME_CLOSED", "游戏正在退出")
        if state is not None and state.get("instance_id") == instance_id:
            state = _copy(state)
            self._state_identity(state)
            if state.get("phase") not in _TERMINAL:
                return state
            # A new call after a completed entry is a deliberate new visit.
            # Loaded completed saves resume at finish_save/done, not this call.
        self.close_active("new_entry")
        instance = self.configuration["instances"][instance_id]
        game = self.configuration["minigames"][instance["game_id"]]
        variables = validate_variables(self.configuration["variables"], variables)
        for binding in instance["inputs"].values():
            if binding.get("kind") == "result":
                _error("MINIGAME_BINDING_INVALID", "小游戏入口不能引用尚未产生的结果")
        inputs = validate_inputs(game["manifest"], {key: resolve_binding(binding, variables) for key, binding in instance["inputs"].items()})
        state = {"format_version": "1.0", "instance_id": instance_id, "logical_entry_id": uuid4().hex,
                 "attempt_id": uuid4().hex, "session_id": uuid4().hex, "load_epoch": self.epoch,
                 "game_id": game["game_id"], "package_digest": game["package_digest"], "package_version": game["package_version"],
                 "save_mode": instance["save_mode"], "inputs": inputs, "snapshot": None,
                 "entry_definition": _copy(instance), "entry_definition_digest": _json_digest(instance),
                 "variable_definitions_digest": _json_digest(self.configuration["variables"]),
                 "phase": "entry", "stage": "entry", "last_commit": None, "completion": None, "error": None}
        self._event("entry", state=state)
        return state

    def ensure_started(self, state):
        self._main()
        state = _copy(state)
        game = self._state_identity(state)
        if state["phase"] in _TERMINAL or self.quitting:
            return state
        if self.active is not None:
            if self.active["attempt_id"] == state["attempt_id"] and not self.active["closing"]:
                return state
            self.close_active("replace_attempt")
        if state["load_epoch"] != self.epoch:
            _error("MINIGAME_LOAD_EPOCH_MISMATCH", "小游戏状态属于此前读档")
        host = self.configuration["host"]
        executable = _relative_path(self.basedir, host["relative_executable"])
        verify_inventory(executable.parent, host["files"], executable.name)
        if host["sha256"] != host["files"][executable.name]["sha256"]:
            _error("MINIGAME_RUNTIME_CHANGED", "宿主程序身份不一致")
        fixed = self.configuration.get("fixed_runtime")
        if not fixed:
            _error("MINIGAME_BROWSER_MISSING", "发行包缺少固定浏览器运行资源")
        browser = _relative_path(self.basedir, fixed["relative_root"])
        verify_inventory(browser, fixed["files"], "msedgewebview2.exe")
        package = validate_directory(_relative_path(self.basedir, game["package_root"]), allow_prototype=self.allow_qa)
        if package.digest != game["package_digest"] or package.manifest != game["manifest"]:
            _error("MINIGAME_PACKAGE_CHANGED", "小游戏包已被修改")
        profile = self.savedir / "html-minigames" / state["attempt_id"]
        if self.parent_window_provider is None:
            _error("MINIGAME_PARENT_WINDOW_MISSING", "没有当前剧情窗口的原生接口")
        parent = _copy(self.parent_window_provider())
        self._validate_parent(parent)
        if self._deferred_parent is not None:
            self._validate_parent(parent, self._deferred_parent["parent"])
        if not self._presentable(parent):
            if self._deferred_parent is None:
                self._event("window_entry_deferred", attempt_id=state["attempt_id"], session_id=state["session_id"], parent=parent)
            self._deferred_parent = {"attempt_id": state["attempt_id"], "load_epoch": self.epoch, "parent": parent}
            return state
        self._deferred_parent = None
        command = [str(executable), "--package-root", str(package.root), "--entry", package.manifest["entry"],
                   "--session-id", state["session_id"], "--game-id", game["game_id"], "--package-version", game["package_version"],
                   "--package-hash", game["package_digest"], "--user-data-root", str(profile / "webview")]
        command.extend(["--parent-window-handle", str(parent["hwnd"]), "--parent-process-id", str(parent["process_id"]),
                        "--footer-fraction", str(self.configuration["host"]["footer_fraction"])])
        if "geometry" not in parent:
            _error("MINIGAME_VIEWPORT_MISSING", "剧情渲染器没有提供精确嵌入区域")
        command.extend(["--viewport-rect", ",".join(str(value) for value in parent["geometry"]["viewport"]),
                        "--parent-client-size", ",".join(str(value) for value in parent["geometry"]["parent_client"])])
        if self.qa:
            command.extend(["--development", "--devtools-port", str(self.qa["devtools_port"])])
        environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("WEBVIEW2_")}
        environment["WEBVIEW2_BROWSER_EXECUTABLE_FOLDER"] = str(browser)
        manifest = package.manifest
        presentation_clock = {"offset": 0.0, "suspended_at": None}
        def session_clock():
            now = self.clock() if presentation_clock["suspended_at"] is None else presentation_clock["suspended_at"]
            return now - presentation_clock["offset"]
        session = ProtocolSession(session_id=state["session_id"], channel_id=uuid4().hex, load_epoch=self.epoch,
            game_id=game["game_id"], package_version=game["package_version"], package_hash=game["package_digest"],
            capabilities=Capabilities(pause=True, checkpoint=manifest["save_capabilities"]["checkpoint"]),
            timeouts=TimeoutPolicy(load_seconds=30, gameplay_seconds=None, commit_seconds=120),
            input_validator=lambda value: validate_inputs(manifest, value),
            result_validator=lambda value: validate_results(manifest, value["outcome"], value["results"]),
            snapshot_validator=lambda value: validate_snapshot(manifest, value), clock=session_clock)
        transport = self.transport_factory(command, environment, profile)
        self.active = {"attempt_id": state["attempt_id"], "session": session, "transport": transport,
                       "closing": False, "cancel_requested": False, "parent": parent,
                       "presentation_suspended": False, "presentation_pause_owned": False,
                       "presentation_clock": presentation_clock}
        self._loaded_menu_entry = None
        self._loaded_menu_terminal = None
        state.update(phase="waiting", stage="waiting")
        self._event("launch", session_id=state["session_id"], attempt_id=state["attempt_id"],
                    instance_id=state["instance_id"], package_digest=game["package_digest"], fixed_runtime_version=fixed["version"],
                    development=self.qa is not None, parent=parent, window_mode="embedded_same_top_level_window")
        return state

    def _settings(self):
        settings = {"audio_volume": 1.0} if self.settings_provider is None else _copy(self.settings_provider())
        volume = settings.get("audio_volume") if type(settings) is dict else None
        if type(settings) is not dict or set(settings) != {"audio_volume"} or type(volume) not in {int, float} or not math.isfinite(volume) or not 0 <= volume <= 1:
            _error("MINIGAME_SETTINGS_INVALID", "小游戏音量必须为0到1的有限数值")
        return settings

    def _validate_parent(self, parent, previous=None):
        if (type(parent.get("hwnd")) is not int or parent["hwnd"] <= 0
                or parent.get("process_id") != os.getpid() or parent.get("root_hwnd") != parent["hwnd"]):
            _error("MINIGAME_PARENT_WINDOW_INVALID", "原生接口必须返回当前剧情进程自己的顶层窗口")
        if previous is not None and parent["hwnd"] != previous["hwnd"]:
            _error("MINIGAME_PARENT_WINDOW_CHANGED", "剧情主窗口已重新创建，小游戏必须安全关闭")

    def _presentable(self, parent):
        if parent.get("presentable", True) is False:
            if ("geometry" in parent or parent.get("presentation_reason") not in
                    {"minimized", "zero_client", "renderer_resize_pending"}):
                _error("MINIGAME_VIEWPORT_INVALID", "暂不可呈现的窗口不能使用伪造的旧布局")
            return False
        if parent.get("presentable", True) is not True or type(parent.get("geometry")) is not dict:
            _error("MINIGAME_VIEWPORT_MISSING", "剧情渲染器没有提供精确嵌入区域")
        return True

    def _pause_for_presentation(self, active):
        session = active["session"]
        if (active["presentation_suspended"] and session.capabilities.pause
                and session.phase == Phase.RUNNING
                and not active["presentation_pause_owned"]):
            active["transport"].send(session.pause(reason="window_unpresentable"))
            active["presentation_pause_owned"] = True

    def _update_geometry(self, active):
        parent = _copy(self.parent_window_provider())
        self._validate_parent(parent, active["parent"])
        clock = active["presentation_clock"]
        if not self._presentable(parent):
            if not active["presentation_suspended"]:
                clock["suspended_at"] = self.clock()
                active["presentation_suspended"] = True
                self._event("window_suspended", attempt_id=active["attempt_id"],
                            session_id=active["session"].session_id, parent=parent)
            self._pause_for_presentation(active)
            return False
        restoring = active["presentation_suspended"]
        if restoring:
            clock["offset"] += max(0.0, self.clock() - clock["suspended_at"])
            clock["suspended_at"] = None
            active["presentation_suspended"] = False
        if restoring or parent["geometry"] != active["parent"]["geometry"]:
            active["transport"].send(active["session"].layout(parent["geometry"]))
            self._event("layout", geometry=parent["geometry"], session_id=active["session"].session_id)
        active["parent"] = parent
        # Only resume a pause owned by presentation, never a native menu or
        # an uncommitted checkpoint/finish. Checkpoint ACK can make this ready
        # on a later poll, after its original persistence contract completes.
        if active["presentation_pause_owned"] and active["session"].phase == Phase.PAUSED:
            active["transport"].send(active["session"].settings(self._settings()))
            active["transport"].send(active["session"].resume())
            active["presentation_pause_owned"] = False
        if restoring:
            self._event("window_restored", attempt_id=active["attempt_id"],
                        session_id=active["session"].session_id, parent=parent)
        return True

    def entry_replacement(self, state):
        self._main()
        candidate = _copy(state)
        if candidate.get("phase") in _TERMINAL:
            return candidate
        self._state_identity(candidate)
        receipt_id = hashlib.sha256((candidate["logical_entry_id"] + ":" + candidate["attempt_id"] + ":entry").encode()).hexdigest()
        candidate["last_commit"] = {"receipt_id": receipt_id, "kind": "entry", "logical_entry_id": candidate["logical_entry_id"],
            "attempt_id": candidate["attempt_id"], "package_digest": candidate["package_digest"], "load_epoch": self.epoch}
        return candidate

    def poll(self, state):
        self._main()
        if state is None:
            return "error"
        if state.get("phase") in _TERMINAL:
            return state["phase"]
        active = self.active
        if active is None:
            if (not self.quitting and self._deferred_parent is not None
                    and self._deferred_parent["attempt_id"] == state.get("attempt_id")
                    and self._deferred_parent["load_epoch"] == self.epoch == state.get("load_epoch")):
                try:
                    parent = _copy(self.parent_window_provider())
                    self._validate_parent(parent, self._deferred_parent["parent"])
                    return "restart" if self._presentable(parent) else "waiting"
                except Exception as exc:
                    self.last_error = {"code": getattr(exc, "code", "MINIGAME_CHANNEL_FAILED"), "message": str(exc)}
                    self._event("error", error=self.last_error)
                    return "error"
            return "restart" if not self.quitting else "cancel"
        if active["attempt_id"] != state["attempt_id"] or active["session"].load_epoch != self.epoch:
            return "restart"
        if active["cancel_requested"]:
            return "cancel"
        session, transport = active["session"], active["transport"]
        if transport.failure:
            self.last_error = _copy(transport.failure)
            return "error"
        try:
            self._update_geometry(active)
            for _ in range(128):
                try:
                    raw = transport.messages.get_nowait()
                except queue.Empty:
                    break
                decision = session.receive(raw, channel_id=session.channel_id, load_epoch=self.epoch)
                self._event("message", message=decision.message.to_dict(), action=decision.action)
                if decision.ack:
                    transport.send(decision.ack)
                elif decision.message.type == "ready" and decision.action == "accepted":
                    transport.send(session.init(inputs=state["inputs"], snapshot=state["snapshot"], settings=self._settings()))
                    transport.send(session.start())
                    self._pause_for_presentation(active)
                elif decision.message.type == "error" and session.phase == Phase.FAILED:
                    self.last_error = decision.message.to_dict()["payload"]
                if session.pending is not None:
                    return "checkpoint" if session.pending.message.type == "checkpoint" else "finish"
                if session.phase == Phase.CANCELLED:
                    return "cancel"
                if session.phase == Phase.FAILED:
                    return "error"
            timeout = session.timeout_status()
            if timeout:
                self.last_error = {"code": "MINIGAME_" + timeout.kind.value.upper() + "_TIMEOUT", "message": "小游戏等待超时"}
                return "error"
            if transport.process.poll() is not None:
                self.last_error = {"code": "MINIGAME_HOST_EXITED", "message": "小游戏窗口意外退出"}
                return "error"
            return "waiting"
        except Exception as exc:
            self.last_error = {"code": getattr(exc, "code", "MINIGAME_CHANNEL_FAILED"), "message": str(exc)}
            self._event("error", error=self.last_error)
            return "error"

    def _pending(self, state, kind):
        self._main()
        if self.active is None or self.active["closing"] or self.active["attempt_id"] != state["attempt_id"]:
            _error("MINIGAME_ATTEMPT_EXPIRED", "小游戏尝试已过期")
        session = self.active["session"]
        event = session.pending
        if event is None or event.message.type != kind or event.load_epoch != self.epoch:
            _error("MINIGAME_COMMIT_EXPIRED", "小游戏提交已过期")
        return event

    def _receipt(self, state, pending):
        digest = hashlib.sha256((state["logical_entry_id"] + ":" + state["attempt_id"] + ":" + pending.fingerprint).encode()).hexdigest()
        return {"receipt_id": digest, "event_id": pending.message.event_id, "fingerprint": pending.fingerprint,
                "kind": pending.message.type, "logical_entry_id": state["logical_entry_id"], "attempt_id": state["attempt_id"],
                "package_digest": state["package_digest"], "load_epoch": self.epoch}

    def checkpoint_replacement(self, state):
        pending = self._pending(state, "checkpoint")
        candidate = _copy(state)
        manifest = self._state_identity(candidate)["manifest"]
        snapshot = validate_snapshot(manifest, pending.message.to_dict()["payload"])
        candidate["snapshot"] = snapshot if candidate["save_mode"] == "checkpoint" else None
        candidate.update(phase="checkpoint", stage="checkpoint", last_commit=self._receipt(candidate, pending))
        return candidate

    def finish_replacement(self, state, variables):
        self._main()
        if state.get("phase") == "completed":
            return _copy(variables), _copy(state)
        pending = self._pending(state, "finish")
        game = self._state_identity(state)
        candidate_variables, completion = plan_finish(self.configuration, state["instance_id"], variables, pending.message.to_dict()["payload"],
                                                      entry_definition=state["entry_definition"], manifest=game["manifest"])
        candidate = _copy(state)
        receipt = self._receipt(candidate, pending)
        completion["receipt_id"] = receipt["receipt_id"]
        candidate.update(phase="completed", stage="completed", last_commit=receipt, completion=completion,
                         target_frame_id=completion["target_frame_id"], target_label=completion["target_label"])
        return candidate_variables, candidate

    def acknowledge(self, state, *, persisted_receipt):
        self._main()
        receipt = state.get("last_commit")
        if type(receipt) is not dict or persisted_receipt != receipt:
            _error("MINIGAME_PERSISTENCE_UNCONFIRMED", "存档确认与本次提交不一致")
        if receipt["load_epoch"] != self.epoch or self.active is None or self.active["attempt_id"] != receipt["attempt_id"]:
            # A saved continuation may execute its ACK statement after load.
            # It must never contact the old channel or ACK a newly opened game.
            return _copy(state)
        session, transport = self.active["session"], self.active["transport"]
        ack = session.commit(receipt["event_id"], persisted_receipt=receipt, load_epoch=self.epoch)
        transport.send(ack)
        if receipt["kind"] == "finish":
            transport.flush()
        self._event("committed", receipt=receipt, completion=state.get("completion"))
        candidate = _copy(state)
        if receipt["kind"] == "checkpoint":
            candidate.update(phase="waiting", stage="waiting")
        return candidate

    def request_cancel(self):
        self._main()
        if self.active is not None and not self.active["closing"]:
            self.active["cancel_requested"] = True
            try:
                self.active["transport"].send(self.active["session"].cancel(reason="player_return_to_story"))
            except BridgeError:
                pass

    def can_pause(self, state):
        self._main()
        return bool(self.active and not self.active["closing"] and self.active["attempt_id"] == state.get("attempt_id")
                    and self.active["session"].capabilities.pause and (self.active["session"].phase == Phase.RUNNING
                    or (self.active["session"].phase == Phase.PAUSED and self.active["presentation_pause_owned"])))

    def pause_for_menu(self, state):
        self._main()
        # FileLoad can resume this very statement, before call screen, after
        # after_load has invalidated the saved browser and returned an entry.
        # Keep the native menu open without starting a new child behind it.
        if (self.active is None and not self.quitting and type(state) is dict
                and state.get("phase") in _TERMINAL
                and self._loaded_menu_terminal == (self.epoch, _json_digest(state))):
            self._event("menu_loaded_terminal", phase=state["phase"])
            return _copy(state)
        if (self.active is None and not self.quitting and type(state) is dict
                and state.get("phase") == "entry" and state.get("load_epoch") == self.epoch
                and self._loaded_menu_entry == tuple(state.get(key) for key in
                    ("instance_id", "logical_entry_id", "attempt_id", "session_id", "load_epoch"))):
            self._state_identity(state)
            self._event("menu_reopened_after_load", session_id=state["session_id"])
            return _copy(state)
        if not self.can_pause(state):
            _error("MINIGAME_PAUSE_UNAVAILABLE", "小游戏当前不能暂停")
        if self.active["presentation_pause_owned"] and self.active["session"].phase == Phase.PAUSED:
            self.active["presentation_pause_owned"] = False
        else:
            self.active["transport"].send(self.active["session"].pause(reason="renpy_menu"))
        self.active["transport"].flush()
        candidate = _copy(state)
        candidate.update(phase="paused", stage="paused")
        self._event("menu_paused", session_id=state["session_id"])
        return candidate

    def resume_after_menu(self, state):
        self._main()
        candidate = _copy(state)
        if candidate.get("phase") in _TERMINAL or self.active is None:
            return candidate
        if self.active["attempt_id"] != candidate["attempt_id"]:
            return candidate
        presentable = self._update_geometry(self.active)
        self.active["transport"].send(self.active["session"].settings(self._settings()))
        if presentable:
            if self.active["session"].phase == Phase.PAUSED:
                self.active["transport"].send(self.active["session"].resume())
        else:
            self.active["presentation_pause_owned"] = True
        candidate.update(phase="waiting", stage="waiting")
        self._event("menu_resumed", session_id=state["session_id"])
        return candidate

    def development_host_running(self, state):
        self._main()
        active = self.active
        return bool(self.qa and active and not active["closing"] and type(state) is dict
                    and state.get("attempt_id") == active["attempt_id"]
                    and state.get("session_id") == active["session"].session_id
                    and state.get("load_epoch") == self.epoch == active["session"].load_epoch
                    and active["session"].phase == Phase.RUNNING and active["transport"].process.poll() is None)

    def development_terminate_own_host(self, state):
        self._main()
        if not self.development_host_running(state):
            _error("MINIGAME_QA_FAULT_FORBIDDEN", "当前会话未启用已认证的小游戏故障验证")
        active = self.active
        receipt = {"pid": active["transport"].process.pid, "session_id": active["session"].session_id,
                   "attempt_id": active["attempt_id"], "load_epoch": self.epoch, "fault": "terminate_own_host"}
        active["transport"].job.close()
        self._event("development_owned_host_terminated", **receipt)
        return receipt

    def terminal_replacement(self, state, outcome):
        self._main()
        if outcome not in {"cancel", "error"}:
            _error("MINIGAME_RESULT_INVALID", "关闭原因不合法")
        candidate = _copy(state)
        instance = candidate.get("entry_definition") or self.configuration["instances"][candidate["instance_id"]]
        target = instance[outcome + "_target_frame_id"]
        if target not in self.configuration["frame_labels"]:
            target = self.configuration["instances"][candidate["instance_id"]][outcome + "_target_frame_id"]
        completion = {"outcome": outcome, "branch_id": None, "results": {}, "target_frame_id": target,
                      "target_label": self.configuration["frame_labels"][target], "receipt_id": None}
        candidate.update(phase="cancelled" if outcome == "cancel" else "error", stage="cancelled" if outcome == "cancel" else "error",
                         completion=completion, target_frame_id=target, target_label=completion["target_label"],
                         error=_copy(self.last_error) if outcome == "error" else None)
        self.close_active(outcome)
        self._event("terminal", state=candidate)
        return candidate

    def recover_loaded_failure(self, state, error):
        self._main()
        self.close_active("incompatible_load")
        self.last_error = {"code": getattr(error, "code", "MINIGAME_SAVE_INCOMPATIBLE"), "message": str(error)[:512]}
        if type(state) is not dict or state.get("instance_id") not in self.configuration["instances"]:
            candidate = incompatible_state(state, error)
        else:
            try:
                candidate = self.terminal_replacement(state, "error")
            except Exception:
                candidate = incompatible_state(state, error)
        self._loaded_menu_terminal = (self.epoch, _json_digest(candidate))
        return candidate

    def after_load(self, state):
        self._main()
        self.close_active("after_load")
        self.epoch += 1
        self.quitting = False
        self.last_error = None
        if state is None:
            return None
        candidate = _copy(state)
        self._state_identity(candidate)
        if candidate.get("phase") in _TERMINAL:
            self._loaded_menu_terminal = (self.epoch, _json_digest(candidate))
            return candidate
        candidate.update(attempt_id=uuid4().hex, session_id=uuid4().hex, load_epoch=self.epoch, phase="entry", stage="entry")
        if candidate["save_mode"] == "entry_restart":
            candidate["snapshot"] = None
        elif candidate["snapshot"] is not None:
            validate_snapshot(self._state_identity(candidate)["manifest"], candidate["snapshot"])
        self._loaded_menu_entry = tuple(candidate[key] for key in
            ("instance_id", "logical_entry_id", "attempt_id", "session_id", "load_epoch"))
        self._event("after_load", state=candidate)
        return candidate

    def close_active(self, reason="story_return"):
        self._main()
        self._deferred_parent = None
        self._loaded_menu_entry = None
        self._loaded_menu_terminal = None
        if self.active is None:
            return
        active = self.active
        active["closing"] = True
        graceful = active["session"].phase == Phase.COMPLETED
        active["session"].invalidate()
        active["transport"].close(graceful=graceful)
        if os.name == "nt" and self.transport_factory is NativeTransport:
            focused = minigame_windows.restore_parent_focus(active["parent"])
            self._event("sdl_focus_restored", result=focused, parent=active["parent"])
        self.active = None
        self._event("close", reason=reason, attempt_id=active["attempt_id"])

    def shutdown(self):
        self._main()
        self.quitting = True
        self.close_active("game_exit")

    def target(self, state):
        self._main()
        completion = state.get("completion")
        if state.get("phase") not in _TERMINAL or not completion:
            _error("MINIGAME_TARGET_UNCOMMITTED", "小游戏尚未产生可跳转结果")
        target = completion["target_frame_id"]
        expected = self.configuration["frame_labels"].get(target)
        if expected is None or completion["target_label"] != expected:
            _error("MINIGAME_TARGET_INVALID", "小游戏出口不在静态剧情映射中")
        return expected


_MANAGER = None


def configure(basedir, savedir, *, allow_qa=False, parent_window_provider=None, settings_provider=None):
    global _MANAGER
    if _MANAGER is None:
        path = Path(basedir) / "game" / "bridge-minigames.json"
        if path.stat().st_size > 16 * 1024 * 1024:
            _error("MINIGAME_CONFIG_INVALID", "小游戏配置过大")
        configuration = json.loads(path.read_text(encoding="utf-8"))
        _MANAGER = RuntimeManager(basedir, savedir, configuration, allow_qa=allow_qa, parent_window_provider=parent_window_provider,
                                  settings_provider=settings_provider)
    return _MANAGER


def manager():
    if _MANAGER is None:
        _error("MINIGAME_RUNTIME_UNINITIALIZED", "小游戏运行时尚未初始化")
    return _MANAGER


def shutdown():
    if _MANAGER is not None:
        _MANAGER.shutdown()


def development_host_running(state):
    return manager().development_host_running(_copy(state))


def development_terminate_own_host(state):
    return manager().development_terminate_own_host(_copy(state))


def incompatible_state(state, error):
    try:
        candidate = _copy(state) if state is not None else {}
    except Exception:
        candidate = {}
    if type(candidate) is not dict:
        candidate = {}
    code = getattr(error, "code", "MINIGAME_SAVE_INCOMPATIBLE")
    messages = {"MINIGAME_SAVE_PACKAGE_CHANGED": "这份存档所需的小游戏版本不在当前游戏中。",
                "MINIGAME_SAVE_TARGET_REMOVED": "这份存档对应的剧情出口已被移除。",
                "MINIGAME_SAVE_VARIABLES_CHANGED": "这份存档的剧情变量与当前版本不兼容。",
                "MINIGAME_STATE_INVALID": "这份存档的小游戏调用已移除或状态不兼容。"}
    candidate.update(phase="incompatible", stage="incompatible", completion=None,
                     error={"code": code, "message": messages.get(code, "当前游戏版本无法继续这份小游戏存档，请返回主菜单。")})
    return candidate


def recover_loaded_failure(state, error):
    return _MANAGER.recover_loaded_failure(state, error) if _MANAGER is not None else incompatible_state(state, error)


def runtime_source_files():
    root = Path(__file__).parent
    files = {"__init__.py": '"""Generated, frozen HTML minigame runtime package."""\n'}
    for name in ("errors.py", "minigame_protocol.py", "minigame_packages.py", "minigame_runtime.py", "minigame_windows.py"):
        files[name] = (root / name).read_text(encoding="utf-8")
    return {"python-packages/renpy_bridge_minigames/" + name: source for name, source in files.items()}


RPY_RUNTIME_SOURCE = r'''# Generated HTML minigame bridge. Store contains JSON only.
default rb_minigame_state = None
default rb_minigame_save_error = None

init 10 python:
    import sys as _rb_mg_sys
    renpy.add_python_directory("python-packages")
    _rb_mg_previous_bytecode = _rb_mg_sys.dont_write_bytecode
    _rb_mg_sys.dont_write_bytecode = True
    try:
        from renpy_bridge_minigames import minigame_runtime as _rb_mg
    finally:
        _rb_mg_sys.dont_write_bytecode = _rb_mg_previous_bytecode

    def rb_minigame_configure():
        return _rb_mg.configure(renpy.config.basedir, renpy.config.savedir, allow_qa=bool(renpy.config.developer),
            parent_window_provider=lambda: _rb_mg.minigame_windows.read_parent(renpy.get_sdl_dll(), renpy.get_sdl_window_pointer(), renpy.display.draw),
            settings_provider=rb_minigame_audio_settings)

    def rb_minigame_audio_settings():
        if _preferences.get_mute("main") or _preferences.get_mute("sfx"):
            return {"audio_volume": 0.0}
        return {"audio_volume": max(0.0, min(1.0, float(_preferences.get_volume("main")) * float(_preferences.get_volume("sfx"))))}

    def rb_minigame_after_load():
        if rb_minigame_state is not None:
            try:
                renpy.store.rb_minigame_state = rb_minigame_configure().after_load(rb_minigame_state)
            except Exception as error:
                renpy.store.rb_minigame_state = _rb_mg.recover_loaded_failure(rb_minigame_state, error)
            if _rb_mg._MANAGER is None and renpy.store.rb_minigame_state.get("phase") == "incompatible":
                renpy.jump("rb_minigame_incompatible_phase")
        elif _rb_mg._MANAGER is not None:
            _rb_mg.manager().after_load(None)

    def rb_minigame_poll():
        status = _rb_mg.manager().poll(rb_minigame_state)
        if status != "waiting":
            renpy.end_interaction(status)

    def rb_minigame_cancel():
        _rb_mg.manager().request_cancel()
        renpy.end_interaction("cancel")

    def rb_minigame_open_menu():
        renpy.end_interaction("menu")

    def rb_minigame_audio(state, entering):
        candidate = _rb_mg._copy(state)
        if entering:
            if "audio_pauses" not in candidate:
                candidate["audio_pauses"] = {channel: bool(renpy.music.get_pause(channel=channel)) for channel in renpy.audio.audio.channels if isinstance(channel, str)}
            for channel in candidate["audio_pauses"]:
                renpy.music.set_pause(True, channel=channel)
            if "ui_restore" not in candidate:
                candidate["ui_restore"] = {"quick_menu": bool(getattr(renpy.store, "quick_menu", False)), "afm_enable": bool(_preferences.afm_enable)}
            renpy.store.quick_menu = False
            _preferences.afm_enable = False
            renpy.stop_skipping()
        else:
            for channel, paused in candidate.get("audio_pauses", {}).items():
                renpy.music.set_pause(paused, channel=channel)
            if "ui_restore" in candidate:
                renpy.store.quick_menu = candidate["ui_restore"]["quick_menu"]
                _preferences.afm_enable = candidate["ui_restore"]["afm_enable"]
        return candidate

    def rb_minigame_persist():
        try:
            if not renpy.config.save:
                raise RuntimeError("当前游戏未启用保存")
            receipt = rb_minigame_state["last_commit"]
            slot = "rb-minigame-" + _rb_mg.hashlib.sha256(_rb_mg.manager().configuration["save_namespace"].encode("utf-8")).hexdigest()[:16]
            renpy.save(slot, extra_info="小游戏进度", include_screenshot=False, extra_json={"rb_minigame_receipt": receipt})
            metadata = renpy.slot_json(slot)
            if metadata is None or metadata.get("rb_minigame_receipt") != receipt:
                raise RuntimeError("保存记录未确认")
            renpy.store.rb_minigame_save_error = None
            return True
        except Exception as error:
            renpy.store.rb_minigame_save_error = str(error)[:512]
            return False

    config.after_load_callbacks.append(rb_minigame_after_load)
    config.quit_callbacks.append(_rb_mg.shutdown)

screen rb_minigame_waiting():
    modal True
    zorder 100
    add Solid("#111827")
    timer 0.05 repeat True action Function(rb_minigame_poll)
    frame:
        xfill True
        yalign 1.0
        ysize int(config.screen_height * 0.1)
        background Solid("#111827")
        hbox:
            align (0.5, 0.5)
            spacing 48
            text "小游戏进行中" size 28 yalign 0.5
            textbutton "暂停与存档" action Function(rb_minigame_open_menu) sensitive _rb_mg.manager().can_pause(rb_minigame_state) yalign 0.5
            textbutton "返回剧情" action Function(rb_minigame_cancel) yalign 0.5
    key "game_menu" action NullAction()
    key "rollback" action NullAction()
    key "skip" action NullAction()
    key "toggle_skip" action NullAction()
    key "fast_skip" action NullAction()
    key "rollforward" action NullAction()

screen rb_minigame_menu():
    modal True
    zorder 105
    add Solid("#111827")
    vbox:
        align (0.5, 0.5)
        spacing 24
        text "小游戏已暂停" size 36 xalign 0.5
        textbutton "继续小游戏" action Return("continue") xalign 0.5
        textbutton "存档" action ShowMenu("save") xalign 0.5
        textbutton "读档" action ShowMenu("load") xalign 0.5
        textbutton "返回剧情" action Return("cancel") xalign 0.5
    key "game_menu" action Return("continue")
    key "rollback" action NullAction()
    key "skip" action NullAction()
    key "toggle_skip" action NullAction()
    key "fast_skip" action NullAction()
    key "rollforward" action NullAction()

screen rb_minigame_save_retry():
    modal True
    zorder 110
    add Solid("#111827")
    vbox:
        align (0.5, 0.5)
        spacing 24
        text "进度暂时无法保存" size 36 xalign 0.5
        text "请确认保存位置可用，然后重试。" size 24 xalign 0.5
        textbutton "重试保存" action Return(True) xalign 0.5
    key "game_menu" action NullAction()
    key "rollback" action NullAction()
    key "skip" action NullAction()
    key "toggle_skip" action NullAction()
    key "fast_skip" action NullAction()
    key "rollforward" action NullAction()

screen rb_minigame_incompatible():
    modal True
    zorder 120
    add Solid("#111827")
    vbox:
        align (0.5, 0.5)
        spacing 24
        text "这份存档暂时无法继续" size 36 xalign 0.5
        text rb_minigame_state["error"]["message"] size 24 xalign 0.5
        textbutton "返回主菜单" action MainMenu(confirm=False) xalign 0.5
    key "game_menu" action NullAction()
    key "rollback" action NullAction()

label rb_minigame_run(rb_minigame_instance_id):
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    $ rb_minigame_configure()
    $ rb_minigame_state = _rb_mg.manager().prepare(rb_minigame_instance_id, rb_minigame_state, rb_story_variables)
    if rb_minigame_state["phase"] in ("completed", "cancelled", "error"):
        jump rb_minigame_done
    $ rb_minigame_state = rb_minigame_audio(rb_minigame_state, True)
    $ renpy.block_rollback()
    jump rb_minigame_entry_save

label rb_minigame_entry_save:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    if rb_minigame_state["phase"] in ("completed", "cancelled", "error"):
        jump rb_minigame_done
    $ rb_minigame_state = _rb_mg.manager().entry_replacement(rb_minigame_state)
    if not rb_minigame_persist():
        call screen rb_minigame_save_retry
        jump rb_minigame_entry_save
    jump rb_minigame_wait

label rb_minigame_wait:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    python:
        try:
            rb_minigame_state = _rb_mg.manager().ensure_started(rb_minigame_state)
        except Exception as error:
            _rb_mg.manager().last_error = {"code": getattr(error, "code", "MINIGAME_START_FAILED"), "message": str(error)[:512]}
            rb_minigame_state = _rb_mg.manager().terminal_replacement(rb_minigame_state, "error")
    if rb_minigame_state["phase"] == "error":
        jump rb_minigame_done
    call screen rb_minigame_waiting
    if _return == "checkpoint":
        jump rb_minigame_checkpoint
    if _return == "finish":
        jump rb_minigame_commit
    if _return == "restart":
        jump rb_minigame_entry_save
    if _return == "menu":
        jump rb_minigame_menu_phase
    if _return == "cancel":
        $ rb_minigame_state = _rb_mg.manager().terminal_replacement(rb_minigame_state, "cancel")
    else:
        $ rb_minigame_state = _rb_mg.manager().terminal_replacement(rb_minigame_state, "error")
    jump rb_minigame_done

label rb_minigame_menu_phase:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    $ rb_minigame_state = _rb_mg.manager().pause_for_menu(rb_minigame_state)
    if rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    if rb_minigame_state["phase"] in ("completed", "cancelled", "error"):
        jump rb_minigame_done
    call screen rb_minigame_menu
    if _return == "cancel":
        $ _rb_mg.manager().request_cancel()
        $ rb_minigame_state = _rb_mg.manager().terminal_replacement(rb_minigame_state, "cancel")
        jump rb_minigame_done
    $ rb_minigame_state = _rb_mg.manager().resume_after_menu(rb_minigame_state)
    if rb_minigame_state["phase"] == "entry":
        jump rb_minigame_entry_save
    jump rb_minigame_wait

label rb_minigame_checkpoint:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    if rb_minigame_state["phase"] in ("completed", "cancelled", "error"):
        jump rb_minigame_done
    $ rb_minigame_state = _rb_mg.manager().checkpoint_replacement(rb_minigame_state)
    $ renpy.block_rollback()
label rb_minigame_checkpoint_save:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    if rb_minigame_state["phase"] in ("cancelled", "error"):
        jump rb_minigame_done
    if not rb_minigame_persist():
        call screen rb_minigame_save_retry
        jump rb_minigame_checkpoint_save
    $ rb_minigame_state = _rb_mg.manager().acknowledge(rb_minigame_state, persisted_receipt=rb_minigame_state["last_commit"])
    jump rb_minigame_wait

label rb_minigame_commit:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    if rb_minigame_state["phase"] in ("cancelled", "error"):
        jump rb_minigame_done
    python:
        try:
            rb_story_variables, rb_minigame_state = _rb_mg.manager().finish_replacement(rb_minigame_state, rb_story_variables)
        except Exception as error:
            _rb_mg.manager().last_error = {"code": getattr(error, "code", "MINIGAME_RESULT_INVALID"), "message": str(error)[:512]}
            rb_minigame_state = _rb_mg.manager().terminal_replacement(rb_minigame_state, "error")
    if rb_minigame_state["phase"] == "error":
        jump rb_minigame_done
    $ rb_minigame_state = rb_minigame_audio(rb_minigame_state, False)
    $ renpy.block_rollback()
label rb_minigame_finish_save:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    if rb_minigame_state["phase"] in ("cancelled", "error"):
        jump rb_minigame_done
    if not rb_minigame_persist():
        call screen rb_minigame_save_retry
        jump rb_minigame_finish_save
    $ rb_minigame_state = _rb_mg.manager().acknowledge(rb_minigame_state, persisted_receipt=rb_minigame_state["last_commit"])
    jump rb_minigame_done

label rb_minigame_done:
    if rb_minigame_state is not None and rb_minigame_state.get("phase") == "incompatible":
        jump rb_minigame_incompatible_phase
    $ _rb_mg.manager().close_active("story_return")
    $ rb_minigame_state = rb_minigame_audio(rb_minigame_state, False)
    $ renpy.block_rollback()
    return _rb_mg.manager().target(rb_minigame_state)

label rb_minigame_incompatible_phase:
    call screen rb_minigame_incompatible
    jump rb_minigame_incompatible_phase
'''
