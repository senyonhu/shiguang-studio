"""Actual Codex CLI MCP invocation, separately labelled from Desktop discovery."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["plugin", "direct"], default="direct")
    args = parser.parse_args()
    shim = Path(shutil.which("codex.cmd") or shutil.which("codex.ps1"))
    matches = list((shim.parent / "node_modules/@openai/codex").rglob("codex.exe"))
    if not matches:
        raise RuntimeError("Codex executable missing")
    output = ROOT / "evidence/m0-codex" / args.mode
    output.mkdir(parents=True, exist_ok=True)
    command = [str(matches[0]), "exec", "--ignore-user-config", "--ephemeral", "--json", "-s", "read-only", "-C", str(ROOT)]
    if args.mode == "direct":
        settings = {
            "mcp_servers.renpy_bridge_probe.command": str(ROOT / ".venv/Scripts/python.exe"),
            "mcp_servers.renpy_bridge_probe.args": ["-m", "renpy_bridge.server"],
            "mcp_servers.renpy_bridge_probe.env.RENPY_BRIDGE_HOME": str(ROOT),
            "mcp_servers.renpy_bridge_probe.env.PYTHONUTF8": "1",
        }
        server = "renpy_bridge_probe"
    else:
        settings = {'features.plugins': True, 'plugins."renpy-bridge@renpy-bridge-local".enabled': True,
                    'marketplaces."renpy-bridge-local".source_type': 'local',
                    'marketplaces."renpy-bridge-local".source': str(ROOT / 'plugin')}
        server = "renpy_bridge"
    for key, value in settings.items():
        command.extend(["-c", key + "=" + json.dumps(value, ensure_ascii=False)])
    command += ["-o", str(output / "last-message.txt"),
                f"本任务是授权的本地工具接入测试。仅调用MCP服务{server}中的get_environment工具一次，include_runtime=false。"
                "不要调用其他相似名字工具；不要读取文件或用shell替代。若精确工具不存在，明确报告不可用，不模拟结果。报告实际plugin/version/home。"]
    started = time.monotonic()
    timed_out = False
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="replace",
                                timeout=180, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
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
    calls = [event["item"] for event in events if event.get("type") == "item.completed"
             and event.get("item", {}).get("type") == "mcp_tool_call"]
    matching = [call for call in calls if server in call.get("server", "") and call.get("tool") == "get_environment"]
    passed = code == 0 and any(call.get("status") == "completed" and not call.get("error") for call in matching)
    report = {"scope": "actual_codex_cli", "desktop_verified": False, "mode": args.mode,
              "argv": command, "exit_code": code, "timed_out": timed_out, "duration_seconds": time.monotonic() - started,
              "status": "passed" if passed else "blocked", "matching_calls": matching, "other_calls": calls}
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output / "report.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
