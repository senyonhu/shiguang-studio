"""Prove native Ren'Py playback with bounded own-speech speaker loopback.

Without project arguments, run the fixed M0 speech fixture. Use --project and
--executable/--package-dir for the formal three-voice Demo audit; --prepare-only
verifies and copies those inputs without launching an engine or recorder.

The immutable M0 SAPI Chinese speech is used as the voice and SFX fixture.
BGM is three copies of that same speech, explicitly a test track. No physical
microphone is opened, device/system volume is never changed, and recording
starts only after the native testcase is ready to play these owned assets.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback
import warnings

from renpy_bridge.generator import generate_project
from renpy_bridge.models import Act, Asset, Dialogue, Flow, Frame, Project
from renpy_bridge.native_qa import NATIVE_QA_SOURCE
from renpy_bridge.sdk import _execute, discover_sdk, run_sdk

from probe_audio import CAPTURE_RATE, envelope_match, read_wav, save_pcm16, signal_metrics

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "m5-audio"
MAX_CAPTURE_SECONDS = 45.0


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_report(run_dir: Path, report: dict) -> None:
    value = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    (run_dir / "report.json").write_text(value, encoding="utf-8")
    (EVIDENCE / "report.json").write_text(value, encoding="utf-8")


def native_source(run_dir: Path, paths: dict, duration: float) -> str:
    header = f'''init -1 python:
    import json as rb_audio_json
    import os as rb_audio_os
    import time as rb_audio_time
    from pathlib import Path as rb_audio_Path
    rb_audio_trace_path = rb_audio_Path({str(run_dir / 'native-trace.jsonl')!r})
    rb_audio_go_path = rb_audio_Path({str(run_dir / 'capture-go')!r})
    def _rb_audio_trace(stage):
        item = {{"stage": stage, "monotonic": rb_audio_time.monotonic(), "frame_id": rb_current_frame_id,
                "skipping": config.skipping, "audio_enabled": renpy.audio.audio.pcm_ok, "driver_override": rb_audio_os.environ.get("SDL_AUDIODRIVER", "default"),
                "channels": {{name: {{"playing": renpy.music.get_playing(name), "position": renpy.music.get_pos(name), "duration": renpy.music.get_duration(name)}} for name in ("voice", "music", "rb_sfx_0")}},
                "mixers": {{name: {{"volume": _preferences.get_volume(name), "mute": _preferences.get_mute(name)}} for name in ("main", "voice", "music", "sfx")}}}}
        with rb_audio_trace_path.open("a", encoding="utf-8") as stream:
            stream.write(rb_audio_json.dumps(item, ensure_ascii=False) + "\\n")
        print("NATIVE_AUDIO=" + rb_audio_json.dumps(item, ensure_ascii=False))

testsuite global:
    teardown:
        exit

testcase native_audio:
    $ _test.timeout = 15.0
    $ _preferences.text_cps = 0
    $ _preferences.afm_enable = False
    $ config.skipping = None
    run Preference("all mute", "disable")
    run Preference("main volume", 0.65)
    run Preference("voice volume", 1.0)
    run Preference("music volume", 0.5)
    run Preference("sound volume", 0.5)
    $ _rb_audio_trace("ready")
    pause 0.1 until eval rb_audio_go_path.exists()
    $ _rb_audio_trace("voice_start")
    run Start()
    pause 0.05 until eval (rb_current_frame_id == "frame_voice" and _rb_qa_visible_text() == "这是中文语音测试。" and renpy.music.get_playing("voice") == {paths['voice']!r} and renpy.music.get_pos("voice") is not None)
    $ _rb_audio_trace("voice_playing")
    $ rb_voice_pos = renpy.music.get_pos("voice")
    pause 0.3
    assert eval (renpy.audio.audio.pcm_ok and config.skipping is None and rb_audio_os.environ.get("SDL_AUDIODRIVER") != "dummy")
    assert eval (renpy.music.get_pos("voice") > rb_voice_pos)
    assert eval (not _preferences.get_mute("voice") and _preferences.get_volume("voice") > 0)
    pause 0.05 until eval (renpy.music.get_playing("voice") is None)
    $ _rb_audio_trace("voice_end")
    $ _rb_audio_trace("replay_start")
    run VoiceReplay()
    pause 0.05 until eval (renpy.music.get_playing("voice") == {paths['voice']!r} and renpy.music.get_pos("voice") is not None)
    assert eval (renpy.music.get_pos("voice") < 0.5)
    pause 0.05 until eval (renpy.music.get_playing("voice") is None)
    $ _rb_audio_trace("replay_end")
    $ _rb_audio_trace("music_start")
    keysym "dismiss"
    pause 0.05 until eval (rb_current_frame_id == "frame_music" and _rb_qa_visible_text() == "音乐通道单独播放自有语音测试音轨。")
    pause 0.05 until eval (renpy.music.get_playing("music") == {paths['music']!r} and renpy.music.get_pos("music") is not None)
    assert eval (renpy.music.get_playing("voice") is None and renpy.music.get_playing("rb_sfx_0") is None)
    $ _rb_audio_trace("music_playing")
    pause {duration + 0.05!r}
    $ _rb_audio_trace("music_end")
    $ _rb_audio_trace("sfx_start")
    keysym "dismiss"
    pause 0.05 until eval (rb_current_frame_id == "frame_sfx" and _rb_qa_visible_text() == "音效通道单独播放自有语音测试音轨。")
    pause 0.05 until eval (renpy.music.get_playing("rb_sfx_0") == {paths['sfx']!r} and renpy.music.get_pos("rb_sfx_0") is not None)
    assert eval (renpy.music.get_playing("music") is None and renpy.music.get_playing("voice") is None)
    $ rb_sfx_pos = renpy.music.get_pos("rb_sfx_0")
    pause 0.3
    assert eval (renpy.music.get_pos("rb_sfx_0") > rb_sfx_pos)
    pause 0.05 until eval (renpy.music.get_playing("rb_sfx_0") is None)
    $ _rb_audio_trace("sfx_end")
    $ _rb_audio_trace("mix_start")
    keysym "dismiss"
    pause 0.05 until eval (rb_current_frame_id == "frame_mix_first" and _rb_qa_visible_text() == "混音第一帧：语音、音乐和音效。")
    pause 0.05 until eval (all(renpy.music.get_pos(name) is not None for name in ("voice", "music", "rb_sfx_0")))
    $ _rb_audio_trace("mix_first")
    pause 0.8
    $ rb_music_pos = renpy.music.get_pos("music")
    $ rb_voice_pos = renpy.music.get_pos("voice")
    $ rb_sfx_pos = renpy.music.get_pos("rb_sfx_0")
    keysym "dismiss"
    pause 0.05 until eval (rb_current_frame_id == "frame_mix_second" and _rb_qa_visible_text() == "混音第二帧：同语音重播，音乐连续。")
    pause 0.05 until eval (all(renpy.music.get_pos(name) is not None for name in ("voice", "music", "rb_sfx_0")))
    $ _rb_audio_trace("mix_second")
    assert eval (renpy.music.get_pos("music") >= rb_music_pos)
    assert eval (renpy.music.get_pos("voice") < rb_voice_pos)
    assert eval (renpy.music.get_pos("rb_sfx_0") < rb_sfx_pos)
    pause 0.4
    $ _rb_audio_trace("mix_end")
    run ShowMenu("preferences")
    pause 0.1 until screen "preferences"
    run Preference("voice volume", 0.25)
    assert eval (abs(_preferences.get_volume("voice") - 0.25) < 0.001)
    run Preference("voice volume", 1.0)
    keysym "game_menu"
    pause 0.1 until not screen "preferences"
    assert eval (rb_current_frame_id == "frame_mix_second")
    run Preference("all mute", "enable")
    pause 0.3
    $ _rb_audio_trace("mute_start")
    assert eval all(_preferences.get_mute(name) for name in ("main", "voice", "music", "sfx"))
    pause 0.6
    $ _rb_audio_trace("mute_end")
    run Preference("all mute", "disable")
    $ _rb_audio_trace("unmute_start")
    run VoiceReplay()
    pause 0.05 until eval (renpy.music.get_pos("voice") is not None)
    $ _rb_audio_trace("unmute_playing")
    assert eval (not _preferences.get_mute("voice"))
    pause 0.4
    $ _rb_audio_trace("unmute_end")
    keysym "dismiss"
    pause 0.05 until eval (rb_current_frame_id == "frame_silent" and _rb_qa_visible_text() == "静音帧：所有剧情音频停止。")
    pause 0.2
    assert eval all(renpy.music.get_playing(name) is None for name in ("voice", "music", "rb_sfx_0"))
    $ _rb_audio_trace("done")
'''
    return NATIVE_QA_SOURCE + "\n" + header


def capture_native(run_dir: Path) -> int:
    import numpy as np
    import soundcard as sc
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    result = {}
    captured = []
    engine_thread = None
    try:
        sdk = discover_sdk(ROOT / "runtimes" / "renpy-8.5.3")
        target = Path(report["generation"]["project_path"])
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("No default speaker")
        matching = [device for device in sc.all_microphones(include_loopback=True)
                    if device.isloopback and str(device.id) == str(speaker.id)]
        if not matching or matching[0].channels < 2:
            raise RuntimeError("No supported stereo loopback for the actual default speaker")
        loopback = matching[0]
        report["output"] = {"default_speaker": {"id": str(speaker.id), "name": speaker.name,
                            "channels": speaker.channels}, "loopback": True,
                            "physical_microphone_opened": False, "volume_changed": False,
                            "capture_rate": CAPTURE_RATE, "capture_limit_seconds": MAX_CAPTURE_SECONDS}
        def engine():
            result["command"] = run_sdk(sdk, target, "test", ["native_audio", "--report-detailed", "--savedir", str(run_dir / "private-saves")],
                                        timeout_seconds=65, env={"RENPY_MULTIPERSISTENT": str(run_dir / "private-multipersistent")})
        # Keep the bounded SDK owner alive if recorder setup/read fails; the
        # outer owned process-tree timeout still handles a stalled driver.
        engine_thread = threading.Thread(target=engine, name="owned-native-audio", daemon=False)
        engine_thread.start()
        trace_path = run_dir / "native-trace.jsonl"
        ready_deadline = time.monotonic() + 20
        while not trace_path.is_file():
            if not engine_thread.is_alive() or time.monotonic() >= ready_deadline:
                raise RuntimeError("Native audio testcase did not reach its ready handshake")
            time.sleep(0.05)
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always")
            with loopback.recorder(samplerate=CAPTURE_RATE, channels=list(range(loopback.channels)), blocksize=2048) as recorder:
                report["output"]["capture_start_monotonic"] = capture_start = time.monotonic()
                (run_dir / "capture-go").write_text("owned-test-playback-only\n", encoding="utf-8")
                saved_frames = 0
                max_frames = int(MAX_CAPTURE_SECONDS * CAPTURE_RATE)
                while time.monotonic() - capture_start < MAX_CAPTURE_SECONDS:
                    chunk = recorder.record(numframes=None)
                    if chunk.size:
                        chunk = chunk[:max_frames - saved_frames].copy()
                        captured.append(chunk)
                        saved_frames += len(chunk)
                    else:
                        time.sleep(0.005)
                    traces = trace_path.read_text(encoding="utf-8")
                    if '"stage": "done"' in traces or not engine_thread.is_alive():
                        break
                report["output"]["capture_wall_seconds"] = time.monotonic() - capture_start
            report["output"]["recorder_closed"] = True
            report["output"]["warnings"] = list(dict.fromkeys(str(item.message) for item in observed))
        engine_thread.join(timeout=20)
        if engine_thread.is_alive() or "command" not in result:
            raise RuntimeError("Owned native engine did not terminate after bounded playback")
        command = result["command"]
        report["commands"]["native-audio"] = asdict(command)
        (run_dir / "native-audio.stdout.log").write_text(command.stdout, encoding="utf-8")
        (run_dir / "native-audio.stderr.log").write_text(command.stderr, encoding="utf-8")
        for engine_file in ("log.txt", "traceback.txt", "errors.txt"):
            if (target / engine_file).is_file():
                shutil.copyfile(target / engine_file, run_dir / ("native-audio." + engine_file))
        if not captured:
            raise RuntimeError("Native audio loopback produced no samples")
        recording = np.concatenate(captured, axis=0)
        output_path = run_dir / "native-speaker-loopback.wav"
        save_pcm16(output_path, recording, CAPTURE_RATE, np)
        _, report["output"]["wav"] = read_wav(output_path, np)
        traces = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
        report["native_trace"] = traces
        indexed = {trace["stage"]: trace for trace in traces}
        source, metadata = read_wav(Path(report["source"]["path"]), np)
        checks = {}
        for name in ("voice", "replay", "music", "sfx", "mix", "mute", "unmute"):
            if name + "_start" not in indexed or name + "_end" not in indexed:
                checks[name] = {"passed": False, "error": "Missing bounded playback phase markers"}
                continue
            # The WASAPI audio clock can differ slightly from wall-clock
            # phase markers. Preserve a bounded 200ms alignment margin for
            # independent source comparison; mute uses its strict markers.
            margin = 0.2 if name in ("voice", "replay", "music", "sfx") else 0.0
            start = max(0, int((indexed[name + "_start"]["monotonic"] - capture_start - margin) * CAPTURE_RATE))
            end = min(len(recording), int((indexed[name + "_end"]["monotonic"] - capture_start + margin) * CAPTURE_RATE))
            phase = recording[start:end]
            phase_path = run_dir / (name + "-output.wav")
            save_pcm16(phase_path, phase, CAPTURE_RATE, np)
            metrics = signal_metrics(phase, np)
            check = {"path": str(phase_path), "sha256": digest(phase_path), "duration_seconds": len(phase) / CAPTURE_RATE, "clock_alignment_margin_seconds": margin, **metrics}
            if name in ("voice", "replay", "music", "sfx"):
                check["source_envelope_match"] = envelope_match(source, metadata["sample_rate"], phase, np)
                correlation = check["source_envelope_match"]["correlation"]
                check["passed"] = metrics["non_silent"] and correlation is not None and correlation > 0.8 and len(phase) / CAPTURE_RATE >= metadata["duration_seconds"] * 0.9
            elif name == "mute":
                check["passed"] = metrics["rms"] < 1e-5
            else:
                check["passed"] = metrics["non_silent"]
            checks[name] = check
        report["output"]["checks"] = checks
        report["source_to_output_interpretation"] = "The native engine identifies the immutable SAPI source file on each channel, advancing positions and complete speech playback; independent output phases preserve its RMS envelope. This correlates the exact generated speech source, rather than treating arbitrary output activity as speech proof. No speech recognition is claimed."
        report["engine_verified"] = command.exit_code == 0 and not command.timed_out and command.cleanup_complete and "[rpytest] Status: PASSED" in command.stdout
        report["actual_audio_output_verified"] = all(check["passed"] for check in checks.values())
        report["complete"] = report["engine_verified"] and report["actual_audio_output_verified"]
    except Exception as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        report["complete"] = False
        if "command" in result:
            report["commands"]["native-audio"] = asdict(result["command"])
        if (run_dir / "native-trace.jsonl").is_file():
            report["native_trace"] = [json.loads(line) for line in (run_dir / "native-trace.jsonl").read_text(encoding="utf-8").splitlines()]
    save_report(run_dir, report)
    print(json.dumps({"report_path": str(run_dir / "report.json"), "complete": report["complete"]}, ensure_ascii=False))
    return 0 if report["complete"] else 1


def main() -> int:
    from probe_demo_audio import add_arguments, capture as capture_demo, run as run_demo
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-child", type=Path)
    add_arguments(parser)
    args = parser.parse_args()
    if args.capture_demo_child:
        return capture_demo(args.capture_demo_child.resolve())
    if args.project or args.executable or args.package_dir or args.prepare_only or args.voice or args.bgm or args.sfx or args.provenance_report:
        return run_demo(args)
    if args.capture_child:
        return capture_native(args.capture_child.resolve())
    import numpy as np
    run_dir = EVIDENCE / "runs" / str(time.time_ns())
    run_dir.mkdir(parents=True, exist_ok=False)
    m0 = json.loads((ROOT / "evidence/m0-audio/report.json").read_text(encoding="utf-8"))
    source_path = Path(m0["tts"]["output_path"])
    source, metadata = read_wav(source_path, np)
    if not metadata["non_silent"] or not 0 < metadata["duration_seconds"] <= 10:
        raise RuntimeError("M0 source is not bounded non-silent Chinese speech")
    sdk = discover_sdk(ROOT / "runtimes" / "renpy-8.5.3")
    authoring = run_dir / "authoring"
    (authoring / "assets").mkdir(parents=True)
    for name in ("voice", "sfx"):
        shutil.copyfile(source_path, authoring / "assets" / (name + ".wav"))
    music = np.concatenate([source, source, source], axis=0) * 0.5
    save_pcm16(authoring / "assets/music.wav", music, metadata["sample_rate"], np)
    assets = {"asset_" + name: Asset(asset_id="asset_" + name, kind="audio", path="assets/" + name + ".wav", sha256=digest(authoring / "assets" / (name + ".wav"))) for name in ("voice", "music", "sfx")}
    ids = ["frame_voice", "frame_music", "frame_sfx", "frame_mix_first", "frame_mix_second", "frame_silent"]
    texts = ["这是中文语音测试。", "音乐通道单独播放自有语音测试音轨。", "音效通道单独播放自有语音测试音轨。", "混音第一帧：语音、音乐和音效。", "混音第二帧：同语音重播，音乐连续。", "静音帧：所有剧情音频停止。"]
    frames = {frame_id: Frame(frame_id=frame_id, act_id="act_audio", order=index, dialogue=Dialogue(text=texts[index]),
              voice="asset_voice" if index in (0, 3, 4) else None,
              bgm="asset_music" if index in (1, 3, 4) else None,
              sfx=["asset_sfx"] if index in (2, 3, 4) else [],
              flow=Flow(kind="next", target_frame_id=ids[index + 1]) if index < len(ids) - 1 else Flow(kind="end")) for index, frame_id in enumerate(ids)}
    project = Project(project_id="project_native_audio", name="原生音频验收", save_namespace="rb_native_audio_fixture",
                      acts={"act_audio": Act(act_id="act_audio", frame_ids=ids)}, frames=frames, assets=assets, entry_frame_id=ids[0])
    generation = generate_project(project, authoring, run_dir / "中文 原生音频工程", sdk)
    target = Path(generation["project_path"])
    manifest = json.loads(Path(generation["manifest_path"]).read_text(encoding="utf-8"))
    paths = {name: manifest["assets"]["asset_" + name]["game_path"] for name in ("voice", "music", "sfx")}
    (target / "game/bridge_audio_qa.rpy").write_text(native_source(run_dir, paths, metadata["duration_seconds"]), encoding="utf-8")
    report = {"schema_version": 1, "probe": "native-engine-audio-own-speaker-loopback", "complete": False,
              "run_directory": str(run_dir), "generation": generation, "sdk_version": sdk.version,
              "source": metadata, "provenance": {"m0_report": str(ROOT / "evidence/m0-audio/report.json"), "m0_report_sha256": digest(ROOT / "evidence/m0-audio/report.json"), "tts": m0["tts"]["selected_voice"], "text": m0["text"], "bgm_fixture": "three repeats of the owned Chinese speech, amplitude 0.5; a test track"},
              "code_sha256": {str(path.relative_to(ROOT)): digest(path) for path in [Path(__file__).resolve(), ROOT / "src/renpy_bridge/generator.py", ROOT / "src/renpy_bridge/native_qa.py", ROOT / "tools/probe_audio.py"]},
              "capture_policy": {"physical_microphone_opened": False, "system_volume_changed": False, "owned_generated_audio_only": True, "maximum_capture_seconds": MAX_CAPTURE_SECONDS},
              "commands": {}, "actual_audio_output_verified": False}
    for name, command, arguments in [("compile", "compile", []), ("lint", "lint", ["--error-code"])]:
        execution = run_sdk(sdk, target, command, [*arguments, "--savedir", str(run_dir / "private-saves")], timeout_seconds=60,
                            env={"RENPY_MULTIPERSISTENT": str(run_dir / "private-multipersistent")})
        report["commands"][name] = asdict(execution)
        save_report(run_dir, report)
        if execution.exit_code != 0 or execution.timed_out or not execution.cleanup_complete:
            print(json.dumps({"report_path": str(run_dir / "report.json"), "complete": False}, ensure_ascii=False))
            return 1
    # The audio driver and engine run inside this owned process tree. A stalled
    # WASAPI call is terminated together with the proven SDK descendants.
    child = _execute([sys.executable, str(Path(__file__).resolve()), "--capture-child", str(run_dir)], ROOT, 90, None)
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    report["capture_process"] = asdict(child)
    if child.exit_code != 0 or child.timed_out or not child.cleanup_complete:
        report["complete"] = False
    save_report(run_dir, report)
    print(json.dumps({"report_path": str(run_dir / "report.json"), "complete": report["complete"]}, ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
