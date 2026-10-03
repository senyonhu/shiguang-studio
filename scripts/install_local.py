"""Install the personal Windows bridge without copying development projects.

Run with the detected Python 3.12. This installer creates a destination-local
venv, verifies the pinned official SDK archive, and optionally registers only
this plugin through native Codex argument-array commands. It does not bundle
Python, edit Codex configuration files, or prove a desktop session was refreshed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
import zipfile


SDK_RELEASE = "8.5.3"
SDK_VERSION = "8.5.3.26051504"
SDK_SHA256 = "ff57648f9c04f27e381c48af6d8e3ee3cdec296bed4d3831f47f09b0a71b505e"
SDK_ARCHIVE = f"renpy-{SDK_RELEASE}-sdk.zip"
SDK_DIRECTORY = f"renpy-{SDK_RELEASE}"
SDK_URL = f"https://www.renpy.org/dl/{SDK_RELEASE}/{SDK_ARCHIVE}"
MARKETPLACE = "renpy-bridge-personal"
PLUGIN = "renpy-bridge"
AUTHORING_TREE = "examples/html_minigames/production/v1.0.1"
AUTHORING_GUIDE = "examples/html_minigames/production/SDK.md"
MANAGED = {"src", "plugin", "docs", "pyproject.toml", "requirements.lock",
           "README.md", "START_HERE.md", "AGENTS.md", "USER_RULES.md", "user-rules.md", "user_rules",
           "tools/probe_html_cdp.mjs", AUTHORING_TREE, AUTHORING_GUIDE}
COPY_TREES = ("src/renpy_bridge", "plugin", "docs", "user_rules", AUTHORING_TREE)
COPY_FILES = ("pyproject.toml", "requirements.lock", "README.md", "START_HERE.md", "AGENTS.md",
              "USER_RULES.md", "user-rules.md", "tools/probe_html_cdp.mjs", AUTHORING_GUIDE)
EXTENSIONS = {".py", ".json", ".toml", ".md", ".txt", ".yaml", ".yml", ".png", ".svg"}
AUTHORING_EXTENSIONS = EXTENSIONS | {".js", ".html", ".css", ".wav"}
PIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9.!+_-]*")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


class InstallError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InstallError(f"Expected a JSON object: {path}")
    return value


def linked(path: Path) -> bool:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def unlinked_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(path.expanduser()))
    for ancestor in (absolute, *absolute.parents):
        if linked(ancestor):
            raise InstallError(f"Installation paths cannot traverse links or junctions: {ancestor}")
    return absolute.resolve()


def bounded(root: Path, path: Path) -> Path:
    absolute = path.resolve()
    if absolute == root or not absolute.is_relative_to(root):
        raise InstallError(f"Managed operation leaves installation root: {path}")
    unlinked_path(path)
    return absolute


def unlinked_tree(root: Path) -> None:
    unlinked_path(root)
    if root.is_dir():
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in (*dirs, *files):
                if linked(Path(directory) / name):
                    raise InstallError(f"Managed code/configuration cannot contain links: {Path(directory) / name}")


def source_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for name in COPY_FILES:
        candidate = source / name
        if candidate.exists():
            if linked(candidate) or not candidate.is_file():
                raise InstallError(f"Source file must be a regular file: {candidate}")
            files.append(candidate)
    for name in COPY_TREES:
        tree = source / name
        authoring_tree = name == AUTHORING_TREE
        if not tree.exists():
            continue
        if linked(tree) or not tree.is_dir():
            raise InstallError(f"Source tree must be a directory without links: {tree}")
        for directory, dirs, names in os.walk(tree, followlinks=False):
            current = Path(directory)
            for entry in dirs:
                if linked(current / entry):
                    raise InstallError(f"Source trees cannot contain links: {current / entry}")
            dirs[:] = [entry for entry in dirs if entry not in {
                "__pycache__", ".pytest_cache", ".git", "node_modules", "evidence",
                "projects", "jobs", "backups", "upstream", ".venv", ".runtime",
            } and not entry.endswith(".egg-info")]
            for name in names:
                candidate = current / name
                if linked(candidate):
                    raise InstallError(f"Source trees cannot contain links: {candidate}")
                if authoring_tree and not candidate.is_file():
                    raise InstallError(f"Curated HTML authoring templates require regular files: {candidate}")
                extensions = AUTHORING_EXTENSIONS if authoring_tree else EXTENSIONS
                if authoring_tree and candidate.suffix.lower() not in extensions:
                    raise InstallError(f"Unsupported file in curated HTML authoring templates: {candidate}")
                if candidate.suffix.lower() in extensions and candidate.is_file():
                    files.append(candidate)
    relative = {path.relative_to(source).as_posix() for path in files}
    required = {"pyproject.toml", "requirements.lock", "src/renpy_bridge/server.py",
                "src/renpy_bridge/worker.py", "plugin/renpy-bridge/.codex-plugin/plugin.json",
                "plugin/.agents/plugins/marketplace.json"}
    if required - relative:
        raise InstallError(f"Installation source is incomplete: {sorted(required - relative)}")
    if "src/renpy_bridge/minigame_qa.py" in relative and "tools/probe_html_cdp.mjs" not in relative:
        raise InstallError("HTML minigame QA requires its curated browser driver tools/probe_html_cdp.mjs.")
    if "src/renpy_bridge/minigame_qa.py" in relative:
        authoring_required = {AUTHORING_GUIDE, AUTHORING_TREE + "/sdk/bridge-sdk.js", AUTHORING_TREE + "/package-digests.json"}
        authoring_required.update(AUTHORING_TREE + "/" + package + "/" + member
                                 for package in ("puzzle", "timed", "checkpoint")
                                 for member in ("manifest.json", "index.html", "bridge-sdk.js", "game.js", "styles.css", "LICENSE.txt", "instructions.txt"))
        authoring_required.add(AUTHORING_TREE + "/timed/tap.wav")
        if authoring_required - relative:
            raise InstallError(f"HTML minigame authoring SDK/templates are incomplete: {sorted(authoring_required - relative)}")
    if sum(path.stat().st_size for path in files) > 100 * 1024 * 1024:
        raise InstallError("Code/document bundle exceeds the 100 MiB installation bound.")
    return sorted(files)


def third_party_lock(path: Path, source: Path) -> str:
    lines: list[str] = []
    editable_bridge = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            editable_bridge = "renpy-codex-bridge" in line.lower()
            continue
        if line.startswith(("-e ", "--editable ")):
            editable_path = line.split(maxsplit=1)[1]
            if not editable_bridge and Path(editable_path).expanduser().resolve() != source:
                raise InstallError("Only the local bridge editable requirement may be removed.")
            editable_bridge = False
            continue
        editable_bridge = False
        if line.lower().startswith(("renpy-codex-bridge==", "renpy_codex_bridge==")):
            continue
        if not PIN.fullmatch(line):
            raise InstallError(f"Dependency lock must contain only fixed third-party versions: {line}")
        lines.append(line)
    if not any(line.lower().startswith("setuptools==") for line in lines):
        raise InstallError("Lock setuptools before using --no-build-isolation.")
    if len({line.split("==", 1)[0].lower().replace("_", "-") for line in lines}) != len(lines):
        raise InstallError("Dependency lock contains duplicate package identities.")
    return "\n".join(lines) + "\n"


def installed_mcp(destination: Path) -> dict:
    return {"mcpServers": {"renpy_bridge": {
        "command": str(destination / ".runtime/Scripts/python.exe"),
        "args": ["-m", "renpy_bridge.server"],
        "env": {"RENPY_BRIDGE_HOME": str(destination), "PYTHONPATH": str(destination / "src"),
                "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"},
    }}}


def stage_code(source: Path, files: list[Path], stage: Path, destination: Path,
               expected_hashes: dict[str, str]) -> list[str]:
    for original in files:
        relative = original.relative_to(source)
        target = stage / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        if sha256(target) != expected_hashes[relative.as_posix()]:
            raise InstallError(f"Installation source changed while staging: {relative}")
    (stage / "requirements.lock").write_text(third_party_lock(stage / "requirements.lock", source),
                                             encoding="utf-8", newline="\n")
    atomic_json(stage / "plugin/renpy-bridge/.mcp.json", installed_mcp(destination))
    marketplace_path = stage / "plugin/.agents/plugins/marketplace.json"
    marketplace = read_json(marketplace_path)
    marketplace["name"] = MARKETPLACE
    marketplace["interface"] = {"displayName": "RenPy Bridge 个人安装"}
    matching = [item for item in marketplace.get("plugins", []) if item.get("name") == PLUGIN]
    if len(matching) != 1 or matching[0].get("source") != {"source": "local", "path": "./renpy-bridge"}:
        raise InstallError("Local marketplace must reference exactly ./renpy-bridge.")
    marketplace["plugins"] = matching
    atomic_json(marketplace_path, marketplace)
    manifest_path = stage / "plugin/renpy-bridge/.codex-plugin/plugin.json"
    manifest = read_json(manifest_path)
    if manifest.get("name") != PLUGIN or manifest.get("mcpServers") != "./.mcp.json":
        raise InstallError("Unexpected plugin identity or MCP entry point.")
    if manifest.get("skills") != "./skills/":
        raise InstallError("Release plugin must declare skills: ./skills/.")
    for path in (manifest_path, marketplace_path, stage / "plugin/renpy-bridge/.mcp.json"):
        content = path.read_text(encoding="utf-8").replace("\\\\", "/").replace("\\", "/")
        if str(source).replace("\\", "/").lower() in content.lower():
            raise InstallError(f"Development absolute path remained in installed configuration: {path}")
    # Curated drivers and authoring versions have their own managed scopes.
    # Existing tools/ and examples/ roots can contain user-owned siblings.
    return sorted(name for name in MANAGED if (stage / name).exists())


def sdk_entries(archive: Path) -> list[tuple[zipfile.ZipInfo, Path]]:
    output: list[tuple[zipfile.ZipInfo, Path]] = []
    seen: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 100000 or sum(item.file_size for item in entries) > 1024 * 1024 * 1024:
            raise InstallError("SDK ZIP exceeds the fixed extraction bounds.")
        for item in entries:
            name = item.filename
            parts = PurePosixPath(name).parts
            mode = item.external_attr >> 16
            if (not parts or parts[0] != f"renpy-{SDK_RELEASE}-sdk" or name.startswith("/")
                    or "\\" in name or "\x00" in name or ":" in name
                    or any(part in {".", ".."} for part in parts)
                    or stat.S_ISLNK(mode) or item.flag_bits & 1):
                raise InstallError(f"Unsafe SDK archive member: {name}")
            if any(part.rstrip(" .") != part or part.split(".", 1)[0].upper() in WINDOWS_RESERVED
                   for part in parts):
                raise InstallError(f"Unsafe Windows SDK archive member: {name}")
            relative = Path(*parts[1:])
            if not parts[1:]:
                continue
            identity = relative.as_posix().casefold()
            if identity in seen:
                raise InstallError(f"Duplicate SDK archive member: {name}")
            seen.add(identity)
            output.append((item, relative))
    return output


def sdk_tree_inventory(root: Path) -> dict[str, dict]:
    """Capture the complete reused tree, including user files and empty dirs."""
    unlinked_tree(root)
    records = {}
    identities = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        identity = relative.casefold()
        if identity in identities:
            raise InstallError(f"SDK tree has conflicting Windows paths: {relative}")
        identities.add(identity)
        if path.is_dir():
            records[relative] = {"kind": "directory"}
        elif path.is_file():
            records[relative] = {"kind": "file", "bytes": path.stat().st_size, "sha256": sha256(path)}
        else:
            raise InstallError(f"SDK tree contains a non-regular entry: {relative}")
    return records


def sdk_cache_source(relative: Path, archived_files: set[Path]) -> Path | None:
    """A cache is mutable only when its real authority is in the pinned ZIP."""
    suffix = relative.suffix.lower()
    candidate = None
    if suffix == ".rpyc":
        # Ren'Py 8.5.3 script.load_script/load_appropriate_file prefers the
        # shipped <basename>_ren.py authority before <basename>.rpy.
        ren_source = relative.with_name(relative.stem + "_ren.py")
        candidate = ren_source if ren_source in archived_files else relative.with_suffix(".rpy")
    elif suffix == ".rpymc":
        candidate = relative.with_suffix(".rpym")
    elif suffix in {".pyc", ".pyo"}:
        if relative.parent.name == "__pycache__":
            matched = re.fullmatch(r"(.+)\.cpython-\d+(?:\.opt-\d+)?\.pyc", relative.name)
            if matched:
                candidate = relative.parent.parent / (matched[1] + ".py")
        else:
            candidate = relative.with_suffix(".py")
    return candidate if candidate in archived_files else None


def sdk_matches(root: Path, archive: Path, entries: list[tuple[zipfile.ZipInfo, Path]]) -> tuple[bool, list[dict]]:
    hashes: list[dict] = []
    unlinked_path(root)
    if not root.exists():
        return False, hashes
    if not root.is_dir():
        raise InstallError("Existing SDK path must be a directory; it will not be replaced.")
    # Check the entire tree, not just ZIP members: an extra launcher save or
    # unknown link must not be silently discarded by a source-only update.
    sdk_tree_inventory(root)
    archived_files = {relative for item, relative in entries if not item.is_dir()}
    with zipfile.ZipFile(archive) as bundle:
        for item, relative in entries:
            target = root / relative
            if item.is_dir():
                below = [path for path in archived_files if path.is_relative_to(relative)]
                cache_only = below and all(sdk_cache_source(path, archived_files) for path in below)
                if not target.exists() and cache_only:
                    continue
                if not target.is_dir() or linked(target):
                    raise InstallError(f"Existing SDK directory is missing or unsafe: {relative}")
                continue
            authority = sdk_cache_source(relative, archived_files)
            if target.exists() and not target.is_file():
                raise InstallError(f"Existing SDK file is not regular: {relative}")
            unlinked_path(target)
            archived = hashlib.sha256()
            with bundle.open(item) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    archived.update(chunk)
            actual = sha256(target) if target.is_file() else None
            if authority is None and actual != archived.hexdigest():
                raise InstallError(f"Existing SDK authoritative file is missing or changed: {relative}; preserve it and repair explicitly.")
            # Each authority itself is an immutable ZIP member and is checked
            # in this same pass before any runtime command can be executed.
            hashes.append({"path": relative.as_posix(), "sha256": actual,
                "archive_sha256": archived.hexdigest(), "mutable_cache": authority is not None,
                "authority": authority.as_posix() if authority else None})
    return True, hashes


def safe_extract(archive: Path, target: Path, entries: list[tuple[zipfile.ZipInfo, Path]]) -> list[dict]:
    target.mkdir(parents=True, exist_ok=False)
    hashes = []
    with zipfile.ZipFile(archive) as bundle:
        for item, relative in entries:
            output = bounded(target.resolve(), target / relative)
            if item.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as original, output.open("xb") as installed:
                    shutil.copyfileobj(original, installed, length=1024 * 1024)
                hashes.append({"path": relative.as_posix(), "sha256": sha256(output)})
    return hashes


def verify_sdk_version(archive: Path, entries: list[tuple[zipfile.ZipInfo, Path]], run: Path, report: dict) -> None:
    # Even --version can rewrite Python/Ren'Py caches. Execute only a pristine
    # isolated ZIP copy, never the reused tree or the tree about to be published.
    probe = run / "sdk-version-probe"
    safe_extract(archive, probe, entries)
    response = execute([str(probe / "lib/py3-windows-x86_64/python.exe"),
                        str(probe / "renpy.py"), "--version"], probe, run,
                       "sdk-version", report, timeout=60,
                       env_changes={"PYTHONDONTWRITEBYTECODE": "1",
                                    "RENPY_PATH_TO_SAVES": str(probe / "isolated-saves")})
    if not re.search(r"Ren['’]Py\s+" + re.escape(SDK_VERSION) + r"\b", response):
        raise InstallError("Verified SDK runtime did not report the expected full version.")
    report["sdk"].update(runtime_version_verified=True, version_probe_isolated=True,
                         version_probe_root=str(probe))


def require_unchanged_sdk(root: Path, expected: dict[str, dict] | None) -> None:
    if expected is not None and sdk_tree_inventory(root) != expected:
        raise InstallError("Reused SDK changed during installation; source publication is refused.")


def require_idle_jobs(destination: Path) -> None:
    for path in (destination / "jobs").glob("*/status.json"):
        bounded(destination, path)
        status = read_json(path)
        if status.get("state") in {"queued", "running"}:
            raise InstallError(f"Finish/cancel managed jobs and close this bridge before upgrading: {status.get('job_id', path.parent.name)}")


def acquire_archive(source: Path, destination: Path, sdk_source: Path | None, run: Path) -> tuple[Path, str]:
    archive = bounded(destination, destination / "runtimes" / SDK_ARCHIVE)
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.is_file() and sha256(archive) == SDK_SHA256:
        return archive, "existing_verified_archive"
    candidates = []
    if sdk_source is not None:
        if sdk_source.is_file():
            candidates.append(sdk_source)
        elif sdk_source.is_dir():
            candidates.extend((sdk_source / SDK_ARCHIVE, sdk_source.parent / SDK_ARCHIVE))
        else:
            raise InstallError(f"SDK source is missing: {sdk_source}")
    candidates.append(source / "runtimes" / SDK_ARCHIVE)
    chosen = next((path for path in candidates if path.is_file() and sha256(path) == SDK_SHA256), None)
    downloaded = run / SDK_ARCHIVE
    if chosen is not None:
        shutil.copy2(chosen, downloaded)
        origin = "copied_verified_archive"
    else:
        if sdk_source is not None:
            raise InstallError("Explicit SDK source has no official archive matching the pinned SHA256.")
        request = urllib.request.Request(SDK_URL, headers={"User-Agent": "renpy-bridge-personal-installer/0.1"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=60) as response, downloaded.open("xb") as output:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > 1024 * 1024 * 1024 or time.monotonic() - started > 1800:
                    raise InstallError("SDK download exceeded its size/time limit.")
                output.write(chunk)
        origin = "downloaded_official_archive"
    if sha256(downloaded) != SDK_SHA256:
        raise InstallError("SDK archive SHA256 does not match the fixed official release.")
    if archive.exists():
        os.replace(archive, run / "previous-invalid-sdk.zip")
    os.replace(downloaded, archive)
    return archive, origin


@contextmanager
def installation_lock(destination: Path):
    path = bounded(destination, destination / ".installer/install.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if stream.read(1) == b"":
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise InstallError("Another installer owns this destination.") from error
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def stop_owned(process: subprocess.Popen, output) -> dict:
    if process.poll() is not None:
        return {"root_exit_code": process.returncode, "root_already_exited": True,
                "descendant_cleanup_verified": False}
    details = {"owned_root_pid": process.pid, "descendant_cleanup_verified": False}
    if os.name == "nt":
        # Popen retains the owned Windows process handle. Its PID cannot be
        # recycled while this handle remains open; /T targets only this tree.
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/taskkill.exe"
        try:
            result = subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                                    stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    shell=False, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            details.update({"tree_stop_exit_code": result.returncode,
                            "tree_stop_command_succeeded": result.returncode == 0})
        except (OSError, subprocess.TimeoutExpired) as error:
            details["tree_stop_error"] = str(error)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=30)
    details["root_exit_code"] = process.returncode
    return details


def execute(argv: list[str], cwd: Path, run: Path, label: str, report: dict,
            timeout: float = 900, env_changes: dict[str, str] | None = None) -> str:
    log = run / f"{label}.log"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
    env.update(env_changes or {})
    started = time.monotonic()
    record = {"label": label, "argv": argv, "cwd": str(cwd), "started_at": now(), "log_path": str(log)}
    report.setdefault("commands", []).append(record)
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, shell=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            cleanup = stop_owned(process, output)
            record.update({"timed_out": True, "exit_code": process.returncode, "cleanup": cleanup,
                           "finished_at": now(), "duration_seconds": round(time.monotonic() - started, 3)})
            raise InstallError(f"Installation command exceeded its limit: {label}")
        except KeyboardInterrupt:
            cleanup = stop_owned(process, output)
            record.update({"interrupted": True, "exit_code": process.returncode, "cleanup": cleanup,
                           "finished_at": now(), "duration_seconds": round(time.monotonic() - started, 3)})
            raise
    record.update({"exit_code": code, "timed_out": False, "finished_at": now(),
                   "duration_seconds": round(time.monotonic() - started, 3), "log_sha256": sha256(log)})
    if code:
        raise InstallError(f"Installation command failed ({code}); read {log}")
    return log.read_text(encoding="utf-8", errors="replace")


def codex_executable(explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".exe":
            return candidate
        raise InstallError("--codex-executable must identify the native Codex .exe.")
    command = shutil.which("codex.exe")
    if command:
        return Path(command).resolve()
    wrapper = shutil.which("codex.cmd") or shutil.which("codex")
    if wrapper:
        package = Path(wrapper).parent / "node_modules/@openai/codex"
        architecture = "aarch64-pc-windows-msvc" if platform.machine().lower() in {"arm64", "aarch64"} else "x86_64-pc-windows-msvc"
        candidates = sorted(path for path in package.rglob("codex.exe") if architecture in path.parts)
        if len(candidates) == 1:
            return candidates[0].resolve()
    raise InstallError("Native Codex executable not found; pass --codex-executable. No shell wrapper is executed.")


def copy_backup(destination: Path, backup: Path, names: list[str]) -> None:
    for name in names:
        if name not in MANAGED:
            raise InstallError(f"Unexpected managed entry: {name}")
        original = bounded(destination, destination / name)
        if not original.exists():
            continue
        unlinked_tree(original)
        target = backup / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if original.is_dir():
            shutil.copytree(original, target, symlinks=False)
        else:
            shutil.copy2(original, target)
    lock = bounded(destination, destination / "runtimes/sdk-lock.json")
    if lock.is_file():
        target = backup / "runtimes/sdk-lock.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lock, target)
    marker = bounded(destination, destination / ".installer/installed.json")
    if marker.is_file():
        shutil.copy2(marker, backup / "installed.json")


def restore_configs(destination: Path, run: Path) -> None:
    backup = bounded(destination, destination / "backups" / f"install-{run.name}")
    for previous, target, stash_name in (
        (backup / "runtimes/sdk-lock.json", destination / "runtimes/sdk-lock.json", "sdk-lock.json"),
        (backup / "installed.json", destination / ".installer/installed.json", "installed.json"),
    ):
        bounded(destination, target)
        if previous.is_file():
            atomic_json(target, read_json(previous))
        elif target.exists():
            stash = bounded(destination, run / "rolled-back-config" / stash_name)
            stash.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, stash)


def validate_publication(journal: dict) -> None:
    if journal.get("schema_version", "1.0") != "1.0" or journal.get("state") not in {"publishing", "committed", "rolled_back"}:
        raise InstallError("Unsupported interrupted-install publication journal.")
    entries = journal.get("entries")
    if not isinstance(entries, list) or not entries:
        raise InstallError("Interrupted-install journal needs managed entries.")
    names = []
    for item in entries:
        if not isinstance(item, dict) or item.get("name") not in MANAGED or type(item.get("had_previous")) is not bool:
            raise InstallError("Unsafe interrupted-install journal entry or previous-state flag.")
        if item.get("state") not in {"pending", "previous_moved", "published"}:
            raise InstallError("Unknown interrupted-install entry state.")
        names.append(item["name"])
    if len(names) != len(set(names)):
        raise InstallError("Interrupted-install journal repeats a managed entry.")
    sdk = journal.get("sdk")
    if sdk is not None and (not isinstance(sdk, dict) or sdk.get("directory") != SDK_DIRECTORY
                           or type(sdk.get("had_previous")) is not bool
                           or sdk.get("state") not in {"pending", "previous_moved", "published"}):
        raise InstallError("Unsafe interrupted-install SDK journal.")


def rollback_publication(destination: Path, run: Path, journal: dict) -> None:
    # Validate every entry before touching any of them. A malformed later
    # entry must not turn a rejected journal into a partial filesystem write.
    validate_publication(journal)
    unlinked_tree(bounded(destination, run))
    unlinked_tree(bounded(destination, destination / "backups" / f"install-{run.name}"))
    for item in journal["entries"]:
        unlinked_tree(bounded(destination, destination / item["name"]))
    if journal.get("sdk") is not None:
        unlinked_tree(bounded(destination, destination / "runtimes" / SDK_DIRECTORY))
    for item in reversed(journal["entries"]):
        name = item["name"]
        target = bounded(destination, destination / name)
        previous = bounded(destination, run / "replaced" / name)
        staged = bounded(destination, run / "code" / name)
        # Filesystem presence also covers a crash between rename and journal write.
        restore = previous.exists()
        remove_new = not item["had_previous"] and not staged.exists()
        if restore or remove_new:
            if target.exists():
                failed = run / "rolled-back-new" / name
                failed.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, bounded(destination, failed))
            if restore:
                os.replace(previous, target)
    if journal.get("sdk") is not None:
        item = journal["sdk"]
        target = bounded(destination, destination / "runtimes" / SDK_DIRECTORY)
        previous = bounded(destination, run / "replaced-sdk" / SDK_DIRECTORY)
        staged = bounded(destination, run / "sdk")
        restore = previous.exists()
        remove_new = not item["had_previous"] and not staged.exists()
        if restore or remove_new:
            if target.exists():
                failed = bounded(destination, run / "rolled-back-sdk" / SDK_DIRECTORY)
                failed.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, failed)
            if restore:
                os.replace(previous, target)
    restore_configs(destination, run)
    journal["state"] = "rolled_back"
    journal["finished_at"] = now()
    atomic_json(run / "publication.json", journal)


def recover_interrupted(destination: Path) -> list[str]:
    recovered = []
    pending = []
    root = bounded(destination, destination / ".installer/runs")
    for path in sorted(root.glob("*/publication.json")):
        bounded(destination, path)
        journal = read_json(path)
        validate_publication(journal)
        unlinked_tree(path.parent)
        if journal.get("state") == "publishing":
            pending.append((path, journal))
    for path, journal in pending:
        rollback_publication(destination, path.parent, journal)
        recovered.append(str(path))
    return recovered


def publish_code(destination: Path, run: Path, names: list[str], sdk_stage: Path | None = None) -> dict:
    journal = {"schema_version": "1.0", "state": "publishing", "started_at": now(), "entries": [
        {"name": name, "had_previous": (destination / name).exists(), "state": "pending"} for name in names]}
    if sdk_stage is not None:
        if bounded(destination, sdk_stage) != bounded(destination, run / "sdk"):
            raise InstallError("SDK publication must use this run's verified staging directory.")
        journal["sdk"] = {"directory": SDK_DIRECTORY, "had_previous": (destination / "runtimes" / SDK_DIRECTORY).exists(), "state": "pending"}
    validate_publication(journal)
    for name in names:
        unlinked_tree(bounded(destination, destination / name))
        unlinked_tree(bounded(destination, run / "code" / name))
    if sdk_stage is not None:
        unlinked_tree(bounded(destination, destination / "runtimes" / SDK_DIRECTORY))
        unlinked_tree(sdk_stage)
    atomic_json(run / "publication.json", journal)
    if sdk_stage is not None:
        target = bounded(destination, destination / "runtimes" / SDK_DIRECTORY)
        if target.exists():
            replaced = bounded(destination, run / "replaced-sdk" / SDK_DIRECTORY)
            replaced.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, replaced)
        journal["sdk"]["state"] = "previous_moved"
        atomic_json(run / "publication.json", journal)
        os.replace(sdk_stage, target)
        journal["sdk"]["state"] = "published"
        atomic_json(run / "publication.json", journal)
    for item in journal["entries"]:
        target = bounded(destination, destination / item["name"])
        staged = bounded(destination, run / "code" / item["name"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            replaced = bounded(destination, run / "replaced" / item["name"])
            replaced.parent.mkdir(parents=True, exist_ok=True)
            os.replace(target, replaced)
        item["state"] = "previous_moved"
        atomic_json(run / "publication.json", journal)
        os.replace(staged, target)
        item["state"] = "published"
        atomic_json(run / "publication.json", journal)
    return journal


def run_install(args, source: Path, destination: Path, files: list[Path], run: Path, report: dict) -> None:
    with installation_lock(destination):
        report["installation_status"] = "preparing"
        marker = bounded(destination, destination / ".installer/installed.json")
        if marker.exists():
            existing = read_json(marker)
            if existing.get("schema_version") != "1.0" or not set(existing.get("managed_entries", [])) <= MANAGED:
                raise InstallError("Existing installation marker has an unsupported schema or managed scope.")
        report["recovered_publications"] = recover_interrupted(destination)
        if not marker.exists() and any((destination / name).exists() for name in MANAGED):
            raise InstallError("Destination contains unmanaged code/configuration; choose an empty installation directory.")
        require_idle_jobs(destination)
        stage = run / "code"
        stage.mkdir()
        names = stage_code(source, files, stage, destination,
                           {item["path"]: item["sha256"] for item in report["source_files"]})
        report["managed_entries"] = names
        backup = bounded(destination, destination / "backups" / f"install-{run.name}")
        backup.mkdir(parents=True)
        copy_backup(destination, backup, names)
        report["backup_path"] = str(backup)
        atomic_json(run / "report.json", report)

        archive, origin = acquire_archive(source, destination, args.sdk_source, run)
        entries = sdk_entries(archive)
        sdk_root = bounded(destination, destination / "runtimes" / SDK_DIRECTORY)
        matches, sdk_hashes = sdk_matches(sdk_root, archive, entries)
        sdk_before = sdk_tree_inventory(sdk_root) if matches else None
        sdk_stage = sdk_root if matches else run / "sdk"
        if not matches:
            sdk_hashes = safe_extract(archive, sdk_stage, entries)
        atomic_json(run / "sdk-files.json", {"archive_sha256": SDK_SHA256, "files": sdk_hashes,
                                           "reused_tree": sdk_before})
        report["sdk"] = {"release": SDK_RELEASE, "version": SDK_VERSION, "archive": str(archive),
                         "archive_sha256": sha256(archive), "acquisition": origin, "reused_sdk": matches,
                         "root": str(sdk_root), "official_files": len(sdk_hashes),
                         "file_hash_report": str(run / "sdk-files.json"), "verified_original_files_only": not matches,
                         "authoritative_archive_files_verified": True,
                         "reused_full_tree_files": sum(record["kind"] == "file" for record in (sdk_before or {}).values()),
                         "mutable_cache_files": sum(record.get("mutable_cache", False) for record in sdk_hashes),
                         "reused_full_tree_preserved": False,
                         "runtime_version_verified": False}
        verify_sdk_version(archive, entries, run, report)
        require_unchanged_sdk(sdk_root, sdk_before)
        runtime = bounded(destination, destination / ".runtime")
        runtime_python = runtime / "Scripts/python.exe"
        if runtime.exists() and not runtime_python.is_file():
            raise InstallError("Existing .runtime is incomplete; preserve it and choose a new destination.")
        if not runtime_python.is_file():
            execute([sys.executable, "-m", "venv", str(runtime)], destination, run, "create-venv", report, timeout=120)
        version = execute([str(runtime_python), "-c", "import json,sys;print(json.dumps({'version':list(sys.version_info[:3]),'base_prefix':sys.base_prefix,'executable':sys.executable}))"],
                          destination, run, "python-version", report, timeout=30)
        info = json.loads(version)
        if info.get("version", [])[:2] != [3, 12]:
            raise InstallError("Destination runtime must use detected Python 3.12.")
        report["python"] = {**info, "bundled": False, "external_base_python_required": True}
        execute([str(runtime_python), "-m", "pip", "install", "--no-input", "-r", str(stage / "requirements.lock")],
                destination, run, "install-locked-dependencies", report, timeout=1800)
        execute([str(runtime_python), "-m", "pip", "install", "--no-input", "--no-deps", "--no-build-isolation", str(stage)],
                destination, run, "install-local-project", report, timeout=300)
        execute([str(runtime_python), "-m", "pip", "check"], destination, run, "pip-check", report, timeout=60)
        freeze = execute([str(runtime_python), "-m", "pip", "freeze", "--exclude-editable"],
                         destination, run, "installed-dependencies", report, timeout=60)
        report["installed_dependency_report"] = str(run / "installed-dependencies.log")
        report["installed_dependency_sha256"] = hashlib.sha256(freeze.encode("utf-8")).hexdigest()
        # Setuptools writes build metadata beside package sources. Preserve it
        # with run evidence instead of publishing an additional source package.
        for item in (stage / "src").iterdir():
            if item.name != "renpy_bridge":
                if not item.name.endswith(".egg-info"):
                    raise InstallError(f"Unexpected source build output: {item.name}")
                build_output = bounded(destination, run / "build-output" / item.name)
                build_output.parent.mkdir(parents=True, exist_ok=True)
                os.replace(item, build_output)
        atomic_json(run / "report.json", report)

        journal = None
        try:
            require_idle_jobs(destination)
            require_unchanged_sdk(sdk_root, sdk_before)
            journal = publish_code(destination, run, names, sdk_stage=None if matches else sdk_stage)
            sdk_lock = {"release": SDK_RELEASE, "version": SDK_VERSION,
                        "relative_path": f"runtimes/{SDK_DIRECTORY}", "download_url": SDK_URL,
                        "sha256": SDK_SHA256, "verified_report": f".installer/runs/{run.name}/report.json"}
            atomic_json(bounded(destination, destination / "runtimes/sdk-lock.json"), sdk_lock)
            env = {"RENPY_BRIDGE_HOME": str(destination), "PYTHONPATH": str(destination / "src")}
            environment = execute([str(runtime_python), "-m", "renpy_bridge.cli", "environment", "--no-runtime"],
                                  destination, run, "installed-environment", report, timeout=60, env_changes=env)
            parsed = json.loads(environment)
            if parsed.get("ok") is not True or Path(parsed.get("home", "")).resolve() != destination:
                raise InstallError("Installed core did not report the destination home.")
            report["installed_environment"] = parsed
            require_unchanged_sdk(sdk_root, sdk_before)
            report["sdk"]["reused_full_tree_preserved"] = matches
            installed_files = [
                {"path": path.relative_to(destination).as_posix(), "sha256": sha256(path)}
                for name in names for path in sorted((destination / name).rglob("*"))
                if (destination / name).is_dir() and path.is_file() and "__pycache__" not in path.parts
            ]
            installed_files.extend({"path": name, "sha256": sha256(destination / name)}
                                   for name in names if (destination / name).is_file())
            atomic_json(run / "installed-files.json", {"files": installed_files})
            report["file_hash_report"] = str(run / "installed-files.json")
            atomic_json(marker, {"schema_version": "1.0", "installed_at": now(), "run_id": run.name,
                                 "destination": str(destination), "managed_entries": names,
                                 "report_path": str(run / "report.json"), "sdk_sha256": SDK_SHA256})
            journal["state"] = "committed"
            journal["finished_at"] = now()
            atomic_json(run / "publication.json", journal)
            report["installation_status"] = "installed"
        except BaseException:
            if journal is None and (run / "publication.json").exists():
                journal = read_json(run / "publication.json")
            if journal is not None:
                rollback_publication(destination, run, journal)
            report["installation_status"] = "code_rolled_back"
            report["runtime_rollback"] = "Code, SDK tree and lock/installation markers are restored. Destination venv dependency/local-wheel updates and verified SDK archive cache are retained; see command logs. This is not full-environment atomic rollback."
            raise

        if args.register:
            codex = codex_executable(args.codex_executable)
            report["registration"] = {"requested": True, "status": "started",
                                      "marketplace": MARKETPLACE, "plugin": PLUGIN,
                                      "desktop_refresh_verified": False, "mcp_call_verified": False}
            report["registration"]["cli_version"] = execute([str(codex), "--version"], destination,
                run, "codex-version", report, timeout=30).strip()
            execute([str(codex), "plugin", "marketplace", "add", str(destination / "plugin"), "--json"],
                    destination, run, "register-marketplace", report, timeout=120)
            execute([str(codex), "plugin", "add", f"{PLUGIN}@{MARKETPLACE}", "--json"],
                    destination, run, "register-plugin", report, timeout=120)
            if args.replace_development_plugin:
                execute([str(codex), "plugin", "remove", "renpy-bridge@renpy-bridge-local", "--json"],
                        destination, run, "remove-own-development-plugin", report, timeout=120)
            execute([str(codex), "plugin", "list", "--marketplace", MARKETPLACE, "--json"],
                    destination, run, "registration-list", report, timeout=60)
            report["registration"].update({"status": "commands_succeeded",
                                          "own_development_plugin_removed": args.replace_development_plugin,
                                          "requires_new_host_session_and_actual_tool_call": True})
        else:
            report["registration"] = {"requested": False, "status": "not_requested"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path, help="Defaults to %%LOCALAPPDATA%%/RenPyBridge.")
    parser.add_argument("--sdk-source", type=Path, help="Verified official ZIP or SDK directory beside its ZIP.")
    parser.add_argument("--register", action="store_true", help="Use native Codex commands to register this personal plugin.")
    parser.add_argument("--codex-executable", type=Path, help="Optional native codex.exe for --register.")
    parser.add_argument("--replace-development-plugin", action="store_true",
                        help="After registration, remove only renpy-bridge@renpy-bridge-local to avoid duplicate tools.")
    parser.add_argument("--dry-run", action="store_true", help="Validate paths/source/lock and print the bounded plan without writing.")
    args = parser.parse_args(argv)
    report = {"schema_version": "1.0", "started_at": now(), "status": "started",
              "installation_status": "not_started", "registration": {"requested": args.register},
              "runtime_acceptance_verified": False, "user_projects_modified": False}
    run = destination = None
    try:
        if os.name != "nt" or sys.version_info[:2] != (3, 12):
            raise InstallError("This Windows installer must run under the detected Python 3.12.")
        if args.replace_development_plugin and not args.register:
            raise InstallError("--replace-development-plugin requires --register.")
        if args.destination is None and not os.environ.get("LOCALAPPDATA"):
            raise InstallError("LOCALAPPDATA is unavailable; specify --destination.")
        source = unlinked_path(args.source)
        destination = unlinked_path(args.destination or Path(os.environ["LOCALAPPDATA"]) / "RenPyBridge")
        if destination == source or source.is_relative_to(destination):
            raise InstallError("Destination cannot be the source checkout or its ancestor.")
        if any(destination.is_relative_to(source / name) for name in COPY_TREES):
            raise InstallError("Destination cannot be nested in a copied source tree.")
        files = source_files(source)
        lock = third_party_lock(source / "requirements.lock", source)
        report.update({"source": str(source), "destination": str(destination),
                       "source_files": [{"path": path.relative_to(source).as_posix(), "sha256": sha256(path)} for path in files],
                       "third_party_lock_sha256": hashlib.sha256(lock.encode("utf-8")).hexdigest(),
                       "preserved_directories": ["projects", "jobs", "backups", "checkpoints"],
                       "python_bundled": False, "sdk": {"release": SDK_RELEASE, "version": SDK_VERSION, "sha256": SDK_SHA256}})
        if args.dry_run:
            report.update({"status": "plan_validated", "installation_status": "not_executed", "finished_at": now()})
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        destination.mkdir(parents=True, exist_ok=True)
        # A packaged Windows host can virtualize LOCALAPPDATA on first mkdir.
        # Resolve the now-existing directory again before any bounded operation
        # or persistent MCP configuration, retaining the requested location.
        report["requested_destination"] = str(destination)
        destination = unlinked_path(destination)
        report["destination"] = str(destination)
        run = bounded(destination, destination / ".installer/runs" / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}")
        run.mkdir(parents=True)
        atomic_json(run / "report.json", report)
        run_install(args, source, destination, files, run, report)
        report["status"] = "completed"
        report["finished_at"] = now()
        atomic_json(run / "report.json", report)
        atomic_json(destination / ".installer/latest-report.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        report.update({"status": "failed", "finished_at": now(),
                       "error": {"type": type(error).__name__, "message": str(error)}})
        if run is not None:
            try:
                atomic_json(run / "report.json", report)
                atomic_json(destination / ".installer/latest-report.json", report)
            except OSError as report_error:
                report["report_write_error"] = str(report_error)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 130 if isinstance(error, KeyboardInterrupt) else 1


if __name__ == "__main__":
    raise SystemExit(main())
