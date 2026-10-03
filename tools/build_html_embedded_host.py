"""Build the isolated Windows HTML host with workspace-local, pinned dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = ROOT / "runtimes" / "html-host-dependencies"
SDK_VERSION = "10.0.401"
SDK_URL = f"https://builds.dotnet.microsoft.com/dotnet/Sdk/{SDK_VERSION}/dotnet-sdk-{SDK_VERSION}-win-x64.zip"
SDK_SHA512 = "24b670ad3d923bfcf47df6c3b034152398b42f6dbc388e10d783aee1cfb5e5817d399fc0ae2a12cfa822a55e61d34830ccb15c50ef6efee437ab874bb7c79430"
WEBVIEW_VERSION = "1.0.4258.31"
WEBVIEW_URL = f"https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/{WEBVIEW_VERSION}/microsoft.web.webview2.{WEBVIEW_VERSION}.nupkg"
WEBVIEW_SHA256 = "56f7f4b8bf9aee4b8efefbbdd4f67d5f74ebd1b100ed0806da71bf76af481aa9"
RUNTIME_VERSION = "10.0.12"
NOTICE_ARCHIVES = {
    "microsoft.netcore.app.runtime.win-x64": {
        "version": RUNTIME_VERSION,
        "sha256": "0f63dee7ca4383cb16848966f81e439399e2058e1671ebe27e5eaf4b200e8691",
        "members": ["LICENSE.TXT", "THIRD-PARTY-NOTICES.TXT"],
    },
    "microsoft.windowsdesktop.app.runtime.win-x64": {
        "version": RUNTIME_VERSION,
        "sha256": "68bce56d2402969d82ac77fbd2b2a54f55fda86ea060366e6ddb832353dab80a",
        "members": ["LICENSE"],
    },
    "microsoft.web.webview2": {
        "version": WEBVIEW_VERSION, "sha256": WEBVIEW_SHA256,
        "members": ["LICENSE.txt", "NOTICE.txt"],
    },
}


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, algorithm).hexdigest()


def download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=90) as response, path.open("wb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)


def copy_runtime_notices(output: Path, packages: Path, *, include_dotnet: bool) -> list[dict]:
    """Redistribute original notices from the exact pinned restored archives."""
    notices = []
    for name, record in NOTICE_ARCHIVES.items():
        if not include_dotnet and name != "microsoft.web.webview2":
            continue
        archive = packages / name / record["version"] / (name + "." + record["version"] + ".nupkg")
        if not archive.is_file() or digest(archive) != record["sha256"]:
            raise RuntimeError("Pinned runtime notice archive differs: " + name)
        with zipfile.ZipFile(archive) as bundle:
            for member in record["members"]:
                entries = [item for item in bundle.infolist() if item.filename == member]
                if len(entries) != 1 or entries[0].is_dir() or entries[0].file_size > 5 * 1024 * 1024:
                    raise RuntimeError("Runtime license member is missing or unsafe: " + member)
                content = bundle.read(entries[0])
                if not content:
                    raise RuntimeError("Runtime license member is empty: " + member)
                relative = Path("third-party-notices") / name / member
                target = output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                notices.append({"package": name, "version": record["version"],
                                "archive_sha256": record["sha256"], "archive_member": member,
                                "path": relative.as_posix(), "sha256": digest(target), "bytes": len(content)})
    return notices


def prepare_sdk() -> Path:
    sdk_root = DEPENDENCIES / f"dotnet-sdk-{SDK_VERSION}"
    sdk_zip = DEPENDENCIES / f"dotnet-sdk-{SDK_VERSION}-win-x64.zip"
    if not sdk_zip.exists():
        download(SDK_URL, sdk_zip)
    if digest(sdk_zip, "sha512") != SDK_SHA512:
        raise RuntimeError("Official .NET SDK SHA512 mismatch")
    if not (sdk_root / "sdk" / SDK_VERSION / "dotnet.dll").is_file():
        sdk_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(sdk_zip) as archive:
            for member in archive.infolist():
                target = (sdk_root / member.filename).resolve()
                if not target.is_relative_to(sdk_root.resolve()):
                    raise RuntimeError("SDK archive path escapes workspace dependency root")
            archive.extractall(sdk_root)
    return sdk_root


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true", help="Download pinned official dependencies if absent")
    parser.add_argument("--output", type=Path, default=ROOT / "runtimes" / "html-minigame-embedded-host" / "win-x64")
    parser.add_argument("--framework-dependent", action="store_true", help="Diagnostic only; final delivery uses self-contained")
    args = parser.parse_args()
    sdk_root = DEPENDENCIES / f"dotnet-sdk-{SDK_VERSION}"
    if args.prepare:
        sdk_root = prepare_sdk()
    if not (sdk_root / "dotnet.exe").is_file():
        raise RuntimeError("Pinned SDK is missing; run with --prepare")
    package = DEPENDENCIES / f"microsoft.web.webview2.{WEBVIEW_VERSION}.nupkg"
    if not package.is_file():
        if not args.prepare:
            raise RuntimeError("Pinned WebView2 NuGet package is missing; run with --prepare")
        download(WEBVIEW_URL, package)
    if digest(package) != WEBVIEW_SHA256:
        raise RuntimeError("Pinned official WebView2 NuGet SHA256 mismatch")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT.resolve()):
        raise RuntimeError("Build output must be inside workspace")
    env = os.environ.copy()
    env.update({
        "DOTNET_ROOT": str(sdk_root), "DOTNET_CLI_HOME": str(DEPENDENCIES / "dotnet-cli"),
        "NUGET_PACKAGES": str(DEPENDENCIES / "nuget"), "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_NOLOGO": "1",
    })
    source_root = ROOT / "native" / "html_minigame_embedded_host"
    source_files = {path.relative_to(source_root).as_posix(): {"bytes": path.stat().st_size, "sha256": digest(path)}
                    for path in sorted(source_root.rglob("*"))
                    if path.is_file() and not ({"bin", "obj"} & set(path.relative_to(source_root).parts))
                    and (path.suffix in {".cs", ".csproj"} or path.name in {"packages.lock.json", "app.manifest"})}
    source_record = {"root": source_root.relative_to(ROOT).as_posix(), "files": source_files,
                     "tree_sha256": hashlib.sha256(json.dumps(source_files, sort_keys=True, ensure_ascii=False,
                                                               separators=(",", ":")).encode("utf-8")).hexdigest()}
    command = [str(sdk_root / "dotnet.exe"), "publish", str(source_root / "HtmlMinigameEmbeddedHost.csproj"),
               "-c", "Release", "-r", "win-x64", "--self-contained", str(not args.framework_dependent).lower(),
               "-o", str(output)]
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    restored_package = DEPENDENCIES / "nuget" / "microsoft.web.webview2" / WEBVIEW_VERSION / package.name
    if result.returncode == 0 and (not restored_package.is_file() or digest(restored_package) != WEBVIEW_SHA256):
        raise RuntimeError("Restored WebView2 bytes differ from pinned official package")
    notices = copy_runtime_notices(output, DEPENDENCIES / "nuget", include_dotnet=not args.framework_dependent) if result.returncode == 0 else []
    report = {
        "sdk": {"version": SDK_VERSION, "url": SDK_URL, "sha512": SDK_SHA512,
                "sha256": digest(DEPENDENCIES / f"dotnet-sdk-{SDK_VERSION}-win-x64.zip")},
        "webview2_sdk": {"version": WEBVIEW_VERSION, "url": WEBVIEW_URL, "sha256": digest(package)},
        "native_source": source_record,
        "third_party_notices": notices,
        "build": {"exit_code": result.returncode, "self_contained": not args.framework_dependent,
                  "output": str(output), "command": command},
    }
    if result.returncode == 0:
        report["build"]["files"] = [{"path": path.relative_to(output).as_posix(), "bytes": path.stat().st_size,
                                     "sha256": digest(path)} for path in sorted(output.rglob("*"))
                                   if path.is_file() and path.name != "build-report.json"]
    output.mkdir(parents=True, exist_ok=True)
    (output / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"exit_code": result.returncode, "output": str(output),
                      "self_contained": not args.framework_dependent, "report": str(output / "build-report.json")}, ensure_ascii=False))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
