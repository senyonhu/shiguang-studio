"""Audit owned Demo audio in read-only copies of a project and PC executable.

Preparation is read-only with respect to the supplied project/package. Playback
is an explicitly selected real-frame audit, rather than full route coverage or
speech recognition. --prepare-only never launches Ren'Py or opens a recorder.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import traceback
import warnings

from renpy_bridge.media import validate_media
from renpy_bridge.native_qa import NATIVE_QA_SOURCE
from renpy_bridge.sdk import _execute, discover_sdk, run_sdk

from probe_audio import CAPTURE_RATE, envelope_match, read_wav, save_pcm16, signal_metrics


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "m8-demo-audio"
QA_NAMES = {"bridge_testcases", "bridge_interactions", "bridge_audio_qa", "bridge_demo_audio_qa", "testcases"}
EXCLUDED_DIRS = {"saves", "cache", "tests", "private-state"}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_report(directory, report):
    (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_arguments(parser):
    parser.add_argument("--project", type=Path, help="Frozen generated project with bridge manifests")
    package = parser.add_mutually_exclusive_group()
    package.add_argument("--executable", type=Path, help="Frozen independent Windows executable")
    package.add_argument("--package-dir", type=Path, help="Already extracted independent package directory")
    parser.add_argument("--voice", action="append", default=[], metavar="FRAME_ID=OWN_WAV", help="Exactly three distinct owned SAPI voices")
    parser.add_argument("--bgm", metavar="FRAME_ID=OWN_WAV")
    parser.add_argument("--sfx", metavar="FRAME_ID=OWN_WAV")
    parser.add_argument("--provenance-report", type=Path, help="Own Demo audio-generation.json with source hashes and SAPI metadata")
    parser.add_argument("--sdk", type=Path, default=ROOT / "runtimes/renpy-8.5.3")
    parser.add_argument("--max-capture-seconds", type=float, default=45.0, help="Per-target hard limit, 5..90 seconds")
    parser.add_argument("--prepare-only", action="store_true", help="Copy and verify inputs without launching engines or recording")
    parser.add_argument("--capture-demo-child", type=Path, help=argparse.SUPPRESS)


def selection(value):
    if not value or "=" not in value:
        raise ValueError("Audio selection must be FRAME_ID=OWN_WAV")
    frame, path = value.split("=", 1)
    if not frame or not path or "\x00" in value:
        raise ValueError("Empty frame/path or NUL in audio selection")
    return frame, Path(path).expanduser().resolve()


def tree_snapshot(directory):
    """Reject links, then bind every original file, including its frozen code."""
    directory = Path(directory)
    result = {}
    for current, folders, files in os.walk(directory, followlinks=False):
        for name in [*folders, *files]:
            path = Path(current) / name
            stat = path.lstat()
            if path.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                raise ValueError(f"Refuse linked project/package entry: {path}")
        for name in files:
            path = Path(current) / name
            result[path.relative_to(directory).as_posix()] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return result


def _copy_ignore(directory, names):
    return [name for name in names if name in EXCLUDED_DIRS or
            (Path(name).suffix in {".rpy", ".rpyc"} and Path(name).stem in QA_NAMES) or
            name in {"log.txt", "traceback.txt", "errors.txt"} or
            _regenerable_engine_cache(Path(directory), name)]


def _regenerable_engine_cache(directory, name):
    if directory.name != "__pycache__" or "renpy" not in directory.parts:
        return False
    match = re.fullmatch(r"(.+)\.cpython-\d+(?:\.opt-\d+)?\.pyc", name)
    return bool(match and (directory.parent / (match.group(1) + ".py")).is_file())


def copy_manifest(original, source_snapshot):
    """Declare retained source bytes before copying, including source-less pyc."""
    expected = {}
    for current, folders, files in os.walk(original, followlinks=False):
        ignored = set(_copy_ignore(current, [*folders, *files]))
        folders[:] = [name for name in folders if name not in ignored]
        for name in files:
            if name not in ignored:
                relative = (Path(current) / name).relative_to(original).as_posix()
                expected[relative] = source_snapshot[relative]
    return dict(sorted(expected.items()))


def find_executable(path):
    path = Path(path).expanduser().resolve()
    if path.is_file():
        candidates = [path]
    elif path.is_dir():
        candidates = [entry for entry in path.rglob("*.exe") if (entry.parent / "game").is_dir()]
    else:
        raise ValueError("Independent executable/package directory does not exist")
    if len(candidates) != 1 or candidates[0].suffix.lower() != ".exe" or not (candidates[0].parent / "game").is_dir():
        raise ValueError("Supply the unique independent executable whose parent contains game/")
    return candidates[0]


def load_phases(args, manifest, source_map):
    import numpy as np
    if len(args.voice) != 3 or not args.bgm or not args.sfx or not args.provenance_report:
        raise ValueError("Demo audio requires three --voice, --bgm, --sfx and --provenance-report")
    if not 5 <= args.max_capture_seconds <= 90:
        raise ValueError("Capture must be bounded to 5..90 seconds")
    provenance_path = args.provenance_report.expanduser().resolve()
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    specifications = [("voice_" + str(index), "voice", value) for index, value in enumerate(args.voice)]
    specifications += [("music", "music", args.bgm), ("sfx", "sfx", args.sfx)]
    phases = []
    for name, role, value in specifications:
        frame_id, source = selection(value)
        if frame_id not in source_map["frames"] or source.suffix.lower() != ".wav":
            raise ValueError("Selected frame must exist and source must be the owned PCM WAV")
        validate_media("audio", source)
        _, metadata = read_wav(source, np)
        maximum = 10 if role == "voice" else 20
        if not metadata["non_silent"] or not 0 < metadata["duration_seconds"] <= maximum:
            raise ValueError("Audio must be non-silent and within the declared phase bound")
        records = provenance["voices"] if role == "voice" else [provenance["music" if role == "music" else "sfx"]]
        matching = [record for record in records if record["sha256"] == metadata["sha256"]]
        if len(matching) != 1:
            raise ValueError("Source hash must match exactly one owned provenance record")
        record = matching[0]
        if role == "voice":
            generation = record.get("generation", {})
            if record.get("method") != "installed SAPI Chinese TTS" or generation.get("status") != "generated" or generation.get("selected_voice", {}).get("culture") != "zh-CN" or not record.get("text"):
                raise ValueError("Voice must be the recorded own Chinese SAPI generation")
        elif not record.get("method", "").startswith("original "):
            raise ValueError("BGM/SFX must be our original generated test assets")
        if abs(record["duration_seconds"] - metadata["duration_seconds"]) > 1 / metadata["sample_rate"]:
            raise ValueError("Source duration differs from its owned generation record")
        assets = [(aid, item) for aid, item in manifest["assets"].items() if item["sha256"] == metadata["sha256"]]
        if not assets:
            raise ValueError("Selected owned source was not included in the frozen project")
        frame = source_map["frames"][frame_id]
        if frame["file"] != "game/script.rpy":
            raise ValueError("Only generated native story frame source mappings are accepted")
        lines = (args.project / frame["file"]).read_text(encoding="utf-8").splitlines()
        block = lines[frame["start_line"] - 1:frame["end_line"]]
        matches = []
        for aid, asset in assets:
            path = asset["game_path"]
            if not path.startswith("bridge_assets/") or ".." in Path(path).parts or Path(path).is_absolute():
                raise ValueError("Manifest audio must remain under game/bridge_assets")
            quoted = json.dumps(path, ensure_ascii=False)
            if role == "voice":
                channel = "voice" if any(line.strip() == "voice " + quoted for line in block) else None
            elif role == "music":
                channel = "music" if any(line.strip() == "play music " + quoted + " if_changed" for line in block) else None
            else:
                found = [re.fullmatch(r"play (rb_sfx_\d+) " + re.escape(quoted), line.strip()) for line in block]
                channels = [item.group(1) for item in found if item]
                channel = channels[0] if len(channels) == 1 else None
            if channel:
                matches.append((aid, path, channel))
        if len(matches) != 1:
            raise ValueError(f"Exactly one native {role} statement must bind {frame_id} to the specified own source")
        asset_id, game_path, channel = matches[0]
        phases.append({"name": name, "role": role, "channel": channel, "frame_id": frame_id,
                       "frame_label": frame["label"], "asset_id": asset_id, "game_path": game_path,
                       "source": metadata, "provenance": record})
    if len({phase["source"]["sha256"] for phase in phases[:3]}) != 3:
        raise ValueError("Three voices must have three distinct source hashes")
    if len({phase["frame_id"] for phase in phases[:3]}) != 3:
        raise ValueError("Three voices must use three distinct real frames")
    if sum(phase["source"]["duration_seconds"] for phase in phases) + 8 > args.max_capture_seconds:
        raise ValueError("Selected full-duration phases do not fit the bounded capture window")
    allowed = {record["sha256"] for record in [*provenance["voices"], provenance["music"], provenance["sfx"]]}
    for asset in manifest["assets"].values():
        if Path(asset["game_path"]).suffix.lower() in {".wav", ".ogg", ".mp3"} and asset["sha256"] not in allowed:
            raise ValueError("Frozen project includes unapproved audio outside own provenance")
    return phases, {"path": str(provenance_path), "sha256": digest(provenance_path)}


def native_source(directory, phases):
    channels = list(dict.fromkeys(["voice", "music", *[phase["channel"] for phase in phases if phase["role"] == "sfx"]]))
    source = NATIVE_QA_SOURCE + f'''
init 999 python:
    import json as rb_demo_audio_json
    import os as rb_demo_audio_os
    import sys as rb_demo_audio_sys
    import time as rb_demo_audio_time
    from pathlib import Path as rb_demo_audio_Path
    config.main_menu_music = None
    rb_demo_audio_channels = {channels!r}
    rb_demo_audio_trace_path = rb_demo_audio_Path({str(directory / 'native-trace.jsonl')!r})
    rb_demo_audio_go_path = rb_demo_audio_Path({str(directory / 'capture-go')!r})
    def _rb_demo_audio_stop():
        for channel in rb_demo_audio_channels:
            renpy.music.stop(channel=channel, fadeout=0)
    def _rb_demo_audio_trace(stage):
        item = {{"stage": stage, "monotonic": rb_demo_audio_time.monotonic(), "frame_id": rb_current_frame_id, "renpy_version": renpy.version_only,
                "engine_module": renpy.__file__, "python_executable": rb_demo_audio_sys.executable,
                "audio_enabled": renpy.audio.audio.pcm_ok, "driver_override": rb_demo_audio_os.environ.get("SDL_AUDIODRIVER", "default"), "skipping": config.skipping,
                "channels": {{channel: {{"playing": renpy.music.get_playing(channel), "position": renpy.music.get_pos(channel), "duration": renpy.music.get_duration(channel)}} for channel in rb_demo_audio_channels}},
                "mixers": {{mixer: {{"volume": _preferences.get_volume(mixer), "mute": _preferences.get_mute(mixer)}} for mixer in ("main", "voice", "music", "sfx")}}}}
        with rb_demo_audio_trace_path.open("a", encoding="utf-8") as stream:
            stream.write(rb_demo_audio_json.dumps(item, ensure_ascii=False) + "\\n")
        print("NATIVE_DEMO_AUDIO=" + rb_demo_audio_json.dumps(item, ensure_ascii=False))

testsuite global:
    teardown:
        $ _rb_demo_audio_stop()
        exit

testcase native_demo_audio:
    $ _test.timeout = 25.0
    $ _preferences.text_cps = 0
    $ _preferences.afm_enable = False
    $ config.skipping = None
    run Preference("all mute", "disable")
    run Preference("main volume", 0.65)
    $ _rb_demo_audio_stop()
    $ _rb_demo_audio_trace("ready")
    pause 0.1 until eval rb_demo_audio_go_path.exists()
'''
    for phase in phases:
        name, role, channel = phase["name"], phase["role"], phase["channel"]
        text_condition = ""
        if role == "voice":
            text_condition = " and _rb_qa_visible_text() == " + repr(phase["provenance"]["text"])
        source += f'''
    if eval (not main_menu):
        run MainMenu(confirm=False, save=False)
    pause 0.1 until screen "main_menu"
    $ _rb_demo_audio_stop()
    run Preference("voice volume", {1.0 if role == 'voice' else 0.0})
    run Preference("music volume", {1.0 if role == 'music' else 0.0})
    run Preference("sound volume", {1.0 if role == 'sfx' else 0.0})
    $ _rb_demo_audio_trace({(name + '_start')!r})
    run Start(label={phase['frame_label']!r})
    pause 0.01 until eval (rb_current_frame_id == {phase['frame_id']!r}{text_condition} and renpy.music.get_playing({channel!r}) == {phase['game_path']!r} and renpy.music.get_pos({channel!r}) is not None)
    $ _rb_demo_audio_trace({(name + '_playing')!r})
    $ rb_demo_audio_initial_pos = renpy.music.get_pos({channel!r})
    pause 0.15
    assert eval (renpy.audio.audio.pcm_ok and config.skipping is None and rb_demo_audio_os.environ.get("SDL_AUDIODRIVER") != "dummy")
    assert eval (renpy.music.get_pos({channel!r}) is not None and renpy.music.get_pos({channel!r}) > rb_demo_audio_initial_pos)
    $ _rb_demo_audio_trace({(name + '_advancing')!r})
'''
        if role == "music":
            source += f"    pause {phase['source']['duration_seconds'] + .1!r}\n"
        else:
            source += f"    pause 0.05 until eval (renpy.music.get_playing({channel!r}) is None)\n"
        source += f'''    $ _rb_demo_audio_trace({(name + '_end')!r})
    $ _rb_demo_audio_stop()
    pause 0.1
'''
    source += '''    assert eval all(renpy.music.get_playing(channel) is None for channel in rb_demo_audio_channels)
    $ _rb_demo_audio_trace("done")
'''
    return source


def prepare(args, run_dir):
    if not args.project:
        raise ValueError("--project is required for Demo mode")
    args.project = args.project.expanduser().resolve()
    if not (args.project / "game").is_dir():
        raise ValueError("Generated project must contain game/")
    manifest_path = args.project / "bridge-manifest.json"
    source_map_path = args.project / "bridge-source-map.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_map = json.loads(source_map_path.read_text(encoding="utf-8"))
    if (manifest["project_id"], manifest["revision"], manifest["sdk_version"]) != (source_map["project_id"], source_map["revision"], source_map["sdk_version"]):
        raise ValueError("Frozen manifests must identify the same project, revision and SDK")
    phases, provenance = load_phases(args, manifest, source_map)
    targets = [{"kind": "sdk-project", "source_directory": str(args.project)}]
    package = args.executable or args.package_dir
    if package:
        exe = find_executable(package)
        targets.append({"kind": "independent-package", "source_directory": str(exe.parent), "original_executable": str(exe), "executable_sha256": digest(exe)})
    report = {"schema_version": 1, "probe": "formal-demo-native-audio-owned-source-loopback", "complete": False,
              "scope": "Selected real frame labels in isolated QA copies of supplied generated project and optional standalone package. No full-route, speech-recognition, or human-listening claim.",
              "run_directory": str(run_dir), "project_id": manifest["project_id"], "revision": manifest["revision"],
              "sdk_root": str(args.sdk.expanduser().resolve()), "sdk_version": manifest["sdk_version"],
              "manifest": {"path": str(manifest_path), "sha256": digest(manifest_path)},
              "source_map": {"path": str(source_map_path), "sha256": digest(source_map_path)}, "provenance_report": provenance,
              "phases": phases, "targets": targets, "maximum_capture_seconds": args.max_capture_seconds,
              "code_sha256": {str(path.relative_to(ROOT)): digest(path) for path in [Path(__file__).resolve(), ROOT / "tools/probe_engine_audio.py", ROOT / "tools/probe_audio.py", ROOT / "src/renpy_bridge/native_qa.py"]},
              "capture_policy": {"physical_microphone_opened": False, "system_volume_changed": False, "owned_generated_audio_only": True,
                                 "ownership_scope": "Only probe-launched playback sources; default-speaker mix can include unrelated applications.",
                                 "other_ingame_mixers_isolated": True}, "prepared": False, "actual_audio_output_verified": False}
    for index, target in enumerate(targets):
        original = Path(target["source_directory"])
        target["source_snapshot"] = tree_snapshot(original)
        allowed_audio = {phase["source"]["sha256"] for phase in phases}
        for path in (original / "game").rglob("*"):
            if path.is_file() and path.suffix.lower() in {".wav", ".ogg", ".mp3"} and digest(path) not in allowed_audio:
                raise ValueError("QA copy may contain only the five selected owned audio sources")
        for phase in phases:
            asset = original / "game" / phase["game_path"]
            if not asset.is_file() or digest(asset) != phase["source"]["sha256"]:
                raise ValueError("Frozen project/package audio bytes do not match selected owned source")
        if target["kind"] == "independent-package":
            identities = [name for name in ("script.rpy", "script.rpyc") if (original / "game" / name).is_file() and (args.project / "game" / name).is_file() and digest(original / "game" / name) == digest(args.project / "game" / name)]
            if not identities:
                raise ValueError("Independent package story must match the supplied frozen generated project")
            target["story_identity_files"] = {name: digest(original / "game" / name) for name in identities}
        directory = run_dir / (str(index) + "-" + target["kind"])
        directory.mkdir()
        clone = directory / "qa-copy"
        expected = copy_manifest(original, target["source_snapshot"])
        shutil.copytree(original, clone, ignore=_copy_ignore, copy_function=shutil.copy2)
        if tree_snapshot(clone) != expected:
            raise ValueError("QA clone bytes differ from the declared retained source manifest")
        copy_path = directory / "retained-source-files.json"
        copy_path.write_text(json.dumps({"files": expected}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        target["copy_verification"] = {"manifest": str(copy_path), "manifest_sha256": digest(copy_path),
                                       "file_count": len(expected), "all_retained_bytes_identical": True,
                                       "retained_pyc_count": sum(name.endswith(".pyc") for name in expected),
                                       "pyc_policy": "Preserve stdlib and source-less pyc; exclude only renpy __pycache__ with exact sibling .py source."}
        if target["kind"] == "independent-package":
            target["executable"] = str(clone / Path(target["original_executable"]).name)
        target.update(run_directory=str(directory), qa_directory=str(clone), commands={}, complete=False)
        harness = clone / "game/bridge_demo_audio_qa.rpy"
        harness.write_text(native_source(directory, phases), encoding="utf-8")
        target["qa_harness_sha256"] = digest(harness)
        write_report(directory, {"schema_version": 1, "complete": False, "target": target, "phases": phases,
                                 "sdk_root": report["sdk_root"], "sdk_version": report["sdk_version"],
                                 "maximum_capture_seconds": args.max_capture_seconds})
    report["prepared"] = True
    report["source_inputs_unchanged"] = all(tree_snapshot(Path(target["source_directory"])) == target["source_snapshot"] for target in targets)
    return report


def capture(directory):
    import numpy as np
    import soundcard as sc
    report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    target, result, chunks = report["target"], {}, []
    engine_thread = None
    capture_start = None
    try:
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("No default speaker")
        matching = [device for device in sc.all_microphones(include_loopback=True) if device.isloopback and str(device.id) == str(speaker.id)]
        if not matching or matching[0].channels < 2:
            raise RuntimeError("No supported stereo WASAPI loopback on the actual default speaker")
        loopback = matching[0]
        report["output"] = {"speaker": {"id": str(speaker.id), "name": speaker.name, "channels": speaker.channels},
                            "capture_rate": CAPTURE_RATE, "physical_microphone_opened": False, "system_volume_changed": False,
                            "loopback": True, "maximum_capture_seconds": report["maximum_capture_seconds"]}
        qa = Path(target["qa_directory"])
        saves = directory / "private-saves"
        saves.mkdir(exist_ok=True)
        env = {"RENPY_MULTIPERSISTENT": str(directory / "private-multipersistent")}
        def engine():
            try:
                args = ["native_demo_audio", "--report-detailed", "--savedir", str(saves)]
                if target["kind"] == "sdk-project":
                    sdk = discover_sdk(Path(report["sdk_root"]))
                    if sdk.version != report["sdk_version"]:
                        raise RuntimeError("SDK differs from frozen project manifest")
                    result["command"] = run_sdk(sdk, qa, "test", args, timeout_seconds=report["maximum_capture_seconds"] + 30, env=env)
                else:
                    result["command"] = _execute([target["executable"], str(qa), "test", *args], qa, report["maximum_capture_seconds"] + 30, env)
            except Exception as exc:
                result["error"] = repr(exc)
        engine_thread = threading.Thread(target=engine, name="owned-demo-native-audio", daemon=False)
        engine_thread.start()
        trace = directory / "native-trace.jsonl"
        deadline = time.monotonic() + 25
        while not trace.exists():
            if not engine_thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("Native Demo did not reach ready handshake: " + str(result.get("error", "")))
            time.sleep(.05)
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always")
            with loopback.recorder(samplerate=CAPTURE_RATE, channels=list(range(loopback.channels)), blocksize=2048) as recorder:
                capture_start = time.monotonic()
                report["output"]["capture_start_monotonic"] = capture_start
                (directory / "capture-go").write_text("owned-demo-audio-only\n", encoding="utf-8")
                saved = 0
                maximum = int(report["maximum_capture_seconds"] * CAPTURE_RATE)
                while time.monotonic() - capture_start < report["maximum_capture_seconds"] and saved < maximum:
                    chunk = recorder.record(numframes=None)
                    if chunk.size:
                        chunk = chunk[:maximum - saved].copy()
                        chunks.append(chunk)
                        saved += len(chunk)
                    else:
                        time.sleep(.005)
                    if '"stage": "done"' in trace.read_text(encoding="utf-8") or not engine_thread.is_alive():
                        break
                report["output"]["capture_wall_seconds"] = time.monotonic() - capture_start
            report["output"]["recorder_closed"] = True
            report["output"]["warnings"] = sorted({str(item.message) for item in observed})
        engine_thread.join(timeout=30)
        if engine_thread.is_alive() or "command" not in result:
            raise RuntimeError("Native Demo engine did not complete: " + str(result.get("error", "")))
        command = result["command"]
        report["command"] = asdict(command)
        (directory / "native-audio.stdout.log").write_text(command.stdout, encoding="utf-8")
        (directory / "native-audio.stderr.log").write_text(command.stderr, encoding="utf-8")
        report["native_trace"] = traces = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        indexed = {item["stage"]: item for item in traces}
        if not chunks:
            raise RuntimeError("Loopback did not produce PCM samples")
        recording = np.concatenate(chunks, axis=0)
        output = directory / "native-speaker-loopback.wav"
        save_pcm16(output, recording, CAPTURE_RATE, np)
        _, report["output"]["wav"] = read_wav(output, np)
        checks = []
        for phase in report["phases"]:
            name = phase["name"]
            required = [name + suffix for suffix in ("_start", "_playing", "_advancing", "_end")]
            if any(stage not in indexed for stage in required):
                checks.append({"phase": name, "passed": False, "error": "Missing phase trace markers"})
                continue
            start = max(0, int((indexed[name + "_start"]["monotonic"] - capture_start - .2) * CAPTURE_RATE))
            end = min(len(recording), int((indexed[name + "_end"]["monotonic"] - capture_start + .2) * CAPTURE_RATE))
            samples = recording[start:end]
            source, metadata = read_wav(Path(phase["source"]["path"]), np)
            if metadata["sha256"] != phase["source"]["sha256"]:
                raise RuntimeError("Owned source changed after preparation")
            phase_output = directory / (name + "-output.wav")
            save_pcm16(phase_output, samples, CAPTURE_RATE, np)
            match = envelope_match(source, metadata["sample_rate"], samples, np)
            metrics = signal_metrics(samples, np)
            playing = indexed[name + "_playing"]["channels"][phase["channel"]]
            advancing = indexed[name + "_advancing"]["channels"][phase["channel"]]
            finished = indexed[name + "_end"]["channels"][phase["channel"]]
            native = playing["playing"] == phase["game_path"] and advancing["playing"] == phase["game_path"] and advancing["position"] > playing["position"]
            native = native and (phase["role"] == "music" or finished["playing"] is None)
            native_duration = indexed[name + "_end"]["monotonic"] - indexed[name + "_playing"]["monotonic"]
            native = native and native_duration >= metadata["duration_seconds"] * .9
            checks.append({"phase": name, "frame_id": phase["frame_id"], "channel": phase["channel"],
                           "source_sha256": metadata["sha256"], "path": str(phase_output), "sha256": digest(phase_output),
                           "duration_seconds": len(samples) / CAPTURE_RATE, "clock_alignment_margin_seconds": .2,
                           "native_phase_duration_seconds": native_duration, "source_duration_seconds": metadata["duration_seconds"],
                           "source_envelope_match": match, "native_channel_verified": native, **metrics,
                           "passed": native and metrics["non_silent"] and match["correlation"] is not None and match["correlation"] > .8 and len(samples) / CAPTURE_RATE >= metadata["duration_seconds"] * .9})
        report["output"]["checks"] = checks
        runtime_root = qa if target["kind"] == "independent-package" else Path(report["sdk_root"])
        report["engine_provenance_verified"] = all(Path(item[key]).resolve().is_relative_to(runtime_root.resolve()) for item in traces for key in ("engine_module", "python_executable"))
        report["engine_verified"] = command.exit_code == 0 and not command.timed_out and command.cleanup_complete and "[rpytest] Status: PASSED" in command.stdout and "done" in indexed and all(item["renpy_version"] == report["sdk_version"] for item in traces) and report["engine_provenance_verified"]
        report["actual_audio_output_verified"] = len(checks) == 5 and all(check["passed"] for check in checks)
        report["source_to_output_interpretation"] = "Real native frames identify the frozen owned file on its expected channel with advancing position and full-duration playback. Bounded default-speaker output is non-silent and preserves the exact source envelope. This supports source playback; no speech recognition, semantic intelligibility judgment, or human ear listening is claimed."
        report["complete"] = report["engine_verified"] and report["actual_audio_output_verified"]
    except Exception as exc:
        report.update(complete=False, error=repr(exc), traceback=traceback.format_exc())
        if "command" in result:
            report["command"] = asdict(result["command"])
        trace = directory / "native-trace.jsonl"
        if trace.is_file():
            report["native_trace_raw"] = trace.read_text(encoding="utf-8")
        if chunks:
            output = directory / "partial-speaker-loopback.wav"
            save_pcm16(output, np.concatenate(chunks, axis=0), CAPTURE_RATE, np)
            report["partial_output"] = {"path": str(output), "sha256": digest(output)}
    write_report(directory, report)
    print(json.dumps({"report": str(directory / "report.json"), "complete": report["complete"]}, ensure_ascii=False))
    return 0 if report["complete"] else 1


def run(args):
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    report = {"complete": False, "prepared": False, "run_directory": str(run_dir)}
    try:
        report = prepare(args, run_dir)
        write_report(run_dir, report)
        if args.prepare_only:
            report["playback_status"] = "not_run_prepare_only"
        else:
            for target in report["targets"]:
                directory = Path(target["run_directory"])
                process = _execute([sys.executable, str(Path(__file__).resolve()), "--capture-demo-child", str(directory)], ROOT, args.max_capture_seconds + 70, None)
                target["capture_process"] = asdict(process)
                result = json.loads((directory / "report.json").read_text(encoding="utf-8"))
                target["audio_report"] = {"path": str(directory / "report.json"), "sha256": digest(directory / "report.json"), "result": result}
                target["complete"] = result["complete"] and process.exit_code == 0 and not process.timed_out and process.cleanup_complete
                write_report(run_dir, report)
            report["actual_audio_output_verified"] = all(target["complete"] for target in report["targets"])
            report["complete"] = report["actual_audio_output_verified"]
        report["source_inputs_unchanged"] = all(tree_snapshot(Path(target["source_directory"])) == target["source_snapshot"] for target in report["targets"])
        report["owned_sources_unchanged"] = all(digest(Path(phase["source"]["path"])) == phase["source"]["sha256"] for phase in report["phases"])
        report["complete"] = report["complete"] and report["source_inputs_unchanged"] and report["owned_sources_unchanged"]
    except Exception as exc:
        report.update(complete=False, error=repr(exc), traceback=traceback.format_exc())
    write_report(run_dir, report)
    print(json.dumps({"report": str(run_dir / "report.json"), "prepared": report["prepared"], "complete": report["complete"]}, ensure_ascii=False))
    return 0 if (report["complete"] or (args.prepare_only and report["prepared"] and report["source_inputs_unchanged"])) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    args = parser.parse_args(argv)
    if args.capture_demo_child:
        return capture(args.capture_demo_child.resolve())
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
