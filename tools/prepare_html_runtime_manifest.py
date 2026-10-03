"""Create a runtime-only installation manifest from verified local build outputs.

No game project, game result, managed installation, or Codex configuration is
modified. Output always uses a new workspace outputs/html-runtime/<run> folder.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


runtime = load_module("runtime_manifest_installer", ROOT / "tools/install_html_runtime.py")
browser = load_module("runtime_manifest_fixed_browser", ROOT / "tools/prepare_html_browser.py")


def native_inventory(root):
    native = browser.safe_child(root, Path(root) / runtime.NATIVE)
    runtime.installer.unlinked_tree(native)
    if not native.is_dir():
        raise runtime.InstallError("Native same-window host source is missing")
    files = {}
    for base, dirs, names in os.walk(native, followlinks=False):
        dirs[:] = [name for name in dirs if name not in {"bin", "obj"}]
        for name in names:
            path = Path(base) / name
            if path.suffix not in {".cs", ".csproj"} and path.name not in {"app.manifest", "packages.lock.json"}:
                continue
            if browser.linked(path) or not path.is_file():
                raise runtime.InstallError("Native source must use regular unlinked files")
            files[path.relative_to(native).as_posix()] = {"sha256": browser.sha(path), "bytes": path.stat().st_size}
    return dict(sorted(files.items()))


def verified_host(root):
    host_root = browser.safe_child(root, Path(root) / "runtimes" / runtime.HOST)
    files = browser.inventory(host_root)
    required_outputs = {runtime.EXE, "HtmlMinigameEmbeddedHost.dll", "build-report.json"}
    if not required_outputs <= files.keys():
        raise runtime.InstallError("Default native host/build report is missing; build the self-contained host first")
    build = runtime.installer.read_json(host_root / "build-report.json")
    if type(build) is not dict or type(build.get("build")) is not dict:
        raise runtime.InstallError("Malformed actual host build report")
    outputs = build["build"]
    records = outputs.get("files")
    if outputs.get("exit_code") != 0 or outputs.get("self_contained") is not True or type(records) is not list:
        raise runtime.InstallError("Host requires a successful self-contained actual build")
    built = {}
    for record in records:
        if (type(record) is not dict or set(record) != {"path", "bytes", "sha256"}
                or type(record["path"]) is not str or record["path"] in built):
            raise runtime.InstallError("Invalid or duplicate actual host build output")
        runtime.relative(record["path"])
        built[record["path"]] = {key: record[key] for key in ("sha256", "bytes")}
    if "build-report.json" in built or built != {name: value for name, value in files.items() if name != "build-report.json"}:
        raise runtime.InstallError("Host complete output inventory differs from actual build")
    native_files = native_inventory(root)
    required = {"Program.cs", "NativeParent.cs", "HtmlMinigameEmbeddedHost.csproj", "app.manifest", "packages.lock.json"}
    native = build.get("native_source")
    if (type(native) is not dict or native.get("root") != runtime.NATIVE or not required <= native_files.keys()
            or native.get("files") != native_files or native.get("tree_sha256") != runtime.canonical_digest(native_files)):
        raise runtime.InstallError("Host actual build is not bound to current delivered native source")
    return files, native_files


def create_manifest(root=ROOT):
    browser.require_windows()
    root = Path(root).absolute()
    files, native_files = verified_host(root)
    pin, metadata_sha = browser.load_pin(root)
    proof = browser.verify_existing(root, pin, metadata_sha)
    if (proof.get("fresh_signature_verified") is not True or proof.get("historical_signature_claim_used_as_proof") is not False
            or proof.get("evergreen_used") is not False or proof.get("files") != pin["files"]):
        raise runtime.InstallError("Fixed browser requires a fresh complete pinned-tree/signature verification")
    browser_lock = browser.safe_child(root, root / "runtimes" / browser.WRAPPER_NAME / "runtime-lock.json")
    browser_lock_sha = browser.sha(browser_lock)
    configuration = {
        "host": {"window_mode": "embedded_same_top_level_window", "relative_executable": "html-runtime/win-x64/" + runtime.EXE,
                 "sha256": files[runtime.EXE]["sha256"], "files": files},
        "fixed_runtime": {"version": pin["version"], "files": proof["files"]},
    }
    # Verify again before creating a new output; a concurrent build must fail
    # closed instead of producing an installation manifest for mixed revisions.
    current_files, current_native = verified_host(root)
    current_pin, current_metadata_sha = browser.load_pin(root)
    current_browser = browser.inventory(root / pin["relative_runtime_path"])
    if (current_files != files or current_native != native_files or current_pin != pin or current_metadata_sha != metadata_sha
            or current_browser != proof["files"] or browser.sha(browser_lock) != browser_lock_sha):
        raise runtime.InstallError("Runtime/source changed while preparing the installation manifest")
    output_root = browser.safe_child(root, root / "outputs/html-runtime")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    output = browser.safe_child(root, output_root / run_id)
    output_root.mkdir(parents=True, exist_ok=True)
    output.mkdir(exist_ok=False)
    configuration_path = browser.safe_child(root, output / "bridge-minigames.json")
    browser.write_json(configuration_path, configuration)
    report = {"status": "prepared", "scope": "runtime_only_installation_configuration", "configuration": str(configuration_path),
              "configuration_sha256": browser.sha(configuration_path), "host_files": len(files), "browser_files": len(proof["files"]),
              "native_source_tree_sha256": runtime.canonical_digest(native_files), "metadata_sha256": metadata_sha,
              "browser_runtime_lock_sha256": browser_lock_sha, "browser_runtime_tree_sha256": browser.canonical_digest(proof["files"]),
              "fresh_signature_verified": True, "authenticode": proof["authenticode"],
              "authenticode_checked_at_utc": proof["authenticode_checked_at_utc"],
              "runtime_acceptance_verified": False, "gameplay_verified": False, "sdk_game_launched": False,
              "managed_installation_modified": False, "plugin_registration_modified": False, "private_game_project_required": False}
    browser.write_json(output / "preparation-report.json", report)
    return report


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        report = create_manifest(ROOT)
    except (runtime.InstallError, browser.BrowserPreparationError, OSError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc), "managed_installation_modified": False}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
