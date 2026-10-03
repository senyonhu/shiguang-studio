"""Download the pinned official Ren'Py SDK and execute an isolated M0 probe.

Run with the workspace's Python, not Ren'Py's Python. Downloads are verified
against the official SHA256 before extraction; no upstream files are touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

VERSION = "8.5.3"
SHA256 = "ff57648f9c04f27e381c48af6d8e3ee3cdec296bed4d3831f47f09b0a71b505e"
BASE_URL = f"https://www.renpy.org/dl/{VERSION}/"
ARCHIVE_NAME = f"renpy-{VERSION}-sdk.zip"
WORKSPACE = Path(__file__).resolve().parents[1]
EVIDENCE = WORKSPACE / "evidence" / "m0-sdk"
SDK_ROOT = WORKSPACE / "runtimes" / f"renpy-{VERSION}"
REPORT_PATH = EVIDENCE / "report.json"
LOG_DIR = EVIDENCE


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def save_report(report: dict) -> None:
    write_json(REPORT_PATH, report)
    if LOG_DIR != EVIDENCE:
        write_json(LOG_DIR / "report.json", report)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, path: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "renpy-bridge-m0-probe/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, path.open("wb") as output:
        total = int(response.headers.get("Content-Length", 0))
        count = 0
        next_progress = 10 * 1024 * 1024
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            count += len(chunk)
            if count >= next_progress:
                print(f"download {count}/{total} bytes", flush=True)
                next_progress += 10 * 1024 * 1024


def acquire(report: dict) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    archive = WORKSPACE / "runtimes" / ARCHIVE_NAME
    archive.parent.mkdir(parents=True, exist_ok=True)
    checksums = EVIDENCE / "official-checksums.txt"
    download(BASE_URL + "checksums.txt", checksums)
    published = f"{SHA256} {ARCHIVE_NAME}"
    if published not in checksums.read_text(encoding="utf-8"):
        raise RuntimeError("Pinned archive hash does not appear in official checksum file")
    if not archive.exists() or sha256(archive) != SHA256:
        download(BASE_URL + ARCHIVE_NAME, archive)
    actual = sha256(archive)
    if actual != SHA256:
        raise RuntimeError(f"Archive SHA256 mismatch: {actual}")
    report["download"] = {"url": BASE_URL + ARCHIVE_NAME, "sha256": actual,
                          "official_checksum_matched": True, "bytes": archive.stat().st_size,
                          "archive": str(archive), "sdk_root": str(SDK_ROOT)}
    save_report(report)
    if not (SDK_ROOT / "renpy.py").is_file():
        SDK_ROOT.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            entries = zf.infolist()
            root_names = {item.filename.split("/", 1)[0] for item in entries}
            if len(root_names) != 1:
                raise RuntimeError(f"Unexpected archive top-level entries: {root_names}")
            archive_root = next(iter(root_names)) + "/"
            for item in entries:
                relative = item.filename.removeprefix(archive_root)
                if not relative:
                    continue
                destination = (SDK_ROOT / relative).resolve()
                if not destination.is_relative_to(SDK_ROOT.resolve()):
                    raise RuntimeError(f"Unsafe archive member: {item.filename}")
                if item.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(item) as source, destination.open("wb") as target:
                        shutil.copyfileobj(source, target)
        print(f"verified and extracted {SDK_ROOT}", flush=True)
    report["sdk_extracted"] = (SDK_ROOT / "renpy.py").is_file()
    save_report(report)


def execute(report: dict, name: str, argv: list[str], timeout: float = 60,
            env_changes: dict[str, str] | None = None) -> dict:
    import psutil
    env = os.environ.copy()
    env.update({"RENPY_SIMPLE_EXCEPTIONS": "1", "PYTHONIOENCODING": "utf-8"})
    env.update(env_changes or {})
    started = time.monotonic()
    proc = subprocess.Popen(argv, cwd=SDK_ROOT, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    identity = psutil.Process(proc.pid)
    created = identity.create_time()
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        owned = []
        try:
            if identity.create_time() == created:
                owned = identity.children(recursive=True) + [identity]
        except psutil.NoSuchProcess:
            pass
        for child in owned:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        stdout, stderr = proc.communicate(timeout=15)
    result = {"argv": argv, "cwd": str(SDK_ROOT), "exit_code": proc.returncode,
              "duration_seconds": round(time.monotonic() - started, 3), "timed_out": timed_out,
              "process_id": proc.pid, "process_create_time": created,
              "environment_overrides": {"RENPY_SIMPLE_EXCEPTIONS": "1", "PYTHONIOENCODING": "utf-8", **(env_changes or {})}}
    (LOG_DIR / f"{name}.stdout.txt").write_text(stdout, encoding="utf-8")
    (LOG_DIR / f"{name}.stderr.txt").write_text(stderr, encoding="utf-8")
    result.update({"stdout_path": str(LOG_DIR / f"{name}.stdout.txt"),
                   "stderr_path": str(LOG_DIR / f"{name}.stderr.txt")})
    if len(argv) > 2 and Path(argv[2]).is_dir():
        engine_log = Path(argv[2]) / "log.txt"
        if engine_log.is_file():
            copied_log = LOG_DIR / f"{name}.engine-log.txt"
            shutil.copyfile(engine_log, copied_log)
            result["engine_log_path"] = str(copied_log)
    report.setdefault("commands", {})[name] = result
    save_report(report)
    print(f"{name}: exit={proc.returncode} timeout={timed_out} seconds={result['duration_seconds']}", flush=True)
    if stderr:
        print(stderr[-2000:], flush=True)
    return result


def probe(report: dict) -> None:
    global LOG_DIR
    run_id = str(time.time_ns())
    LOG_DIR = EVIDENCE / "runs" / run_id
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report["probe_run_id"] = run_id
    report["probe_evidence_directory"] = str(LOG_DIR)
    report["screenshot_policy"] = "fresh isolated fixture; captures are evidence, not an approved regression baseline"
    report["commands"] = {}
    report.pop("results", None)
    report.pop("probe_error", None)
    py = SDK_ROOT / "lib" / "py3-windows-x86_64" / "python.exe"
    entry = SDK_ROOT / "renpy.py"
    if not py.is_file() or not entry.is_file():
        raise RuntimeError("SDK Windows runtime or entrypoint missing")
    base = [str(py), str(entry)]
    report["environment"] = {"workspace": str(WORKSPACE), "host_python": sys.version,
                             "sdk_python": str(py), "platform": sys.platform,
                             "upstream_unchanged_by_probe": True}
    execute(report, "version", [*base, "--version"])
    execute(report, "launcher-help", [*base, "launcher", "--help"])
    execute(report, "generate-gui-help", [*base, "launcher", "generate_gui", "--help"])
    execute(report, "distribute-help", [*base, "launcher", "distribute", "--help"])
    fixture = LOG_DIR / "中文 最小工程"
    report["fixture_path"] = str(fixture)
    fixture.parent.mkdir(parents=True, exist_ok=True)
    if not fixture.exists():
        result = execute(report, "generate-chinese-project", [*base, "launcher", "generate_gui", str(fixture),
                         "--start", "--width", "1280", "--height", "720", "--language", "schinese",
                         "--template", str(SDK_ROOT / "gui")])
        if result["exit_code"] != 0:
            raise RuntimeError("Native Chinese project generation failed; inspect logs")
    game = fixture / "game"
    if not game.is_dir():
        raise RuntimeError("Generated game directory missing")
    # generate_gui generates scalable images and translated code, but its
    # copy_files only copies language-specific files. The SDK launcher's New
    # Project workflow also copies static template GUI assets. Reproduce that
    # missing step without overwriting images already generated at this size.
    copied_gui = []
    template_gui = SDK_ROOT / "gui" / "game" / "gui"
    for source in template_gui.rglob("*"):
        if source.is_file():
            destination = game / "gui" / source.relative_to(template_gui)
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                copied_gui.append(str(destination.relative_to(game)))
    report["static_gui_assets_copied"] = copied_gui
    # Updating generated defines in place avoids duplicate-definition lint
    # errors. Other probe-only settings may safely live in a separate file.
    options_path = game / "options.rpy"
    options = options_path.read_text(encoding="utf-8-sig")
    for variable, value in [("config.save_directory", '"renpy_bridge_m0_isolated"'),
                             ("config.window", '"show"')]:
        options, count = re.subn(rf"(?m)^define {re.escape(variable)}\s*=.*$",
                                 lambda match: f"define {variable} = {value}", options)
        if count != 1:
            raise RuntimeError(f"Expected one template define for {variable}, found {count}")
    options_path.write_text(options, encoding="utf-8")
    # This fixture is owned by the probe. No user/game content is overwritten.
    (game / "script.rpy").write_text('''# Generated M0 smoke fixture, not the final demo.
default m0_reached = False
label start:
    scene expression Solid("#233142")
    "你好，这是中文引擎运行验证。"
    menu:
        "继续验证":
            $ m0_reached = True
            "分支已通过。"
    return
''', encoding="utf-8")
    (game / "testcases.rpy").write_text('''testsuite global:
    teardown:
        exit

testcase m0_smoke:
    $ _test.timeout = 15.0
    $ _preferences.text_cps = 0
    run Start()
    pause 0.6
    assert screen "say"
    screenshot "m0-chinese-dialogue"
    advance until screen "choice"
    assert screen "choice"
    screenshot "m0-chinese-choice"
    click "继续验证"
    assert eval m0_reached
    pause 0.4
    screenshot "m0-chinese-branch"

testcase m0_failure:
    assert eval False
''', encoding="utf-8")
    (game / "m0_options.rpy").write_text('''define config.extra_savedirs = []
''', encoding="utf-8")
    private_savedir = LOG_DIR / "private-saves"
    private_multi = LOG_DIR / "private-multipersistent"
    private_savedir.mkdir(parents=True, exist_ok=True)
    private_multi.mkdir(parents=True, exist_ok=True)
    for inherited in [game / "saves"]:
        if inherited.exists():
            report.setdefault("isolation_warnings", []).append(str(inherited))
    isolated = {"RENPY_MULTIPERSISTENT": str(private_multi)}
    execute(report, "gui-images-help", [*base, str(fixture), "gui_images", "--help"], env_changes=isolated)
    execute(report, "materialize-gui-images", [*base, str(fixture), "gui_images", "--savedir", str(private_savedir)],
            timeout=90, env_changes=isolated)
    for name, command in [("compile-help", "compile"), ("lint-help", "lint"), ("test-help", "test"), ("run-help", "run")]:
        execute(report, name, [*base, str(fixture), command, "--help"], env_changes=isolated)
    execute(report, "compile", [*base, str(fixture), "compile", "--savedir", str(private_savedir)], env_changes=isolated)
    execute(report, "lint", [*base, str(fixture), "lint", "--error-code", "--savedir", str(private_savedir)], env_changes=isolated)
    execute(report, "actual-render-smoke", [*base, str(fixture), "test", "m0_smoke", "--report-detailed",
            "--savedir", str(private_savedir)], timeout=90, env_changes=isolated)
    execute(report, "failure-exit-code", [*base, str(fixture), "test", "m0_failure", "--report-detailed",
            "--savedir", str(private_savedir)], timeout=45, env_changes=isolated)
    font = game / "SourceHanSansLite.ttf"
    screenshots = []
    for path in fixture.rglob("*.png"):
        if "screenshots" in path.parts:
            screenshots.append({"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size})
    commands = report["commands"]
    capture_names = {Path(item["path"]).stem.split("@", 1)[0] for item in screenshots}
    expected_captures = {"m0-chinese-dialogue", "m0-chinese-choice", "m0-chinese-branch"}
    report["results"] = {"chinese_font": {"path": str(font), "exists": font.is_file(),
                                            "sha256": sha256(font) if font.is_file() else None},
                         "screenshots": screenshots,
                         "compile_passed": commands["compile"]["exit_code"] == 0,
                         "lint_passed": commands["lint"]["exit_code"] == 0,
                         "real_renderer_smoke_passed": commands["actual-render-smoke"]["exit_code"] == 0
                         and not commands["actual-render-smoke"]["timed_out"] and expected_captures <= capture_names,
                         "negative_test_returned_failure": commands["failure-exit-code"]["exit_code"] == 1
                         and not commands["failure-exit-code"]["timed_out"]
                         and "RenpyTestAssertionError" in ((LOG_DIR / "failure-exit-code.stdout.txt").read_text(encoding="utf-8")
                                  + (LOG_DIR / "failure-exit-code.stderr.txt").read_text(encoding="utf-8")),
                         "actual_audio_output_verified": False,
                         "release_package_verified": False}
    # The renderer, configured driver and save paths are in Ren'Py's real log.
    if (fixture / "log.txt").exists():
        shutil.copyfile(fixture / "log.txt", LOG_DIR / "engine-last-log.txt")
    save_report(report)
    print(json.dumps(report["results"], ensure_ascii=False, indent=2), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["download", "probe", "all"], default="all")
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8")) if REPORT_PATH.exists() else {"sdk_version": VERSION}
    try:
        if args.stage in {"download", "all"}:
            acquire(report)
        if args.stage in {"probe", "all"}:
            probe(report)
    except Exception as error:
        report["probe_error"] = {"type": type(error).__name__, "message": str(error)}
        save_report(report)
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
