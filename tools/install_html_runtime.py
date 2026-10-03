"""Verify and stage the frozen same-window HTML runtime into a managed home.

This helper changes only two named runtime trees and its independent installer
records. It does not register plugins, update the SDK, migrate projects, or open
a game. A frozen generated bridge-minigames.json provides the tested inventory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("_html_runtime_source_installer", ROOT / "scripts/install_local.py")
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)
InstallError = installer.InstallError
HOST = "html-minigame-embedded-host/win-x64"
NATIVE = "native/html_minigame_embedded_host"
EXE = "HtmlMinigameEmbeddedHost.exe"
MAX_FILES, MAX_BYTES = 8192, 2 * 1024**3
RESERVED = installer.WINDOWS_RESERVED


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def relative(value):
    if (type(value) is not str or not value or "\\" in value or ":" in value or "\0" in value
            or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/"))
            or any(part.rstrip(" .") != part or part.split(".", 1)[0].upper() in RESERVED for part in value.split("/"))):
        raise InstallError("Unsafe runtime-relative path")
    return Path(*PurePosixPath(value).parts)


def safe_child(root, value):
    root = installer.unlinked_path(root)
    return installer.bounded(root, root / relative(value))


def inventory(root):
    root = installer.unlinked_path(root)
    installer.unlinked_tree(root)
    if not root.is_dir():
        raise InstallError("Runtime source tree is missing: " + str(root))
    output, identities, total = {}, set(), 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in (*dirs, *names):
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            relative(rel)
            if rel.casefold() in identities:
                raise InstallError("Case-conflicting runtime paths")
            identities.add(rel.casefold())
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)) or installer.linked(path):
                raise InstallError("Runtime tree contains a non-regular entry")
            if path.is_file():
                total += path.stat().st_size
                output[rel] = {"sha256": installer.sha256(path), "bytes": path.stat().st_size}
                if total > MAX_BYTES or len(output) > MAX_FILES:
                    raise InstallError("Runtime inventory exceeds its size/file bound")
    return dict(sorted(output.items()))


def check_inventory(root, expected):
    if type(expected) is not dict or not expected or len(expected) > MAX_FILES:
        raise InstallError("Missing bounded runtime inventory")
    for rel, record in expected.items():
        relative(rel)
        if (type(record) is not dict or set(record) != {"sha256", "bytes"}
                or not re.fullmatch(r"[a-f0-9]{64}", str(record["sha256"]))
                or type(record["bytes"]) is not int or not 0 <= record["bytes"] <= MAX_BYTES):
            raise InstallError("Invalid recorded runtime file")
    actual = inventory(root)
    if actual != expected:
        raise InstallError("Frozen runtime inventory differs: " + str(root))
    return actual


def managed_destination(destination):
    destination = installer.unlinked_path(destination)
    marker = installer.read_json(safe_child(destination, ".installer/installed.json"))
    if (marker.get("schema_version") != "1.0" or type(marker.get("managed_entries")) is not list
            or not marker["managed_entries"] or not set(marker["managed_entries"]) <= installer.MANAGED
            or installer.unlinked_path(Path(marker.get("destination", ""))) != destination):
        raise InstallError("Destination has no matching supported managed installation marker")
    for path in (destination / "jobs").glob("*/status.json"):
        status = installer.read_json(installer.bounded(destination, path))
        if status.get("state") in {"queued", "running"}:
            raise InstallError("A managed job is still active; close it before changing its runtime")
    installer.unlinked_path(destination / "runtimes")
    return destination


def make_plan(source, destination, configuration):
    source = installer.unlinked_path(source)
    destination = managed_destination(destination)
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise InstallError("Source and managed installation must use separate trees")
    configuration = installer.unlinked_path(configuration)
    cfg = installer.read_json(configuration)
    host = cfg.get("host", {})
    if (host.get("window_mode") != "embedded_same_top_level_window"
            or host.get("relative_executable") != "html-runtime/win-x64/" + EXE):
        raise InstallError("Configuration is not the frozen same-window Windows host")
    host_root = safe_child(source, "runtimes/" + HOST)
    host_files = check_inventory(host_root, host.get("files"))
    if host_files.get(EXE, {}).get("sha256") != host.get("sha256") or "build-report.json" not in host_files:
        raise InstallError("Frozen executable/build report identity is missing")
    build = installer.read_json(host_root / "build-report.json")
    outputs = build.get("build", {})
    records = outputs.get("files", [])
    if outputs.get("exit_code") != 0 or outputs.get("self_contained") is not True or type(records) is not list:
        raise InstallError("Host must be a successful self-contained actual build")
    built = {}
    for record in records:
        if type(record) is not dict or set(record) != {"path", "bytes", "sha256"} or record["path"] in built:
            raise InstallError("Invalid or duplicated actual build output")
        relative(record["path"])
        built[record["path"]] = {key: record[key] for key in ("sha256", "bytes")}
    if "build-report.json" in built or built != {name: meta for name, meta in host_files.items() if name != "build-report.json"}:
        raise InstallError("Actual build output differs from the tested frozen inventory")
    native = build.get("native_source", {})
    native_files = {rel: meta for rel, meta in inventory(safe_child(source, NATIVE)).items()
                    if not ({"bin", "obj"} & set(rel.split("/")))
                    and (Path(rel).suffix in {".cs", ".csproj"} or Path(rel).name in {"app.manifest", "packages.lock.json"})}
    required = {"Program.cs", "NativeParent.cs", "HtmlMinigameEmbeddedHost.csproj", "app.manifest", "packages.lock.json"}
    if (native.get("root") != NATIVE or not required <= native_files.keys()
            or native.get("files") != native_files or native.get("tree_sha256") != canonical_digest(native_files)):
        raise InstallError("Actual built native source does not match delivered source")
    fixed = cfg.get("fixed_runtime", {})
    version = fixed.get("version")
    if type(version) is not str or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise InstallError("Fixed browser version is missing")
    browser_name = "webview2-fixed-" + version
    wrapper = safe_child(source, "runtimes/" + browser_name)
    lock = installer.read_json(safe_child(wrapper, "runtime-lock.json"))
    browser_relative = browser_name + "/Microsoft.WebView2.FixedVersionRuntime." + version + ".x64"
    if (lock.get("relative_runtime_path") != "runtimes/" + browser_relative or lock.get("version") != version
            or lock.get("architecture") != "x64" or lock.get("mode") != "fixed"
            or lock.get("signature_status") != "Valid" or "Microsoft Corporation" not in str(lock.get("signer", ""))):
        raise InstallError("Fixed browser provenance lock is missing or incompatible")
    browser = safe_child(source, "runtimes/" + browser_relative)
    check_inventory(browser, fixed.get("files"))
    if fixed["files"].get("msedgewebview2.exe", {}).get("sha256") != lock.get("exe_sha256"):
        raise InstallError("Fixed browser executable differs from its official lock")
    browser_files = inventory(wrapper)
    prefix = browser.name + "/"
    if any(name not in {"runtime-lock.json", "runtime.cab"} and not name.startswith(prefix) for name in browser_files):
        raise InstallError("Unexpected files in the fixed browser version wrapper")
    if "runtime.cab" in browser_files and browser_files["runtime.cab"]["sha256"] != lock.get("cab_sha256"):
        raise InstallError("Optional official fixed browser CAB hash differs")
    trees = [{"relative": HOST, "source": str(host_root), "files": host_files},
             {"relative": browser_name, "source": str(wrapper), "files": browser_files}]
    for tree in trees:
        installer.unlinked_tree(safe_child(destination, "runtimes/" + tree["relative"]))
    return {"schema_version": "1.0", "extension": "same_window_html_minigames", "source": str(source),
            "destination": str(destination), "configuration": str(configuration), "configuration_sha256": installer.sha256(configuration),
            "native_source_tree_sha256": native["tree_sha256"], "fixed_browser_version": version,
            "trees": trees, "runtime_acceptance_verified": False, "plugin_registration_modified": False,
            "preserved": ["runtimes/renpy-8.5.3", "projects", "jobs", "checkpoints", "user saves"]}


def verify_installed(plan):
    destination = managed_destination(Path(plan["destination"]))
    for tree in plan["trees"]:
        check_inventory(safe_child(destination, "runtimes/" + tree["relative"]), tree["files"])
    return True


def _journal_paths(destination, run, tree):
    name = tree["relative"]
    if name != HOST and not re.fullmatch(r"webview2-fixed-\d+\.\d+\.\d+\.\d+", name):
        raise InstallError("Unsupported runtime publication target")
    return (safe_child(destination, "runtimes/" + name), safe_child(run, "stage/" + name),
            safe_child(destination, "backups/html-runtime-" + run.name + "/" + name))


def rollback(destination, run, journal):
    run = installer.bounded(destination, run)
    if not run.is_relative_to(destination / ".installer/html-runtime/runs"):
        raise InstallError("Publication run is outside its owned runtime installer directory")
    if journal.get("schema_version") != "1.0" or journal.get("state") != "publishing" or len(journal.get("trees", [])) != 2:
        raise InstallError("Malformed runtime publication journal")
    paths = [(tree, _journal_paths(destination, run, tree)) for tree in journal["trees"]]
    if (len({tree["relative"] for tree, _ in paths}) != 2 or HOST not in {tree["relative"] for tree, _ in paths}
            or any(type(tree.get("had_previous")) is not bool for tree, _ in paths)):
        raise InstallError("Malformed runtime previous-state flags")
    for tree, triple in paths:
        for path in triple:
            installer.unlinked_tree(path)
    for tree, (target, staged, previous) in reversed(paths):
        if previous.exists() or (not tree["had_previous"] and not staged.exists()):
            if target.exists():
                failed = safe_child(run, "failed-new/" + tree["relative"])
                failed.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, failed)
            if previous.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(previous, target)
    marker = safe_child(destination, ".installer/html-runtime/installed.json")
    old_marker = safe_child(run, "previous-installed.json")
    if old_marker.is_file():
        installer.atomic_json(marker, installer.read_json(old_marker))
    elif marker.exists():
        os.replace(marker, safe_child(run, "failed-new-installed.json"))
    journal["state"] = "rolled_back"
    installer.atomic_json(run / "publication.json", journal)


def install_plan(plan):
    destination = managed_destination(Path(plan["destination"]))
    with installer.installation_lock(destination):
        runs = safe_child(destination, ".installer/html-runtime/runs")
        for publication in sorted(runs.glob("*/publication.json")):
            installer.unlinked_path(publication)
            prior = installer.read_json(publication)
            if prior.get("state") == "publishing":
                rollback(destination, publication.parent, prior)
        run = safe_child(destination, ".installer/html-runtime/runs/" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
        run.mkdir(parents=True, exist_ok=False)
        report = {**plan, "status": "staging", "run": str(run), "started_at": installer.now()}
        installer.atomic_json(run / "report.json", report)
        journal = None
        try:
            refreshed = make_plan(Path(plan["source"]), destination, Path(plan["configuration"]))
            if refreshed != plan:
                raise InstallError("Frozen source/configuration changed after planning")
            marker = safe_child(destination, ".installer/html-runtime/installed.json")
            if marker.is_file():
                shutil.copy2(marker, run / "previous-installed.json")
            for tree in plan["trees"]:
                source = installer.unlinked_path(Path(tree["source"]))
                check_inventory(source, tree["files"])
                _, staged, _ = _journal_paths(destination, run, tree)
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, staged, symlinks=False)
                check_inventory(staged, tree["files"])
            if make_plan(Path(plan["source"]), destination, Path(plan["configuration"])) != plan:
                raise InstallError("Frozen source/configuration changed while staging")
            journal = {"schema_version": "1.0", "state": "publishing", "trees": [
                {"relative": tree["relative"], "had_previous": _journal_paths(destination, run, tree)[0].exists()}
                for tree in plan["trees"]]}
            installer.atomic_json(run / "publication.json", journal)
            for tree in journal["trees"]:
                target, staged, previous = _journal_paths(destination, run, tree)
                installer.unlinked_tree(target)
                if target.exists():
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, previous)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
            verify_installed(plan)
            report.update(status="installed", finished_at=installer.now(), backup_path=str(destination / "backups" / ("html-runtime-" + run.name)))
            installer.atomic_json(run / "report.json", report)
            installer.atomic_json(destination / ".installer/html-runtime/installed.json", report)
            committed = {**journal, "state": "committed"}
            installer.atomic_json(run / "publication.json", committed)
            journal = committed
            return report
        except BaseException as error:
            if journal is not None and journal["state"] == "publishing":
                rollback(destination, run, journal)
            report.update(status="failed", finished_at=installer.now(), error={"type": type(error).__name__, "message": str(error)}, old_runtime_restored=journal is not None)
            installer.atomic_json(run / "report.json", report)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--configuration", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = make_plan(args.source, args.destination, args.configuration)
        if args.dry_run:
            result = {**plan, "status": "plan_verified", "installation_executed": False}
        elif args.verify_only:
            verify_installed(plan)
            result = {**plan, "status": "installed_inventory_verified", "installation_executed": False}
        else:
            result = install_plan(plan)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error": {"type": type(error).__name__, "message": str(error)}}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
