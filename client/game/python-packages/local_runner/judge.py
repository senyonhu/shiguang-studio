"""Versioned, bounded C -> WASI -> Wasmtime pipeline (Python stdlib only)."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import uuid

from .process import bounded_process

TASK_DIR = Path(__file__).resolve().parents[2] / "tasks"
REVISION = "tower-c11-1.1"


def load_task(task_id):
    if task_id not in ("tower_light_01", "tower_light_02", "tower_light_03"):
        raise ValueError("未知任务")
    return json.loads((TASK_DIR / (task_id + ".json")).read_text(encoding="utf-8"))


def validate_fragment(task_id, fragment):
    if not isinstance(fragment, str) or len(fragment.encode("utf-8")) > 4096:
        raise ValueError("片段必须在 4 KiB 内")
    if task_id == "tower_light_01":
        if not re.fullmatch(r"[0-9]{1,12}", fragment):
            raise ValueError("本关只编辑引号内的数字，允许 1—12 位十进制数字")
        return
    # A small, explicit teaching subset, not an arbitrary C compiler sandbox.
    # Lex before accepting: '#' digraphs/trigraphs, splices and unknown bytes
    # cannot hide inside comments or string literals to add a directive.
    if "\\\n" in fragment or "\\\r" in fragment or "??" in fragment or "%:" in fragment:
        raise ValueError("片段不支持续行、三字符组或预处理等价记号")
    token = re.compile(r'\s+|//[^\n]*|/\*[\s\S]*?\*/|"(?:[^"\\\n]|\\[nrt\\"0])*"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|<=|>=|==|!=|\+\+|--|&&|\|\||[+*/%<>=!&|^~?:;,(){}\[\]-]')
    allowed = {"i", "n"} if task_id == "tower_light_02" else {
        "i", "n", "int", "for", "while", "if", "else", "break", "continue", "printf"}
    pos = 0
    while pos < len(fragment):
        match = token.match(fragment, pos)
        if not match:
            raise ValueError("片段包含不支持的字符或未闭合的字符串 / 注释")
        value = match.group()
        pos = match.end()
        if value.isspace() or value.startswith(("//", "/*")):
            continue
        if re.match(r"[A-Za-z_]", value) and value not in allowed:
            raise ValueError("本 Demo 片段暂不支持标识符：" + value)
        if task_id == "tower_light_02" and (value.startswith('"') or value in (";", "{", "}", "[", "]", "=", "++", "--", ",")):
            raise ValueError("本关只编辑使用 i 与 n 的条件表达式")


def build_source(task_id, fragment):
    validate_fragment(task_id, fragment)
    if task_id == "tower_light_01":
        source = '#include <stdio.h>\n\nint main(void) {\n    printf("' + fragment + '\\n");\n    return 0;\n}\n'
    else:
        block = ('for (int i = 1; ' + fragment + '; i++) {\n    printf("%d\\n", i);\n}') if task_id == "tower_light_02" else fragment
        source = '#include <stdio.h>\n\nint main(void) {\n    int n;\n    scanf("%d", &n);\n\n' + '\n'.join('    ' + line for line in block.splitlines()) + '\n\n    return 0;\n}\n'
    if len(source.encode("utf-8")) > 16384:
        raise ValueError("完整源码超过 16 KiB")
    return source


def source_hash(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def compare_output(actual, n):
    actual = actual.replace("\r\n", "\n")
    expected = "".join(str(i) + "\n" for i in range(1, n + 1))
    passed = actual == expected or (n > 0 and actual == expected[:-1])
    if passed:
        message = "需要零条指令，实际零条指令" if n == 0 else "期望 %d 条指令，实际 %d 条；编号与顺序一致" % (n, n)
        return {"passed": True, "kind": "passed", "message": message, "expected": expected}
    lines = actual[:-1].split("\n") if actual.endswith("\n") else actual.split("\n")
    if actual == "":
        lines = []
    if actual in ("0", "0\n") and n == 1:
        kind, message = "range", "收到了编号 0；当前灯的编号是 1"
    elif any(not re.fullmatch(r"[1-9][0-9]*", line) for line in lines):
        kind, message = "format", "输出格式不符：每行一个编号，不加空格、前导零、提示语或空行"
    else:
        # Avoid converting arbitrarily long decimal output to an integer.
        nums = [int(line) if len(line) < 10 else 9999999999 for line in lines]
        missing = [str(i) for i in range(1, n + 1) if i not in nums]
        if len(nums) != len(set(nums)):
            kind, message = "duplicate", "出现重复编号；每盏灯只需要一条指令"
        elif any(i < 1 or i > n for i in nums):
            kind, message = "range", "收到不属于当前回路的编号"
        elif missing:
            kind, message = "missing", "期望 %d 条指令，实际 %d 条；缺少 %s 号" % (n, len(nums), "、".join(missing))
        else:
            kind, message = "order", "编号顺序不符，需要从 1 按顺序输出到 n"
    return {"passed": False, "kind": kind, "message": message, "expected": expected}


class Runner:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.clang = self.root / "toolchain" / "bin" / "clang.exe"
        self.sysroot = self.root / "toolchain" / "share" / "wasi-sysroot"
        self.wasmtime = self.root / "runtime" / "wasmtime.exe"
        self.lock = threading.RLock()
        self.entries = {}
        self.requests = {}
        self.active = None
        atexit.register(self.cancel_all)

    def check_runtime(self):
        missing = [str(p) for p in (self.clang, self.sysroot, self.wasmtime) if not p.exists()]
        try:
            with tempfile.TemporaryDirectory(prefix="tower-check-") as folder:
                Path(folder, "probe").write_text("ok")
            for tid in ("tower_light_01", "tower_light_02", "tower_light_03"):
                load_task(tid)
        except (OSError, ValueError) as exc:
            missing.append(str(exc))
        return {"available": not missing, "message": "本地组件已就绪 / C11 / WASI" if not missing else "本地运行组件缺失或无法启动", "details": "\n".join(missing)}

    def start_submission(self, request):
        request = json.loads(json.dumps(request))
        tid = request["task_id"]
        task = load_task(tid)
        if request.get("task_revision") != task["task_revision"]:
            raise ValueError("任务版本不匹配")
        source = build_source(tid, request["editable_fragment"])
        if source_hash(source) != request.get("source_hash"):
            raise ValueError("源码校验不匹配")
        if request.get("mode") not in ("trial", "verify"):
            raise ValueError("运行模式无效")
        trial = request.get("trial_input", 3)
        if type(trial) is not int or trial not in task["allowed_trial_inputs"]:
            raise ValueError("试运行输入超出当前任务范围")
        signature = source_hash(json.dumps(request, sort_keys=True))
        key = (request["run_id"], request["client_request_id"])
        with self.lock:
            if key in self.requests:
                old_sig, sid = self.requests[key]
                if old_sig != signature:
                    raise ValueError("请求 ID 冲突")
                return sid
            if self.active is not None:
                raise ValueError("上一项运行尚未结束")
            sid = uuid.uuid4().hex
            cancel = threading.Event()
            entry = {"status": "running", "phase": "正在编译 C11 源码", "cancel": cancel, "result": None}
            self.entries[sid] = entry
            self.requests[key] = (signature, sid)
            self.active = sid
            worker = threading.Thread(target=self._worker, args=(sid, request, source, cancel), daemon=True)
            entry["thread"] = worker
            worker.start()
            return sid

    def poll_submission(self, sid):
        with self.lock:
            item = self.entries.get(sid)
            if not item:
                return {"status": "missing"}
            return {"status": item["status"], "phase": item["phase"], "result": json.loads(json.dumps(item["result"])) if item["result"] else None}

    def cancel_submission(self, sid):
        with self.lock:
            entry = self.entries.get(sid)
            if entry:
                entry["cancel"].set()

    def cancel_all(self):
        with self.lock:
            items = list(self.entries.values())
            for entry in items:
                if entry["status"] == "running":
                    entry["cancel"].set()
        for entry in items:
            worker = entry.get("thread")
            if worker and worker is not threading.current_thread() and worker.is_alive():
                worker.join(timeout=2)

    def _worker(self, sid, request, source, cancel):
        result = {k: request[k] for k in ("task_id", "task_revision", "source_hash", "mode", "run_id")}
        result.update(submission_id=sid, passed=False, cases=[], compile_output="", status="unavailable", error_kind=None)
        try:
            if not self.check_runtime()["available"]:
                raise OSError(self.check_runtime()["details"])
            with tempfile.TemporaryDirectory(prefix="tower-c11-") as folder:
                path = Path(folder)
                (path / "main.c").write_text(source, encoding="utf-8")
                compiled = bounded_process([str(self.clang), "--target=wasm32-wasip1", "--sysroot=" + str(self.sysroot), "-std=c11", "-O0", "-fno-color-diagnostics", "-Wl,--max-memory=67108864", str(path / "main.c"), "-o", str(path / "program.wasm")], b"", 15, 65536, cancel, folder)
                result["compile_output"] = compiled["stderr"] + compiled["stdout"]
                if compiled["status"] != "ok":
                    result["status"] = "compile_error" if compiled["status"] == "runtime_error" else compiled["status"]
                    result["error_kind"] = "compile_" + compiled["status"]
                else:
                    tid = request["task_id"]
                    inputs = [1] if tid.endswith("01") else [3] if tid.endswith("02") else [0, 1, 3, 6] if request["mode"] == "verify" else [request["trial_input"]]
                    for index, n in enumerate(inputs):
                        with self.lock:
                            self.entries[sid]["phase"] = "正在验证回路 %d / %d / n=%d" % (index + 1, len(inputs), n)
                        case = bounded_process([str(self.wasmtime), "run", "-W", "fuel=2000000", "-W", "max-memory-size=67108864", "-W", "max-memories=1", str(path / "program.wasm")], b"" if tid.endswith("01") else (str(n) + "\n").encode(), 3, 16384, cancel, folder)
                        case.update(input=n, comparison=compare_output(case["stdout"], n))
                        case["passed"] = case["status"] == "ok" and case["comparison"]["passed"]
                        result["cases"].append(case)
                        if case["status"] == "cancelled":
                            break
                    result["passed"] = len(result["cases"]) == len(inputs) and all(c["passed"] for c in result["cases"])
                    result["status"] = "passed" if result["passed"] else next((c["status"] for c in result["cases"] if c["status"] != "ok"), "wrong_answer")
                    result["error_kind"] = None if result["passed"] else result["status"]
        except Exception as exc:
            result.update(status="unavailable", error_kind="local_component", compile_output=str(exc))
        finally:
            with self.lock:
                self.entries[sid].update(status="finished", phase="已结束", result=result)
                if self.active == sid:
                    self.active = None
                # Retain a bounded idempotence cache; never evict the active task.
                if len(self.entries) > 128:
                    oldest = next(iter(self.entries))
                    self.entries.pop(oldest, None)
                    self.requests = {k: v for k, v in self.requests.items() if v[1] != oldest}
