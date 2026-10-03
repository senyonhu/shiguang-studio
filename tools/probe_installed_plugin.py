"""One real model tool call through the installed plugin, not direct MCP config."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    inventory = json.loads((ROOT / "evidence/m0-codex/host-investigation/inventory.json").read_text(encoding="utf-8"))
    default = next(item["model"] for item in inventory["model_list"] if item["isDefault"])
    shim = Path(shutil.which("codex.cmd") or shutil.which("codex.ps1"))
    executable = next((shim.parent / "node_modules/@openai/codex").rglob("codex.exe"))
    output = ROOT / "evidence/m0-codex/installed-plugin"
    output.mkdir(parents=True, exist_ok=True)
    argv = [str(executable), "exec", "--ephemeral", "--json", "-s", "read-only", "-C", str(ROOT),
            "-m", default, "--enable", "plugins", "-o", str(output / "last-message.txt"),
            "本任务是已授权的本地 RenPy Bridge 插件接入测试。"
            "仅调用已安装插件 RenPy Bridge 提供的 MCP 服务 renpy_bridge 中的 get_environment 工具一次，include_runtime=false。"
            "不要调用其他相似工具，不要读文件或调用 shell，不要修改设置。工具精确不存在时报告不可用，不得模拟。"
            "返回实际工具结果中的 plugin/version/home。"]
    started = time.monotonic()
    timed_out = False
    try:
        process = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace", timeout=180,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        stdout, stderr, code = process.stdout, process.stderr, process.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        code = None
    (output / "raw-events.jsonl").write_text(stdout, encoding="utf-8")
    (output / "stderr.txt").write_text(stderr, encoding="utf-8")
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    calls = [event["item"] for event in events if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "mcp_tool_call"]
    matching = [item for item in calls if item.get("server") == "renpy_bridge" and item.get("tool") == "get_environment"]
    passed = code == 0 and len(matching) == 1 and matching[0].get("status") == "completed" and not matching[0].get("error")
    report = {"scope": "actual_codex_cli_installed_plugin", "desktop_verified": False, "config_changed": False,
              "model_override_source": "live model/list isDefault", "mode": "installed_plugin", "argv": argv,
              "exit_code": code, "timed_out": timed_out, "duration_seconds": time.monotonic() - started,
              "status": "passed" if passed else "blocked", "matching_calls": matching, "other_calls": [item for item in calls if item not in matching]}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json"), "model": default}, ensure_ascii=False))


if __name__ == "__main__":
    main()
