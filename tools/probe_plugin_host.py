"""Read-only Codex app-server inventory probe for our installed plugin.

Starts a separate bounded local app-server process. Never changes global settings,
reads credentials, or sends a model turn. Only saves our plugin/server inventory.
"""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evidence/m0-codex/host-investigation"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    shim = Path(shutil.which("codex.cmd") or shutil.which("codex.ps1"))
    executable = next((shim.parent / "node_modules/@openai/codex").rglob("codex.exe"))
    argv = [str(executable), "app-server", "--stdio", "--enable", "plugins"]
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               encoding="utf-8", errors="replace", bufsize=1,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    messages = queue.Queue()
    errors = []
    def read_stdout():
        for line in process.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
    def read_stderr():
        for line in process.stderr:
            errors.append(line.rstrip())
    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=read_stderr, daemon=True).start()
    sequence = 0
    def request(method, params, timeout=60):
        nonlocal sequence
        sequence += 1
        process.stdin.write(json.dumps({"id": sequence, "method": method, "params": params}, ensure_ascii=False) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = messages.get(timeout=max(.1, deadline - time.monotonic()))
            if response.get("id") == sequence:
                return response
        raise TimeoutError(method)
    result = {"scope": "actual_codex_cli_app_server_inventory", "desktop_verified": False, "argv": argv}
    try:
        result["initialize"] = request("initialize", {"clientInfo": {"name": "renpy_bridge_inventory_probe", "version": "0.1.0"}, "capabilities": {"experimentalApi": True}})
        process.stdin.write('{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        models = request("model/list", {"includeHidden": True})
        result["model_list"] = [{k: item.get(k) for k in ("id", "model", "displayName", "isDefault")}
                                for item in models.get("result", {}).get("data", [])]
        result["plugin_read"] = request("plugin/read", {"pluginName": "renpy-bridge", "marketplacePath": str(ROOT / "plugin/.agents/plugins/marketplace.json")})
        installed = request("plugin/installed", {"cwds": [str(ROOT)]})
        value = installed.get("result", {})
        result["plugin_installed_schema_keys"] = list(value)
        result["plugin_installed"] = {k: [x for x in v if "renpy" in json.dumps(x)] if isinstance(v, list) else v for k, v in value.items()}
        start = request("thread/start", {"cwd": str(ROOT), "ephemeral": True, "sandbox": "read-only", "approvalPolicy": "never"})
        if "error" in start:
            result["thread_start_error"] = start["error"]
        else:
            value = start.get("result", {})
            thread = value.get("thread", {})
            result["thread"] = {"id": thread.get("id"), "model": value.get("model"), "modelProvider": value.get("modelProvider")}
            status = request("mcpServerStatus/list", {"threadId": thread.get("id"), "limit": 100})
            value = status.get("result", {})
            result["mcp_inventory_schema_keys"] = list(value)
            result["mcp_inventory"] = {k: [x for x in v if "renpy" in json.dumps(x)] if isinstance(v, list) else v for k, v in value.items()}
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
        result["exit_code"] = process.returncode
        result["relevant_stderr"] = [line for line in errors if "renpy" in line.lower() or "plugin" in line.lower()]
        (OUTPUT / "inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    own = result.get("mcp_inventory", {}).get("data", [])
    print(json.dumps({"report": str(OUTPUT / "inventory.json"), "model_list": result.get("model_list"),
                      "servers": [{"name": item.get("name"), "tools": list(item.get("tools", {}))} for item in own],
                      "error": result.get("error")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
