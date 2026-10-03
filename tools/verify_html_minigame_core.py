"""Run all repository tests and bind fresh evidence to before/after source hashes."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, data):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def hashes():
    files = [*(ROOT / "src/renpy_bridge").rglob("*.py"), *(ROOT / "tests").rglob("*.py"),
             *(ROOT / "examples/html_minigames").rglob("*"),
             *(ROOT / "tools").glob("*.py"), *(ROOT / "tools").glob("*.mjs"),
             *(ROOT / "scripts").glob("*.py"), *(ROOT / "metadata").rglob("*.json"),
             *(ROOT / "native/html_minigame_embedded_host").glob("*.cs"),
             *(ROOT / "native/html_minigame_embedded_host").glob("*.csproj"),
             ROOT / "native/html_minigame_embedded_host/packages.lock.json",
             ROOT / "native/html_minigame_embedded_host/app.manifest",
             ROOT / "runtimes/html-minigame-embedded-host/win-x64/build-report.json",
             ROOT / "runtimes/webview2-fixed-154.0.4258.53/runtime-lock.json",
             ROOT / "pyproject.toml", ROOT / "requirements.lock",
             ROOT / "plugin/renpy-bridge/skills/renpy-author/SKILL.md",
             ROOT / "plugin/renpy-bridge/skills/renpy-author/references/html-minigames.md"]
    paths = sorted(set(path for path in files if path.is_file() and not any(
        part in {"__pycache__", "node_modules", "bin", "obj"} for part in path.relative_to(ROOT).parts)),
        key=lambda path: path.relative_to(ROOT).as_posix().encode("utf-8"))
    return {path.relative_to(ROOT).as_posix(): {"sha256": file_hash(path), "bytes": path.stat().st_size} for path in paths}


def main():
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    output = ROOT / "evidence/html-minigames/core-regression/runs" / run_id
    output.mkdir(parents=True, exist_ok=False)
    before = hashes()
    write_json(output / "source-before.json", before)
    command = [sys.executable, "-m", "pytest", "tests", "-q", "--junitxml=" + str(output / "junit.xml")]
    started = datetime.now(timezone.utc).isoformat()
    result = subprocess.run(command, cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1"),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=1800)
    (output / "pytest.log").write_text(result.stdout, encoding="utf-8", newline="\n")
    after = hashes()
    write_json(output / "source-after.json", after)
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    junit = output / "junit.xml"
    counts = {field: 0 for field in ("tests", "failures", "errors", "skipped")}
    if junit.is_file():
        root = ET.parse(junit).getroot()
        for suite in [root] if root.tag == "testsuite" else root.findall("testsuite"):
            for field in counts:
                counts[field] += int(suite.get(field, "0"))
    report = {"run_id": run_id, "evidence_root": str(output), "command": command, "started_at": started,
              "finished_at": datetime.now(timezone.utc).isoformat(), "exit_code": result.returncode,
              "counts": counts, "source_stable": not changed, "source_changed_during_tests": changed,
              "source_before_sha256": file_hash(output / "source-before.json"),
              "source_after_sha256": file_hash(output / "source-after.json"),
              "junit_sha256": file_hash(junit) if junit.is_file() else None,
              "log_sha256": file_hash(output / "pytest.log"),
              "passed_for_fixed_sources": result.returncode == 0 and not changed,
              "scope": "all_repository_pytest_with_explicit_skips", "gameplay_verified": False,
              "window_matrix_verified": False, "release_verified": False}
    write_json(output / "report.json", report)
    write_json(output / "evidence-integrity.json", {"algorithm": "sha256", "files": [
        {"path": file.name, "sha256": file_hash(file), "bytes": file.stat().st_size}
        for file in sorted(output.iterdir()) if file.is_file()]})
    print(result.stdout)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(result.returncode or (1 if changed else 0))


if __name__ == "__main__":
    main()
