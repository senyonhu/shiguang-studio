"""Create immutable v1.0.1 examples from the preserved formal v1.0.0 packages.

This fixes dynamic audio settings without changing historical package bytes.
Manifest validity is not a browser, window, or release certification.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import uuid

from build_prototypes import package_digest, sha256

ROOT = Path(__file__).resolve().parents[1] / "production"
VERSION = "1.0.1"
TARGET = ROOT / ("v" + VERSION)
NAMES = ("puzzle", "timed", "checkpoint")


def upgrade_sdk(source: str) -> str:
    source = source.replace('options.packageVersion || "1.0.0"', 'options.packageVersion || "1.0.1"')
    needle = "    function receive(raw) {"
    handler = '''    function applySettings(settings) {
      if (!settings || typeof settings !== "object" || Array.isArray(settings)) return;
      const value = settings.audio_volume;
      if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) return;
      audioVolume = value;
      emit("settings", { audio_volume: audioVolume });
    }

'''
    if source.count(needle) != 1:
        raise ValueError("Preserved SDK receive function changed")
    source = source.replace(needle, handler + needle)
    old = '''        audioVolume = Math.max(0, Math.min(1, Number(payload.settings && payload.settings.audio_volume) || 0));
        if (!payload.settings || payload.settings.audio_volume === undefined) audioVolume = 1;'''
    if old not in source:
        raise ValueError("Preserved SDK initialization changed")
    source = source.replace(old, "        applySettings(payload.settings);")
    source = source.replace('      if (message.type === "ack") {',
        '''      if (message.type === "settings") {
        applySettings(message.payload);
        return;
      }
      if (message.type === "ack") {''')
    source = source.replace('      } else if (message.type === "resume" && phase === "paused") {\n        phase = "running";',
        '''      } else if (message.type === "resume" && phase === "paused") {
        applySettings(message.payload && message.payload.settings);
        phase = "running";''')
    return source


def build() -> dict:
    if TARGET.exists():
        # Never rewrite a published version. Check it before returning its inventory.
        digests = json.loads((TARGET / "package-digests.json").read_text(encoding="utf-8"))
        for name in NAMES:
            if package_digest(TARGET / name) != digests["packages"][name]["package_hash"]:
                raise ValueError("Existing v1.0.1 package differs from its frozen inventory")
        return digests
    stage = ROOT / (".upgrade-" + uuid.uuid4().hex)
    stage.mkdir()
    sdk = upgrade_sdk((ROOT / "sdk/bridge-sdk.js").read_text(encoding="utf-8"))
    (stage / "sdk").mkdir()
    (stage / "sdk/bridge-sdk.js").write_text(sdk, encoding="utf-8", newline="\n")
    digests = {"prototype": False, "protocol": "1.0", "package_version": VERSION,
               "status": "formal_manifest_examples_not_release_certification",
               "digest_algorithm": "sha256(path_utf8+nul+file_sha256_ascii+lf)-v1", "packages": {}}
    for name in NAMES:
        directory = stage / name
        shutil.copytree(ROOT / name, directory)
        (directory / "bridge-sdk.js").write_text(sdk, encoding="utf-8", newline="\n")
        game_path = directory / "game.js"
        game = game_path.read_text(encoding="utf-8").replace('packageVersion: "1.0.0"', 'packageVersion: "1.0.1"')
        if name == "timed":
            game = game.replace('  bridge.on("init", payload => {',
                                '  bridge.on("settings", settings => { tone.volume = settings.audio_volume; })\n    .on("init", payload => {')
        game_path.write_text(game, encoding="utf-8", newline="\n")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        manifest["version"] = VERSION
        manifest["files"] = [{"path": path.relative_to(directory).as_posix(),
                              "sha256": sha256(path), "bytes": path.stat().st_size}
            for path in sorted(directory.rglob("*"), key=lambda path: path.relative_to(directory).as_posix().encode("utf-8"))
            if path.is_file() and path.name != "manifest.json"]
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        digests["packages"][name] = {"game_id": manifest["game_id"], "package_version": VERSION,
            "package_hash": package_digest(directory), "manifest_sha256": sha256(directory / "manifest.json"),
            "file_count": len(manifest["files"]) + 1}
    (stage / "package-digests.json").write_text(json.dumps(digests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    stage.rename(TARGET)
    return digests


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
