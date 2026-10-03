"""Dry preparation tests; no process, speaker, recorder, or real voice playback."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import wave

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("demo_audio_probe_test_module", TOOLS / "probe_demo_audio.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wav(path, index):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes((1000 + index * 100).to_bytes(2, "little") * 4000)


@pytest.fixture
def inputs(tmp_path):
    project = tmp_path / "original-generated"
    game = project / "game"
    assets = game / "bridge_assets"
    assets.mkdir(parents=True)
    own = tmp_path / "owned-synthetic-unit-fixtures"
    own.mkdir()
    voices, phases, manifest_assets, frame_map, lines = [], [], {}, {}, []
    for index, role in enumerate(["voice", "voice", "voice", "music", "sfx"]):
        source = own / (str(index) + ".wav")
        wav(source, index)
        hashed = sha(source)
        path = "bridge_assets/" + hashed + ".wav"
        (game / path).write_bytes(source.read_bytes())
        record = {"path": str(source), "sha256": hashed, "duration_seconds": .25,
                  "text": "中文单元夹具" + str(index), "method": "installed SAPI Chinese TTS",
                  "generation": {"status": "generated", "selected_voice": {"culture": "zh-CN", "name": "unit fixture"}}}
        if role != "voice":
            record["method"] = "original synthetic unit fixture"
        frame = "frame_" + str(index)
        frame_map[frame] = {"file": "game/script.rpy", "label": "rb_frame_" + str(index), "start_line": len(lines) + 1}
        command = {"voice": "voice ", "music": "play music ", "sfx": "play rb_sfx_0 "}[role] + json.dumps(path)
        if role == "music":
            command += " if_changed"
        lines += ["label rb_frame_" + str(index) + ":", "    " + command, "    \"dialogue\""]
        frame_map[frame]["end_line"] = len(lines)
        manifest_assets["asset_" + str(index)] = {"sha256": hashed, "game_path": path, "version": 1}
        phases.append(frame + "=" + str(source))
        if role == "voice":
            voices.append(record)
        elif role == "music":
            music = record
        else:
            sfx = record
    common = {"project_id": "project_unit", "revision": 7, "sdk_version": "8.5.3.26051504"}
    (project / "bridge-manifest.json").write_text(json.dumps({**common, "assets": manifest_assets}), encoding="utf-8")
    (project / "bridge-source-map.json").write_text(json.dumps({**common, "frames": frame_map}), encoding="utf-8")
    (game / "script.rpy").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (game / "script.rpyc").write_bytes(b"unit fixture identity; never executable")
    (game / "bridge_testcases.rpy").write_text("old QA must not be cloned", encoding="utf-8")
    (game / "saves").mkdir()
    (game / "saves/player.save").write_bytes(b"private player sentinel")
    report = tmp_path / "audio-generation.json"
    report.write_text(json.dumps({"voices": voices, "music": music, "sfx": sfx}), encoding="utf-8")
    package = tmp_path / "frozen-package"
    (package / "game/bridge_assets").mkdir(parents=True)
    for path in assets.iterdir():
        (package / "game/bridge_assets" / path.name).write_bytes(path.read_bytes())
    (package / "game/script.rpyc").write_bytes((game / "script.rpyc").read_bytes())
    exe = package / "game-unit.exe"
    exe.write_bytes(b"MZ unit fixture; must never execute")
    parser = argparse.ArgumentParser()
    probe.add_arguments(parser)
    args = parser.parse_args(["--project", str(project), "--executable", str(exe),
                              "--voice", phases[0], "--voice", phases[1], "--voice", phases[2],
                              "--bgm", phases[3], "--sfx", phases[4], "--provenance-report", str(report), "--prepare-only"])
    return args, tmp_path


def test_prepare_clones_both_targets_and_preserves_every_original_file(inputs):
    args, directory = inputs
    original_project = probe.tree_snapshot(args.project)
    original_package = probe.tree_snapshot(args.executable.parent)
    run = directory / "qa-run"
    run.mkdir()
    report = probe.prepare(args, run)
    assert report["prepared"] and report["source_inputs_unchanged"]
    assert not report["complete"] and not report["actual_audio_output_verified"]
    assert len(report["phases"]) == 5 and len(report["targets"]) == 2
    assert probe.tree_snapshot(args.project) == original_project
    assert probe.tree_snapshot(args.executable.parent) == original_package
    for target in report["targets"]:
        game = Path(target["qa_directory"]) / "game"
        assert (game / "bridge_demo_audio_qa.rpy").is_file()
        assert not (game / "saves").exists()
        assert not (game / "bridge_testcases.rpy").exists()
        target_report = json.loads((Path(target["run_directory"]) / "report.json").read_text(encoding="utf-8"))
        assert not target_report["complete"]
    assert report["targets"][1]["story_identity_files"]["script.rpyc"] == sha(args.project / "game/script.rpyc")


def test_prepare_only_never_launches_an_engine_or_capture(inputs, monkeypatch):
    args, directory = inputs
    monkeypatch.setattr(probe, "EVIDENCE", directory / "evidence")
    def forbidden(*a, **k):
        raise AssertionError("Preparation must not launch an engine or recorder")
    monkeypatch.setattr(probe, "_execute", forbidden)
    monkeypatch.setattr(probe, "run_sdk", forbidden)
    monkeypatch.setattr(probe, "discover_sdk", forbidden)
    monkeypatch.setattr(probe, "capture", forbidden)
    assert probe.run(args) == 0
    report = json.loads(next((directory / "evidence/runs").glob("*/report.json")).read_text(encoding="utf-8"))
    assert report["playback_status"] == "not_run_prepare_only" and not report["complete"]


@pytest.mark.parametrize("damage", ["package-asset", "package-story", "unapproved-audio", "provenance", "native-statement", "revision", "capture-window"])
def test_damaged_or_unbound_inputs_are_rejected_before_execution(inputs, damage):
    args, directory = inputs
    if damage == "package-asset":
        next((args.executable.parent / "game/bridge_assets").glob("*.wav")).write_bytes(b"wrong frozen bytes")
    elif damage == "package-story":
        (args.executable.parent / "game/script.rpyc").write_bytes(b"wrong story identity")
    elif damage == "unapproved-audio":
        (args.project / "game/unselected.wav").write_bytes(b"unapproved content must not be played")
    elif damage == "provenance":
        value = json.loads(args.provenance_report.read_text(encoding="utf-8"))
        value["voices"][0]["method"] = "public person recording"
        args.provenance_report.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "native-statement":
        path = args.project / "game/script.rpy"
        path.write_text(path.read_text(encoding="utf-8").replace("    voice ", "    # removed voice ", 1), encoding="utf-8")
    elif damage == "revision":
        path = args.project / "bridge-source-map.json"
        value = json.loads(path.read_text(encoding="utf-8")); value["revision"] += 1
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        args.max_capture_seconds = 5
    run = directory / "qa-failed"
    run.mkdir()
    with pytest.raises(ValueError):
        probe.prepare(args, run)


def test_harness_waits_full_voice_and_sfx_duration_and_isolates_ingame_mixers(inputs):
    args, directory = inputs
    manifest = json.loads((args.project / "bridge-manifest.json").read_text(encoding="utf-8"))
    source_map = json.loads((args.project / "bridge-source-map.json").read_text(encoding="utf-8"))
    phases, _ = probe.load_phases(args, manifest, source_map)
    source = probe.native_source(directory, phases)
    for phase in phases:
        assert "Start(label=" + repr(phase["frame_label"]) in source
        assert phase["name"] + "_advancing" in source and phase["name"] + "_end" in source
        if phase["role"] != "music":
            assert "renpy.music.get_playing(" + repr(phase["channel"]) + ") is None" in source
    assert source.count('run Preference("voice volume", 1.0)') == 3
    assert source.count('run Preference("music volume", 1.0)') == 1
    assert source.count('run Preference("sound volume", 1.0)') == 1
    assert 'physical_microphone' not in source


def test_selection_uses_first_separator_and_requires_frame(inputs):
    _, directory = inputs
    frame, path = probe.selection("frame=" + str(directory / "own=name.wav"))
    assert frame == "frame" and path.name == "own=name.wav"
    with pytest.raises(ValueError):
        probe.selection("=" + str(directory / "voice.wav"))


def test_package_copy_preserves_stdlib_sourceless_cache_and_native_blobs(inputs):
    args, directory = inputs
    original = args.executable.parent
    preserved = {"lib/python3.12/encodings/__init__.pyc": b"source-less stdlib",
                 "renpy/__pycache__/py3analysis.cpython-312.pyc": b"source-less engine bytecode",
                 "lib/py3-windows-x86_64/renpy.audio.pyd": b"native runtime audio codec",
                 "game/retained_identity.rpyc": b"compiled statement identity"}
    for name, content in preserved.items():
        path = original / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    source = original / "renpy/text/text.py"
    source.parent.mkdir(parents=True)
    source.write_text("# exact source counterpart\n", encoding="utf-8")
    cache = original / "renpy/text/__pycache__/text.cpython-312.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"regenerable matching-source cache")
    before = probe.tree_snapshot(original)
    run = directory / "runtime-copy-qa"
    run.mkdir()
    report = probe.prepare(args, run)
    target = report["targets"][1]
    clone = Path(target["qa_directory"])
    for name, content in preserved.items():
        assert (clone / name).read_bytes() == content
    assert (clone / "renpy/text/text.py").read_bytes() == source.read_bytes()
    assert not (clone / "renpy/text/__pycache__/text.cpython-312.pyc").exists()
    assert probe.tree_snapshot(original) == before
    verification = target["copy_verification"]
    assert verification["all_retained_bytes_identical"] and verification["retained_pyc_count"] == 2
    manifest = json.loads(Path(verification["manifest"]).read_text(encoding="utf-8"))["files"]
    assert sha(Path(verification["manifest"])) == verification["manifest_sha256"]
    for name in preserved:
        assert manifest[name] == before[name]


def test_copy_rejects_missing_or_changed_declared_runtime_bytes(inputs, monkeypatch):
    args, directory = inputs
    path = args.executable.parent / "lib/python3.12/encodings/__init__.pyc"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"required stdlib")
    actual = probe.shutil.copytree
    def corrupt(source, destination, *copy_args, **kwargs):
        copied = actual(source, destination, *copy_args, **kwargs)
        if Path(source) == args.executable.parent:
            (Path(destination) / "lib/python3.12/encodings/__init__.pyc").write_bytes(b"changed")
        return copied
    monkeypatch.setattr(probe.shutil, "copytree", corrupt)
    run = directory / "corrupt-runtime-copy-qa"
    run.mkdir()
    with pytest.raises(ValueError, match="declared retained source"):
        probe.prepare(args, run)
