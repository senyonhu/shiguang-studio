from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import psutil
import pytest

from renpy_bridge.sdk import SdkInfo, run_sdk


def fake_sdk(tmp_path: Path, source: str) -> SdkInfo:
    entry = tmp_path / "fake renpy.py"
    entry.write_text(source, encoding="utf-8")
    return SdkInfo(root=tmp_path, version="test", python_executable=Path(sys.executable), entrypoint=entry)


def test_process_argument_array_preserves_unicode_and_shell_characters(tmp_path):
    sdk = fake_sdk(tmp_path, "import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\n")
    argument = '中文 with spaces; $(echo unsafe) "quoted"'
    result = run_sdk(sdk, "launcher", "generate_gui", [argument], timeout_seconds=10)
    assert result.exit_code == 0
    assert not result.timed_out
    assert json.loads(result.stdout) == ["launcher", "generate_gui", argument]


def test_timeout_stops_owned_descendant_and_keeps_unrelated_process(tmp_path):
    child_pid = tmp_path / "child.pid"
    script = (
        "import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    sdk = fake_sdk(tmp_path, script)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        result = run_sdk(sdk, "launcher", "run", timeout_seconds=1.5)
        assert result.timed_out
        assert result.process_id in result.terminated_process_ids
        assert child_pid.exists(), "fixture must actually create a descendant before timing out"
        pid = int(child_pid.read_text())
        assert pid in result.terminated_process_ids
        if psutil.pid_exists(pid):
            assert psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        assert unrelated.poll() is None
    finally:
        unrelated.kill()
        unrelated.wait(timeout=10)


def test_rejects_a_shell_command_string_for_arguments(tmp_path):
    sdk = fake_sdk(tmp_path, "raise SystemExit(0)\n")
    with pytest.raises(TypeError, match="sequence"):
        run_sdk(sdk, "launcher", "compile", "--option value")
