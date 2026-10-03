"""Prepare an allowlisted public source snapshot; never install or publish it."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid
import zipfile

ROOT = Path(__file__).absolute().parents[1]
TOP_FILES = {
    ".gitignore", ".gitattributes", "README.md", "START_HERE.md",
    "pyproject.toml", "requirements.lock",
}
SOURCE_TREES = {
    "src", "tests", "scripts", "tools", "plugin", "examples/html_minigames", "native",
}
PUBLIC_DOCS = {
    "TOOLS.md", "CONTRACT.md", "USAGE.md", "tools.schema.json",
    "project.schema.json", "tool-examples.json", "PUBLIC_RELEASE.md",
    "VALIDATION.md", "MIGRATION_FROM_STUDIO.md",
}
PUBLIC_METADATA = {"webview2-runtime.lock.json"}
EXCLUDED = {
    ".git", ".venv", ".runtime", ".codex", "bin", "obj", "__pycache__",
    "node_modules", "evidence", "outputs", "projects", "jobs", "runtimes",
    "prototypes", "upstream", "profile", "profiles", "save", "saves",
}
TEXT_SUFFIXES = {
    ".py", ".ps1", ".sh", ".mjs", ".cjs", ".js", ".html", ".css", ".json",
    ".yaml", ".yml", ".toml", ".md", ".txt", ".xml", ".manifest", ".cs",
    ".csproj", ".sln", ".props", ".targets", ".rst",
}
ASSET_SUFFIXES = {".svg", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".ogg", ".mp3", ".ico"}
SPECIAL_NAMES = {".gitignore", ".gitattributes", "LICENSE", "NOTICE", "COPYING"}
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
METADATA_NAME = "PUBLIC_SOURCE_MANIFEST.json"


class PreparationError(RuntimeError):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def unlinked(path: Path) -> Path:
    absolute = Path(os.path.abspath(path))
    for ancestor in (absolute, *absolute.parents):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PreparationError("Links/junctions are not allowed: " + str(ancestor))
    return absolute


def excluded(name: str) -> bool:
    value = name.casefold()
    return (value in EXCLUDED or value.startswith(".env")
            or value.endswith((".egg-info", ".dist-info")))


def safe_relative(relative: str) -> None:
    path = PurePosixPath(relative)
    if path.is_absolute() or not path.parts or "\\" in relative:
        raise PreparationError("Invalid source-relative path: " + relative)
    for part in path.parts:
        if (part in {".", ".."} or ":" in part or "\x00" in part
                or part.rstrip(" .") != part
                or part.split(".", 1)[0].upper() in WINDOWS_RESERVED):
            raise PreparationError("Unsafe Windows source path: " + relative)


def read_regular(path: Path) -> bytes:
    unlinked(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise PreparationError("Source must be a regular file: " + str(path))
    if before.st_nlink != 1:
        raise PreparationError("Hard-linked source files are not allowed: " + str(path))
    if before.st_size > MAX_FILE_BYTES:
        raise PreparationError("Public source file exceeds 5 MiB: " + str(path))
    data = path.read_bytes()
    after = path.lstat()
    identity = lambda value: (value.st_size, value.st_mtime_ns, value.st_ctime_ns, value.st_ino)
    if identity(before) != identity(after) or len(data) != before.st_size:
        raise PreparationError("Source changed while reading: " + str(path))
    unlinked(path)
    return data


def collect(source: Path) -> dict[str, bytes]:
    source = unlinked(source)
    if not source.is_dir():
        raise PreparationError("Source root is missing")
    values = {}
    seen = set()
    root_allowed = {name.casefold() for name in TOP_FILES | {tree.split("/")[0] for tree in SOURCE_TREES} | {"docs", "metadata"}}
    root_names = [path.name.casefold() for path in source.iterdir() if path.name.casefold() in root_allowed]
    if len(root_names) != len(set(root_names)):
        raise PreparationError("Source root has Windows case-colliding names")

    def add(path):
        relative = path.relative_to(source).as_posix()
        safe_relative(relative)
        identity = relative.casefold()
        if identity in seen:
            raise PreparationError("Public source has Windows case-colliding paths: " + relative)
        seen.add(identity)
        if (relative not in TOP_FILES and path.suffix.casefold() not in TEXT_SUFFIXES | ASSET_SUFFIXES
                and path.name not in SPECIAL_NAMES):
            raise PreparationError("Unsupported public source file: " + relative)
        values[relative] = read_regular(path)

    for relative in sorted(TOP_FILES):
        path = source / relative
        if not path.is_file():
            raise PreparationError("Required top-level public source file is missing: " + relative)
        add(path)
    for name in sorted(PUBLIC_DOCS):
        path = source / "docs" / name
        if not path.is_file():
            raise PreparationError("Required public document is missing: docs/" + name)
        add(path)
    for name in sorted(PUBLIC_METADATA):
        path = source / "metadata" / name
        if not path.is_file():
            raise PreparationError("Required public metadata is missing: metadata/" + name)
        add(path)
    for tree in sorted(SOURCE_TREES):
        root = unlinked(source / tree)
        if not root.is_dir():
            raise PreparationError("Required public source tree is missing: " + tree)
        for directory, dirs, files in os.walk(root, followlinks=False):
            current = Path(directory)
            kept = []
            siblings = set()
            for name in sorted((*dirs, *files)):
                if excluded(name):
                    continue
                identity = name.casefold()
                if identity in siblings:
                    raise PreparationError("Public source has Windows case-colliding paths: " + str(current / name))
                siblings.add(identity)
                path = unlinked(current / name)
                safe_relative(path.relative_to(source).as_posix())
                if name in dirs:
                    if not path.is_dir():
                        raise PreparationError("Source directory is not regular: " + str(path))
                    kept.append(name)
                else:
                    add(path)
            dirs[:] = kept
    if sum(map(len, values.values())) > MAX_TOTAL_BYTES:
        raise PreparationError("Public source exceeds 50 MiB")
    return dict(sorted(values.items()))


def entropy(value: str) -> float:
    return -sum((value.count(char)/len(value))*math.log2(value.count(char)/len(value)) for char in set(value))


def placeholder(value: str) -> bool:
    return bool(re.search(r"(?:^|[_-])(?:test|example|placeholder|dummy|fake|redacted)(?:[_-]|$)", value, re.I)
                or value.upper().endswith("EXAMPLE") or entropy(value) < 3.0)


CREDENTIALS = {
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})\b"),
    "openai_key": re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,255}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\s+([A-Za-z0-9+/=\s]{64,})-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
PROFILE = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"'<>]+)", re.I)
MARKDOWN_LINK = re.compile(r"\]\((?:<([^>]+)>|([^\s()]+)(?:\s+\"[^\"]*\")?)\)")


def privacy_check(values: dict[str, bytes]) -> dict:
    findings = []
    for relative, data in values.items():
        text = data.decode("utf-8", errors="ignore")
        for rule, pattern in CREDENTIALS.items():
            for match in pattern.finditer(text):
                value = match.group(1).replace("\n", "").replace("\r", "").replace(" ", "") if rule == "private_key" else match[0]
                if placeholder(value):
                    continue
                findings.append({"path": relative, "rule": rule, "line": text.count("\n", 0, match.start())+1,
                                 "value_sha256": digest(match[0].encode())})
        for match in PROFILE.finditer(text):
            if re.match(r"^(?:public|default|example(?:user)?|fixture(?:user)?|test(?:user)?|your(?:user(?:name)?)?|user(?:name)?)(?:[-_0-9 ].*)?$", match[1], re.I):
                continue
            findings.append({"path": relative, "rule": "personal_windows_profile_path",
                             "line": text.count("\n", 0, match.start())+1})
        if relative.lower().endswith(".md"):
            for match in MARKDOWN_LINK.finditer(text):
                target = (match[1] or match[2]).split("#", 1)[0]
                if not target or re.match(r"^(?:https?://|mailto:)", target, re.I):
                    continue
                if re.match(r"^(?:[A-Za-z]:|/|file:|codex:)", target, re.I):
                    findings.append({"path": relative, "rule": "local_absolute_markdown_link",
                                     "line": text.count("\n", 0, match.start())+1})
                    continue
                joined = os.path.normpath(str(PurePosixPath(relative).parent / target)).replace("\\", "/")
                if joined not in values:
                    findings.append({"path": relative, "rule": "markdown_link_outside_public_source",
                                     "line": text.count("\n", 0, match.start())+1, "target": target})
    return {"passed": not findings, "files_scanned": len(values), "findings": findings,
            "rules": [*CREDENTIALS, "personal_windows_profile_path", "public_markdown_links"],
            "scope": "High-confidence credential patterns, profile paths and local Markdown links; no network access."}


def manifest(values: dict[str, bytes]) -> dict:
    return {path: {"bytes": len(data), "sha256": digest(data)} for path,data in values.items()}


def prepare(source: Path, output: Path) -> dict:
    source, output = unlinked(source), unlinked(output)
    public_outputs = source / "outputs/public-source"
    if output == public_outputs or not output.is_relative_to(public_outputs):
        raise PreparationError("Output must be a new directory beneath workspace/outputs/public-source")
    if any(output.is_relative_to(source/tree) for tree in SOURCE_TREES | {"docs", "metadata"}):
        raise PreparationError("Output cannot be inside a public source tree")
    if output.exists():
        raise PreparationError("Output already exists; no directory is overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    unlinked(output.parent)
    output.mkdir(exist_ok=False)
    report = {"status": "preparing", "kind": "controlled_public_source", "output": str(output),
              "network_accessed": False, "installed": False, "committed": False, "pushed": False,
              "project_license_inferred": False}
    write_json(output / "report.json", report)
    try:
        values = collect(source)
        before = manifest(values)
        privacy = privacy_check(values)
        write_json(output / "privacy-report.json", privacy)
        if not privacy["passed"]:
            raise PreparationError("Privacy validation failed; no public ZIP was created")
        destination = output / "source-root"
        destination.mkdir()
        for relative, data in values.items():
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
        if manifest(collect(source)) != before:
            raise PreparationError("Source changed while copying; no public ZIP was created")
        copied = {path.relative_to(destination).as_posix(): read_regular(path)
                  for path in destination.rglob("*") if path.is_file()}
        if manifest(copied) != before or not privacy_check(copied)["passed"]:
            raise PreparationError("Copied source does not match the reviewed source")
        metadata = {"schema_version": "1.0", "kind": "controlled_public_source", "files": before,
            "source_content_sha256": digest(json.dumps(before, sort_keys=True, separators=(",", ":")).encode()),
            "privacy_checked": True, "sdk_and_production_evidence_included": False,
            "project_license": None, "license_note": "No project license is inferred; retain and review existing third-party notices.",
            "limits": {"file_bytes": MAX_FILE_BYTES, "total_bytes": MAX_TOTAL_BYTES},
            "excluded": sorted(EXCLUDED | {".env*", "*.egg-info", "*.dist-info"})}
        write_json(destination / METADATA_NAME, metadata)
        write_json(output / "manifest.json", metadata)
        public_zip = output / "public-source.zip"
        with zipfile.ZipFile(public_zip, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(destination.rglob("*")):
                if path.is_file():
                    name = "source-root/" + path.relative_to(destination).as_posix()
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    archive.writestr(info, read_regular(path))
        with zipfile.ZipFile(public_zip) as archive:
            zipped = {name.removeprefix("source-root/"): archive.read(name) for name in archive.namelist()}
        expected_zip = dict(copied)
        expected_zip[METADATA_NAME] = read_regular(destination / METADATA_NAME)
        if zipped != expected_zip:
            raise PreparationError("ZIP bytes differ from the prepared source")
        if manifest(collect(source)) != before:
            raise PreparationError("Source changed while preparing ZIP")
        report.update(status="prepared", source_root=str(destination), files=len(before),
            bytes=sum(item["bytes"] for item in before.values()), source_manifest_sha256=digest(read_regular(output / "manifest.json")),
            source_content_sha256=metadata["source_content_sha256"], public_zip=str(public_zip),
            public_zip_sha256=digest(public_zip.read_bytes()), privacy_report_sha256=digest(read_regular(output / "privacy-report.json")),
            source_copy_and_zip_verified=True)
    except Exception as error:
        report.update(status="failed", error={"type": type(error).__name__, "message": str(error)})
        public_zip = output / "public-source.zip"
        if public_zip.is_file():
            os.replace(public_zip, output / "failed-unverified-source.zip")
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(output / "report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New output directory within this workspace")
    args = parser.parse_args(argv)
    output = args.output or ROOT / "outputs/public-source" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    try:
        result = prepare(ROOT, output)
    except PreparationError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "prepared" else 1


if __name__ == "__main__":
    sys.exit(main())
