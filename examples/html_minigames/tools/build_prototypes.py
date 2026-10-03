"""Materialize self-contained H0 packages and their independently computed hashes."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import wave

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_digest(directory: Path) -> str:
    hasher = hashlib.sha256()
    paths = sorted((path for path in directory.rglob("*") if path.is_file()),
                   key=lambda path: path.relative_to(directory).as_posix().encode("utf-8"))
    for path in paths:
        relative = path.relative_to(directory).as_posix()
        hasher.update(relative.encode("utf-8") + b"\0" + sha256(path).encode("ascii") + b"\n")
    return hasher.hexdigest()


def tone(path: Path) -> None:
    rate = 22050
    count = int(rate * 0.12)
    samples = bytearray()
    for index in range(count):
        envelope = min(1.0, index / 180, (count - index) / 300)
        value = int(7000 * envelope * math.sin(2 * math.pi * 660 * index / rate))
        samples.extend(struct.pack("<h", value))
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples)


DEFINITIONS = {
    "puzzle": {
        "display_name": "星图锁 · 九宫格拼图",
        "inputs": {
            "seed": {"type": "integer", "default": 1742, "minimum": 0, "maximum": 4294967295},
            "scramble_moves": {"type": "integer", "default": 12, "minimum": 2, "maximum": 80},
        },
        "results": {"outcomes": ["win"], "fields": {
            "score": {"type": "integer", "minimum": 0, "maximum": 1000},
            "moves": {"type": "integer", "minimum": 1},
            "elapsed_ms": {"type": "integer", "minimum": 0},
        }},
        "save_capabilities": {"entry_restart": True, "checkpoint": False, "snapshot_version": None},
        "automation": {"board": "[data-testid=board]", "actual_actions": "点击与空格相邻的 tile-N，直到排序正确"},
    },
    "timed": {
        "display_name": "信号回路 · 限时操作",
        "inputs": {
            "duration_seconds": {"type": "number", "default": 12, "minimum": 3, "maximum": 60},
            "rounds": {"type": "integer", "default": 6, "minimum": 3, "maximum": 24},
        },
        "results": {"outcomes": ["win", "lose"], "fields": {
            "score": {"type": "integer", "minimum": 0},
            "hits": {"type": "integer", "minimum": 0, "maximum": 24},
            "misses": {"type": "integer", "minimum": 0},
            "elapsed_ms": {"type": "integer", "minimum": 0},
        }},
        "save_capabilities": {"entry_restart": True, "checkpoint": False, "snapshot_version": None},
        "automation": {"begin": "[data-testid=begin]", "lit_target": ".target.lit", "actual_actions": "开始后逐次按发亮端子胜利；开始后不操作到计时归零失败"},
    },
    "checkpoint": {
        "display_name": "温室修复 · 检查点任务",
        "inputs": {"target_temperature": {"type": "integer", "default": 37, "minimum": 20, "maximum": 50}},
        "results": {"outcomes": ["win"], "fields": {
            "score": {"type": "integer", "minimum": 0, "maximum": 300},
            "steps_completed": {"type": "integer", "enum": [3]},
            "mistakes": {"type": "integer", "minimum": 0},
            "calibrated_temperature": {"type": "integer", "minimum": 20, "maximum": 50},
        }},
        "save_capabilities": {"entry_restart": True, "checkpoint": True, "snapshot_version": "1.0"},
        "snapshot_schema": {"type": "object", "required": ["stage", "sensor", "wire", "calibration", "mistakes"], "properties": {
            "stage": {"type": "integer", "minimum": 0, "maximum": 2},
            "sensor": {"type": ["string", "null"], "enum": [None, "temperature"]},
            "wire": {"type": ["string", "null"], "enum": [None, "blue"]},
            "calibration": {"type": "integer", "minimum": 0, "maximum": 99},
            "mistakes": {"type": "integer", "minimum": 0, "maximum": 100000},
        }, "additionalProperties": False},
        "automation": {"stage": "[data-testid=workshop]", "actual_actions": "温度探头→蓝色端子→调到目标温度→实测；前两步真实提交检查点"},
    },
}


def main() -> None:
    digests = {"prototype": True, "protocol": "1.0", "digest_algorithm": "sha256(path_utf8+nul+file_sha256_ascii+lf)-v1", "packages": {}}
    for name, definition in DEFINITIONS.items():
        directory = ROOT / name
        shutil.copyfile(ROOT / "sdk/bridge-sdk.js", directory / "bridge-sdk.js")
        shutil.copyfile(ROOT / "sdk/common.css", directory / "styles.css")
        if name == "timed":
            tone(directory / "tap.wav")
        manifest = {"format_version": "1.0", "prototype": True, "game_id": "h0_" + name,
                    "version": "h0-1", "entry": "index.html", "protocol": "1.0", **definition,
                    "required_capabilities": ["local_content", "host_messages"] + (["audio"] if name == "timed" else []),
                    "license": "Original example code and generated tone; no external dependency", "files": []}
        for path in sorted(directory.rglob("*"), key=lambda item: item.relative_to(directory).as_posix().encode("utf-8")):
            if path.is_file() and path.name != "manifest.json":
                manifest["files"].append({"path": path.relative_to(directory).as_posix(), "sha256": sha256(path), "bytes": path.stat().st_size})
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        digests["packages"][name] = {"game_id": manifest["game_id"], "package_version": manifest["version"],
                                     "package_hash": package_digest(directory), "manifest_sha256": sha256(directory / "manifest.json"),
                                     "file_count": len(manifest["files"]) + 1}
    (ROOT / "prototype-digests.json").write_text(json.dumps(digests, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(digests, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
