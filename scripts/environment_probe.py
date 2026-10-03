"""Record only non-sensitive runtime information."""
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "evidence/m0-environment"
out.mkdir(parents=True, exist_ok=True)
report = {"run_id": "m0-environment", "started_at": datetime.now().astimezone().isoformat(),
          "python": sys.version, "python_path": sys.executable, "platform": platform.platform(),
          "root": str(ROOT), "commands": {}}
for name, argv in {
    "git": ["git", "--version"], "node": ["node", "--version"],
    "codex": ["powershell.exe", "-NoProfile", "-Command", "codex --version"],
    "upstream": ["git", "-C", str(ROOT / "upstream/renpy"), "rev-parse", "HEAD"],
}.items():
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    report["commands"][name] = {"argv": argv, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
report["tool_paths"] = {name: shutil.which(name) for name in ["git", "node", "python", "uv"]}
report["status"] = "passed" if all(r["returncode"] == 0 for r in report["commands"].values()) else "failed"
(out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"status": report["status"], "report": str(out / "report.json")}, ensure_ascii=False))
