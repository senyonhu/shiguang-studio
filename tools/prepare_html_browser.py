"""Prepare or freshly verify the pinned workspace-local Windows WebView2 runtime.

Default/--verify-only never downloads or modifies a runtime. --prepare publishes
only to a missing workspace runtimes/webview2-fixed-* directory; failed staging
is retained for diagnosis. This tool never reads an Evergreen installation.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
VERSION = "154.0.4258.53"
RUNTIME_NAME = "Microsoft.WebView2.FixedVersionRuntime." + VERSION + ".x64"
WRAPPER_NAME = "webview2-fixed-" + VERSION
PINNED = {
    "version": VERSION,
    "architecture": "x64",
    "mode": "fixed",
    "official_page": "https://developer.microsoft.com/en-us/microsoft-edge/webview2/",
    "source_url": "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/0b89c3a3-0043-4746-b39e-65830da7744d/" + RUNTIME_NAME + ".cab",
    "cab_sha256": "ec12b2db6423d127fb8e1935d34e2e68abc70fe8ecb1f6162ba1c1ccc2825f6d",
    "cab_bytes": 307996323,
    "exe_sha256": "30285e24a33be3c0b2d0a7c3909fa1cc21d2fc0f0c2ab4eb79e46a9304b4256d",
    "runtime_tree_sha256": "afcde57278f1cd84e8956d7ba591807d28c28362950b33b8a97806ec79efce7a",
    "relative_runtime_path": "runtimes/" + WRAPPER_NAME + "/" + RUNTIME_NAME,
}
MAX_FILES = 8192
MAX_BYTES = 2 * 1024**3
RESERVED = {"CON", "PRN", "AUX", "NUL", *("COM" + str(n) for n in range(1, 10)), *("LPT" + str(n) for n in range(1, 10))}


class BrowserPreparationError(RuntimeError):
    pass


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def linked(path):
    path = Path(path)
    if path.is_symlink():
        return True
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return bool(getattr(value, "st_file_attributes", 0) & 0x400) or (stat.S_ISREG(value.st_mode) and value.st_nlink > 1)


def safe_child(root, path):
    root, path = Path(root).absolute(), Path(path).absolute()
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise BrowserPreparationError("Output must stay in the workspace") from exc
    if any(part in {".", ".."} for part in parts):
        raise BrowserPreparationError("Workspace output must not contain traversal components")
    current = root
    for part in (None, *parts):
        if part is not None:
            current /= part
        if linked(current):
            raise BrowserPreparationError("Links/reparse points are forbidden: " + str(current))
    if path.resolve().is_relative_to(root.resolve()) is False:
        raise BrowserPreparationError("Resolved path leaves workspace")
    return path


def relative(value):
    if (type(value) is not str or not value or "\\" in value or ":" in value or "\0" in value or value.startswith("/")
            or any(part in {"", ".", ".."} or part.rstrip(" .") != part or part.split(".", 1)[0].upper() in RESERVED for part in value.split("/"))):
        raise BrowserPreparationError("Unsafe runtime-relative member: " + repr(value))
    return Path(*PurePosixPath(value).parts)


def inventory(root):
    root = Path(root)
    if linked(root) or not root.is_dir():
        raise BrowserPreparationError("Runtime directory is missing or linked")
    files, directories, identities, size = {}, set(), set(), 0
    for base, dirs, names in os.walk(root, followlinks=False):
        for name in (*dirs, *names):
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            relative(rel)
            if linked(path) or rel.casefold() in identities:
                raise BrowserPreparationError("Linked or case-conflicting runtime member: " + rel)
            identities.add(rel.casefold())
            mode = path.lstat().st_mode
            if stat.S_ISDIR(mode):
                directories.add(rel)
            elif stat.S_ISREG(mode):
                size += path.stat().st_size
                files[rel] = {"sha256": sha(path), "bytes": path.stat().st_size}
                if len(files) > MAX_FILES or size > MAX_BYTES:
                    raise BrowserPreparationError("Runtime tree exceeds pinned preparation bounds")
            else:
                raise BrowserPreparationError("Non-regular runtime member: " + rel)
    expected_dirs = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents if parent.as_posix() != "."}
    if directories != expected_dirs:
        raise BrowserPreparationError("Runtime tree contains an unexpected empty directory")
    return dict(sorted(files.items()))


def load_pin(root):
    path = safe_child(root, Path(root) / "metadata/webview2-runtime.lock.json")
    value = read_json(path)
    if type(value) is not dict or any(value.get(key) != expected for key, expected in PINNED.items()):
        raise BrowserPreparationError("Public runtime metadata differs from this tool's fixed pin")
    files = value.get("files")
    if type(files) is not dict or not files or len(files) > MAX_FILES:
        raise BrowserPreparationError("Missing complete bounded pinned runtime inventory")
    for name, record in files.items():
        relative(name)
        if (type(record) is not dict or set(record) != {"sha256", "bytes"} or type(record["bytes"]) is not int
                or not 0 <= record["bytes"] <= MAX_BYTES or not re.fullmatch(r"[0-9a-f]{64}", str(record["sha256"]))):
            raise BrowserPreparationError("Invalid pinned runtime inventory record")
    if (sum(record["bytes"] for record in files.values()) > MAX_BYTES
            or canonical_digest(files) != value["runtime_tree_sha256"]
            or files.get("msedgewebview2.exe", {}).get("sha256") != value["exe_sha256"]):
        raise BrowserPreparationError("Pinned complete runtime inventory digest differs")
    return value, sha(path)


def require_windows():
    if os.name != "nt":
        raise BrowserPreparationError("Fixed WebView2 preparation/Authenticode verification requires Windows")


def authenticode(exe):
    require_windows()
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    pwsh = shutil.which("pwsh")
    powershell = Path(pwsh) if pwsh else system / "WindowsPowerShell/v1.0/powershell.exe"
    if not powershell.is_file():
        raise BrowserPreparationError("Windows PowerShell is required for fresh Authenticode verification")
    literal = str(Path(exe).absolute()).replace("'", "''")
    script = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $s=Get-AuthenticodeSignature -LiteralPath '" + literal + "'; " + (
        "[pscustomobject]@{status=$s.Status.ToString();signer=$s.SignerCertificate.Subject;powershell_version=$PSVersionTable.PSVersion.ToString();"
        "signer_thumbprint=$s.SignerCertificate.Thumbprint;timestamp_signer=$s.TimeStamperCertificate.Subject} | ConvertTo-Json -Compress")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # A PowerShell 7 parent can export its own module paths. The built-in
    # Windows PowerShell must load its matching native Security module instead.
    child_env = {**os.environ, "PSModulePath": str(powershell.parent / "Modules")}
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, encoding="utf-8-sig", errors="strict", timeout=60, check=False, env=child_env)
    if result.returncode != 0:
        raise BrowserPreparationError("Fresh Windows Authenticode command failed: " + result.stderr[-1000:])
    signature = json.loads(result.stdout)
    if type(signature) is not dict or signature.get("status") != "Valid" or not re.search(r"(?:^|,\s*)O=Microsoft Corporation(?:,|$)", str(signature.get("signer", ""))):
        raise BrowserPreparationError("Fixed browser does not have a fresh Valid Microsoft Authenticode signature")
    signature["verifier_executable"] = str(powershell)
    return signature


def verify_tree(runtime, pin):
    files = inventory(runtime)
    if files != pin["files"]:
        raise BrowserPreparationError("Fixed browser complete tree differs from the official CAB pin")
    if sha(Path(runtime) / "msedgewebview2.exe") != pin["exe_sha256"]:
        raise BrowserPreparationError("Fixed browser executable SHA256 differs")
    signature = authenticode(Path(runtime) / "msedgewebview2.exe")
    if inventory(runtime) != files:
        raise BrowserPreparationError("Runtime changed during fresh signature verification")
    return files, signature


def verify_existing(root, pin, metadata_sha):
    wrapper = safe_child(root, Path(root) / "runtimes" / WRAPPER_NAME)
    runtime = safe_child(root, wrapper / RUNTIME_NAME)
    lock_path = safe_child(root, wrapper / "runtime-lock.json")
    if not wrapper.is_dir() or not lock_path.is_file():
        raise BrowserPreparationError("Pinned fixed browser is missing; use --prepare explicitly")
    locked = read_json(lock_path)
    if type(locked) is not dict:
        raise BrowserPreparationError("Existing runtime-lock must be a JSON object")
    for key in ("version", "architecture", "mode", "relative_runtime_path", "cab_sha256", "exe_sha256"):
        if locked.get(key) != pin[key]:
            raise BrowserPreparationError("Existing runtime-lock identity differs: " + key)
    allowed = {RUNTIME_NAME, "runtime-lock.json", "runtime.cab"}
    if {path.name for path in wrapper.iterdir()} - allowed:
        raise BrowserPreparationError("Existing fixed browser wrapper contains unexpected files")
    for path in wrapper.iterdir():
        safe_child(root, path)
    cab = wrapper / "runtime.cab"
    if cab.exists() and (not cab.is_file() or cab.stat().st_size != pin["cab_bytes"] or sha(cab) != pin["cab_sha256"]):
        raise BrowserPreparationError("Existing official CAB size/SHA256 differs")
    files, signature = verify_tree(runtime, pin)
    if "files" in locked and locked["files"] != files:
        raise BrowserPreparationError("Recorded complete runtime-lock inventory differs")
    return {"status": "verified", "runtime": str(runtime), "version": pin["version"], "files": files,
            "runtime_tree_sha256": canonical_digest(files), "metadata_sha256": metadata_sha,
            "authenticode": signature, "authenticode_checked_at_utc": datetime.now(timezone.utc).isoformat(),
            "fresh_signature_verified": True, "historical_signature_claim_used_as_proof": False,
            "downloaded": False, "runtime_modified": False, "evergreen_used": False}


def download_cab(pin, target):
    started, size, digest = time.monotonic(), 0, hashlib.sha256()
    request = urllib.request.Request(pin["source_url"], headers={"User-Agent": "RenPyBridge-pinned-WebView2-preparation"})
    with urllib.request.urlopen(request, timeout=60) as response, Path(target).open("xb") as output:
        if urllib.parse.urlsplit(response.geturl()).scheme != "https":
            raise BrowserPreparationError("Official CAB download redirected away from HTTPS")
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > pin["cab_bytes"] or time.monotonic() - started > 600:
                raise BrowserPreparationError("Official CAB download exceeded its fixed size/time bound")
            output.write(chunk)
            digest.update(chunk)
    if size != pin["cab_bytes"] or digest.hexdigest() != pin["cab_sha256"]:
        raise BrowserPreparationError("Downloaded official CAB size/SHA256 differs")


def extract_cab(cab, output):
    require_windows()
    expand = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/expand.exe"
    if not expand.is_file():
        raise BrowserPreparationError("Windows expand.exe is required")
    output.mkdir()
    result = subprocess.run([str(expand), str(cab), "-F:*", str(output)], capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=180, check=False)
    if result.returncode != 0:
        raise BrowserPreparationError("Pinned official CAB extraction failed: " + (result.stdout + result.stderr)[-1000:])


def publish_missing(source, destination):
    # Windows os.rename fails if the destination exists, including an empty
    # directory. Never use os.replace for an existing runtime publication.
    require_windows()
    if Path(destination).exists() or linked(destination):
        raise BrowserPreparationError("Refusing to replace an existing fixed browser")
    os.rename(source, destination)


def prepare(root, pin, metadata_sha):
    require_windows()
    root = Path(root).absolute()
    runtimes = safe_child(root, root / "runtimes")
    wrapper = safe_child(root, runtimes / WRAPPER_NAME)
    if wrapper.exists():
        return verify_existing(root, pin, metadata_sha)
    runtimes.mkdir(exist_ok=True)
    lock_path = safe_child(root, runtimes / ("." + WRAPPER_NAME + ".prepare.lock"))
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise BrowserPreparationError("Another preparation or a retained lock already exists") from exc
    token = str(os.getpid()) + ":" + uuid.uuid4().hex
    with os.fdopen(fd, "w", encoding="utf-8") as lock_stream:
        lock_stream.write(token)
    stage = safe_child(root, runtimes / ("." + WRAPPER_NAME + ".stage-" + uuid.uuid4().hex))
    try:
        stage.mkdir()
        if wrapper.exists() or linked(wrapper):
            raise BrowserPreparationError("Fixed browser appeared before preparation; refusing replacement")
        cab = stage / "runtime.cab"
        download_cab(pin, cab)
        # Hash again before invoking an external extraction process.
        if cab.stat().st_size != pin["cab_bytes"] or sha(cab) != pin["cab_sha256"]:
            raise BrowserPreparationError("Pinned CAB changed before extraction")
        extracted = stage / "extracted"
        extract_cab(cab, extracted)
        if {path.name for path in extracted.iterdir()} != {RUNTIME_NAME}:
            raise BrowserPreparationError("Pinned CAB has an unexpected extracted root layout")
        runtime = safe_child(root, extracted / RUNTIME_NAME)
        files, signature = verify_tree(runtime, pin)
        publish = stage / "publish"
        publish.mkdir()
        os.rename(runtime, publish / RUNTIME_NAME)
        os.rename(cab, publish / "runtime.cab")
        verified_at = datetime.now(timezone.utc).isoformat()
        lock = {key: pin[key] for key in ("version", "architecture", "mode", "source_url", "cab_sha256", "exe_sha256", "relative_runtime_path")}
        lock.update(official_page=pin["official_page"], signature_status=signature["status"], signer=signature["signer"],
                    authenticode=signature, authenticode_checked_at_utc=verified_at, files=files,
                    runtime_tree_sha256=canonical_digest(files), metadata_sha256=metadata_sha)
        write_json(publish / "runtime-lock.json", lock)
        if inventory(publish / RUNTIME_NAME) != files or sha(publish / "runtime.cab") != pin["cab_sha256"]:
            raise BrowserPreparationError("Staged publication changed before commit")
        safe_child(root, wrapper)
        publish_missing(publish, wrapper)
        return {"status": "prepared", "runtime": str(wrapper / RUNTIME_NAME), "version": pin["version"], "files": files,
                "runtime_tree_sha256": canonical_digest(files), "metadata_sha256": metadata_sha, "authenticode": signature,
                "authenticode_checked_at_utc": verified_at, "fresh_signature_verified": True,
                "historical_signature_claim_used_as_proof": False, "downloaded": True, "runtime_modified": False,
                "new_runtime_published": True, "evergreen_used": False, "staging_retained": str(stage)}
    except Exception as exc:
        try:
            write_json(stage / "failure.json", {"status": "failed", "error": str(exc), "existing_runtime_replaced": False})
        except OSError:
            pass
        raise BrowserPreparationError("Preparation failed; staging retained at " + str(stage) + ": " + str(exc)) from exc
    finally:
        # Remove only this tool's lock, never any runtime or staging directory.
        safe_child(root, lock_path)
        if lock_path.read_text(encoding="utf-8") == token:
            lock_path.unlink()


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="Explicitly download/verify the pinned CAB and publish only when absent")
    mode.add_argument("--verify-only", action="store_true", help="Freshly verify existing files/signature without downloading or modifying them (default)")
    args = parser.parse_args(argv)
    try:
        require_windows()
        pin, metadata_sha = load_pin(ROOT)
        report = prepare(ROOT, pin, metadata_sha) if args.prepare else verify_existing(ROOT, pin, metadata_sha)
    except (BrowserPreparationError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc), "evergreen_used": False}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
