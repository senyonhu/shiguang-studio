"""Run an independent Codex author turn using the installed RenPy Bridge plugin.

The prompt is supplied by the caller; this harness neither edits global Codex
configuration nor wires a direct MCP server. Full event evidence is preserved in
a unique run directory. Tool success is reported separately from process exit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import uuid

import psutil

ROOT = Path(__file__).resolve().parents[1]


def codex_executable() -> Path:
    shim = shutil.which("codex.cmd") or shutil.which("codex.ps1")
    if not shim:
        raise RuntimeError("Codex CLI executable is not installed")
    return next((Path(shim).parent / "node_modules/@openai/codex").rglob("codex.exe"))


def live_default_model(executable: Path) -> str:
    """Read the signed-in account's current default without reading auth files."""
    process = subprocess.Popen([str(executable), "app-server", "--stdio"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    messages: queue.Queue = queue.Queue()
    def read():
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
    threading.Thread(target=read, daemon=True).start()
    def request(identifier: int, method: str, params: dict) -> dict:
        process.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            response = messages.get(timeout=max(.1, deadline - time.monotonic()))
            if response.get("id") == identifier:
                if "error" in response:
                    raise RuntimeError(json.dumps(response["error"]))
                return response.get("result", {})
        raise TimeoutError(method)
    try:
        request(1, "initialize", {"clientInfo": {"name": "renpy_bridge_author_harness", "version": "0.1.0"}})
        process.stdin.write('{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        models = request(2, "model/list", {})
        default = next((item.get("model") for item in models.get("data", []) if item.get("isDefault")), None)
        if not default:
            raise RuntimeError("Live model/list did not return a default model")
        return default
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)


def stop_owned_tree(process: subprocess.Popen, created: float) -> list[int]:
    """Only terminate the process whose identity this harness recorded."""
    try:
        parent = psutil.Process(process.pid)
        if abs(parent.create_time() - created) > .01:
            return []
        children = parent.children(recursive=True)
        targets = children[::-1] + [parent]
        terminated = []
        for target in targets:
            try:
                target.terminate()
                terminated.append(target.pid)
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(targets, timeout=5)
        for target in alive:
            try:
                target.kill()
            except psutil.Error:
                pass
        return terminated
    except psutil.Error:
        return []


def run(prompt_file: Path, output_dir: Path, timeout: int) -> dict:
    prompt_file = prompt_file.resolve(strict=True)
    output_dir = output_dir.resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = output_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    prompt = prompt_file.read_text(encoding="utf-8-sig")
    (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
    executable = codex_executable()
    model = live_default_model(executable)
    argv = [str(executable), "exec", "--ephemeral", "--json", "-s", "danger-full-access", "-C", str(ROOT),
            "-c", 'approval_policy="never"', "-m", model, "--enable", "plugins",
            "-o", str(run_dir / "last-message.txt"), "-"]
    started = time.monotonic()
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8", errors="replace",
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    created = psutil.Process(process.pid).create_time()
    timed_out = False
    terminated = []
    print(json.dumps({"event": "author_started", "run_dir": str(run_dir), "model": model}, ensure_ascii=False), flush=True)
    def capture(stream, path, announce=False):
        with path.open("w", encoding="utf-8") as output:
            for line in stream:
                output.write(line)
                output.flush()
                if announce:
                    try:
                        event = json.loads(line)
                        item = event.get("item", {})
                        if event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call":
                            print(json.dumps({"event": "mcp_completed", "server": item.get("server"),
                                              "tool": item.get("tool"), "status": item.get("status")}, ensure_ascii=False), flush=True)
                    except ValueError:
                        pass
    readers = [threading.Thread(target=capture, args=(process.stdout, run_dir / "raw-events.jsonl", True), daemon=True),
               threading.Thread(target=capture, args=(process.stderr, run_dir / "stderr.txt"), daemon=True)]
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(prompt)
        process.stdin.close()
        process.wait(timeout=timeout)
        code = process.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        terminated = stop_owned_tree(process, created)
        process.wait(timeout=15)
        code = process.returncode
    for reader in readers:
        reader.join(timeout=10)
    stdout = (run_dir / "raw-events.jsonl").read_text(encoding="utf-8")
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    completed = [item["item"] for item in events if item.get("type") == "item.completed"]
    calls = [item for item in completed if item.get("type") == "mcp_tool_call"]
    plugin_calls = [item for item in calls if item.get("server") == "renpy_bridge"]
    def failed_tool(item):
        if item.get("status") != "completed" or item.get("error") or item.get("result", {}).get("isError"):
            return True
        for content in item.get("result", {}).get("content", []):
            if content.get("type") == "text":
                try:
                    value = json.loads(content.get("text", ""))
                    if isinstance(value, dict) and value.get("ok") is False:
                        return True
                except ValueError:
                    pass
        return False
    failed_calls = [item for item in plugin_calls if failed_tool(item)]
    report = {"scope": "independent_codex_cli_author", "desktop_verified": False, "run_id": run_id,
              "config_changed": False, "direct_mcp_configured": False, "model": model,
              "model_override_source": "live model/list isDefault", "argv": argv, "prompt_file": str(prompt_file),
              "run_dir": str(run_dir), "exit_code": code, "timed_out": timed_out,
              "duration_seconds": time.monotonic() - started, "terminated_process_ids": terminated,
              "status": "completed" if code == 0 and not timed_out else "failed", "plugin_call_count": len(plugin_calls),
              "plugin_calls": plugin_calls, "failed_plugin_calls": failed_calls,
              "other_mcp_calls": [item for item in calls if item not in plugin_calls],
              "shell_calls": [item for item in completed if item.get("type") in {"command_execution", "file_change"}],
              "errors": [item for item in events if item.get("type") == "error"]}
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "latest.json").write_text(json.dumps({"run_id": run_id, "report": str(report_path)}, indent=2), encoding="utf-8")
    return {"status": report["status"], "run_dir": str(run_dir), "report": str(report_path),
            "plugin_call_count": len(plugin_calls), "failed_plugin_call_count": len(failed_calls)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt-file", type=Path, required=True)
    parser.add_argument("--outputdir", "--output-dir", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    if not 30 <= args.timeout <= 7200:
        parser.error("--timeout must be between 30 and 7200 seconds")
    print(json.dumps(run(args.prompt_file, args.outputdir, args.timeout), ensure_ascii=False))


if __name__ == "__main__":
    main()
