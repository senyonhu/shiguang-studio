"""Probe local Chinese speech synthesis and actual Windows speaker loopback.

Only the generated short test speech is played. System output is captured only
in that playback window with a 0.25-second tail. No physical microphone is
opened and no user/device volume is changed. Native audio work runs in a hidden,
bounded child process so a driver problem cannot leave a recording running.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
import uuid
import warnings
import wave


DEFAULT_TEXT = "这是中文语音测试。"
TAIL_SECONDS = 0.25
CAPTURE_RATE = 48000
MAX_SPEECH_SECONDS = 10.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def hidden_process_options() -> dict:
    if os.name != "nt":
        return {}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    return {
        "startupinfo": startup,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


def signal_metrics(samples, np) -> dict:
    values = np.asarray(samples, dtype=np.float64)
    finite = bool(np.isfinite(values).all())
    if not values.size or not finite:
        return {
            "finite": finite, "peak": 0.0, "rms": 0.0,
            "active_fraction": 0.0, "non_silent": False,
        }
    peak = float(np.max(np.abs(values)))
    rms = float(np.sqrt(np.mean(values * values)))
    fraction = float(np.mean(np.abs(values) > 1e-5))
    return {
        "finite": finite,
        "peak": peak,
        "rms": rms,
        "active_fraction": fraction,
        "non_silent": bool(peak > 1e-4 and rms > 1e-6 and fraction > 0.001),
    }


def read_wav(path: Path, np):
    with wave.open(str(path), "rb") as stream:
        channels = stream.getnchannels()
        width = stream.getsampwidth()
        rate = stream.getframerate()
        frames = stream.getnframes()
        compression = stream.getcomptype()
        raw = stream.readframes(frames)
    if compression != "NONE":
        raise ValueError(f"Unsupported compressed WAV: {compression}")
    if width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128
    elif width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768
    elif width == 3:
        bytes24 = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        integer = bytes24[:, 0] | (bytes24[:, 1] << 8) | (bytes24[:, 2] << 16)
        integer = np.where(integer >= 0x800000, integer - 0x1000000, integer)
        samples = integer.astype(np.float64) / 8388608
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648
    else:
        raise ValueError(f"Unsupported PCM sample width: {width}")
    samples = samples.reshape(-1, channels)
    metadata = {
        "path": str(path.resolve()),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "channels": channels,
        "sample_width_bytes": width,
        "sample_rate": rate,
        "frames": frames,
        "duration_seconds": frames / rate if rate else 0.0,
        "compression": compression,
        **signal_metrics(samples, np),
    }
    return samples, metadata


def save_pcm16(path: Path, samples, rate: int, np) -> None:
    pcm = np.round(np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(pcm.shape[1])
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm.tobytes())


def envelope_match(source, source_rate: int, captured, np) -> dict:
    """Report a rough source/capture envelope match; not speech recognition."""
    def envelope(data, rate):
        mono = np.mean(data, axis=1)
        block = max(1, int(rate * 0.02))
        usable = len(mono) // block * block
        if usable == 0:
            return np.empty(0)
        return np.sqrt(np.mean(mono[:usable].reshape(-1, block) ** 2, axis=1))
    reference = envelope(source, source_rate)
    recording = envelope(captured, CAPTURE_RATE)
    best = None
    lag_at_best = None
    for lag in range(-5, 61):
        a_start = max(0, -lag)
        b_start = max(0, lag)
        length = min(len(reference) - a_start, len(recording) - b_start)
        if length < max(8, int(len(reference) * 0.75)):
            continue
        a = reference[a_start:a_start + length]
        b = recording[b_start:b_start + length]
        if np.std(a) < 1e-9 or np.std(b) < 1e-9:
            continue
        value = float(np.corrcoef(a, b)[0, 1])
        if math.isfinite(value) and (best is None or value > best):
            best, lag_at_best = value, lag * 0.02
    return {
        "method": "20ms RMS-envelope Pearson correlation; diagnostic only",
        "correlation": best,
        "lag_seconds": lag_at_best,
    }


def capture_child(source_path: Path, run_dir: Path) -> int:
    result: dict = {
        "status": "unverified",
        "scope": "Default speaker loopback only; no physical microphone",
        "started_at_utc": utc_now(),
        "tail_seconds": TAIL_SECONDS,
        "volume_changed": False,
        "loopback_devices": [],
    }
    result_path = run_dir / "loopback.json"
    captured = []
    recorded_warnings = []
    try:
        import numpy as np
        import soundcard as sc
        if os.name != "nt":
            raise RuntimeError("This probe targets Windows WASAPI.")
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("No default speaker.")
        result["default_speaker"] = {
            "id": str(speaker.id), "name": speaker.name, "channels": speaker.channels,
        }
        loopbacks = [
            device for device in sc.all_microphones(include_loopback=True)
            if getattr(device, "isloopback", False)
        ]
        result["loopback_devices"] = [
            {"id": str(device.id), "name": device.name, "channels": device.channels}
            for device in loopbacks
        ]
        matching = [
            device for device in loopbacks if str(device.id) == str(speaker.id)
        ]
        if not matching:
            raise RuntimeError("No loopback for the actual default speaker.")
        microphone = matching[0]
        if microphone.channels < 2:
            raise RuntimeError(
                "Mono WASAPI loopback is unverified due to SoundCard's documented single-channel issue."
            )
        if not source_path.is_file():
            raise RuntimeError("No generated test WAV; devices listed without recording.")
        source, metadata = read_wav(source_path, np)
        duration = metadata["duration_seconds"]
        if not metadata["non_silent"] or not 0 < duration <= MAX_SPEECH_SECONDS:
            raise RuntimeError("Test speech must be non-silent and at most 10 seconds.")

        result["capture_rate"] = CAPTURE_RATE
        result["input_sha256"] = metadata["sha256"]
        result["capture_limit_seconds"] = duration + TAIL_SECONDS + 1.0
        write_json(result_path, result)
        done = threading.Event()
        playback_state: dict = {}

        def playback() -> None:
            import winsound
            try:
                playback_state["started_at_utc"] = utc_now()
                playback_state["start_monotonic"] = time.monotonic()
                winsound.PlaySound(
                    str(source_path),
                    winsound.SND_FILENAME | winsound.SND_NODEFAULT,
                )
                playback_state["status"] = "played"
            except Exception as error:
                playback_state["status"] = "failed"
                playback_state["error"] = f"{type(error).__name__}: {error}"
            finally:
                playback_state["finished_at_utc"] = utc_now()
                playback_state["end_monotonic"] = time.monotonic()
                done.set()

        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always")
            # SoundCard initializes COM on this main thread. Keep recorder here;
            # winsound playback needs no shared COM pointers.
            with microphone.recorder(
                samplerate=CAPTURE_RATE,
                channels=list(range(microphone.channels)),
                blocksize=2048,
            ) as recorder:
                result["capture_started_at_utc"] = utc_now()
                start = time.monotonic()
                thread = threading.Thread(target=playback, name="probe-test-playback", daemon=True)
                thread.start()
                saved_frames = 0
                max_frames = int((duration + TAIL_SECONDS + 1.0) * CAPTURE_RATE)
                while time.monotonic() - start < duration + TAIL_SECONDS + 1.0:
                    if done.is_set() and (
                        time.monotonic() - playback_state["end_monotonic"] >= TAIL_SECONDS
                    ):
                        break
                    chunk = recorder.record(numframes=None)
                    if chunk.size and saved_frames < max_frames:
                        chunk = chunk[:max_frames - saved_frames].copy()
                        captured.append(chunk)
                        saved_frames += len(chunk)
                    elif not chunk.size:
                        time.sleep(0.005)
                result["capture_wall_seconds"] = time.monotonic() - start
            result["capture_finished_at_utc"] = utc_now()
            thread.join(timeout=0.2)
            recorded_warnings = list(dict.fromkeys(str(item.message) for item in observed))
        result["playback"] = {
            key: value for key, value in playback_state.items()
            if not key.endswith("_monotonic")
        }
        result["recorder_closed"] = True
        result["warnings"] = recorded_warnings
        if not done.is_set():
            raise RuntimeError("Playback exceeded the bounded test window.")
        if playback_state.get("status") != "played":
            raise RuntimeError(playback_state.get("error", "Playback failed."))
        if not captured:
            raise RuntimeError("Loopback returned no samples.")
        data = np.concatenate(captured, axis=0)
        output = run_dir / "speaker-loopback.wav"
        save_pcm16(output, data, CAPTURE_RATE, np)
        _, output_meta = read_wav(output, np)
        result["wav"] = output_meta
        result["source_envelope_match"] = envelope_match(
            source, metadata["sample_rate"], data, np
        )
        result["status"] = "passed" if output_meta["non_silent"] else "failed"
        if result["status"] != "passed":
            result["error"] = "Actual default-speaker loopback was silent."
        result["interpretation"] = (
            "Non-silence verifies output activity during generated speech playback; "
            "this is not speech recognition or a RenPy runtime audio test."
        )
    except (ImportError, RuntimeError) as error:
        result["error"] = f"{type(error).__name__}: {error}"
        result["status"] = "unverified"
    except Exception as error:
        result["error"] = f"{type(error).__name__}: {error}"
        result["status"] = "failed"
    result["finished_at_utc"] = utc_now()
    write_json(result_path, result)
    return 0 if result["status"] == "passed" else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--voice-name", default="")
    parser.add_argument("--capture-child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.capture_child:
        if args.run_dir is None:
            parser.error("--capture-child requires --run-dir")
        return capture_child(args.capture_child.resolve(), args.run_dir.resolve())
    project_root = Path(__file__).resolve().parent.parent
    output_root = (args.output_root or project_root / "evidence" / "m0-audio").resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = output_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "schema_version": "1.0",
        "probe": "M0 Chinese TTS and own-playback loopback",
        "formal_demo": False,
        "run_id": run_id,
        "started_at_utc": utc_now(),
        "text": args.text,
        "status": "unverified",
        "dependencies": {
            "numpy": package_version("numpy"), "soundcard": package_version("soundcard"),
        },
        "scope": {
            "physical_microphone_opened": False,
            "user_volume_changed": False,
            "capture_only_for_generated_playback": True,
            "maximum_speech_seconds": MAX_SPEECH_SECONDS,
            "post_playback_tail_seconds": TAIL_SECONDS,
        },
        "script_sha256": sha256(Path(__file__).resolve()),
        "tts": {"status": "unverified"},
        "loopback": {"status": "unverified"},
    }
    source_path = run_dir / "chinese-test-voice.wav"
    try:
        if os.name != "nt":
            raise RuntimeError("Windows System.Speech is required.")
        powershell = Path(
            os.environ.get("SystemRoot", r"C:\Windows")
        ) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        helper = project_root / "tools" / "probe_voice.ps1"
        command = [
            str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(helper),
            "-OutputPath", str(source_path),
            "-TextBase64", base64.b64encode(args.text.encode("utf-8")).decode("ascii"),
        ]
        if args.voice_name:
            command.extend(["-VoiceName", args.voice_name])
        tts_process = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=25, **hidden_process_options(),
        )
        (run_dir / "tts-stderr.txt").write_text(tts_process.stderr, encoding="utf-8")
        report["tts"] = json.loads(tts_process.stdout.lstrip("\ufeff"))
        report["tts"]["exit_code"] = tts_process.returncode
        report["tts"]["helper_sha256"] = sha256(helper)
        if report["tts"]["status"] == "generated" and source_path.is_file():
            import numpy as np
            _, metadata = read_wav(source_path, np)
            report["tts"]["wav"] = metadata
            report["tts"]["status"] = "passed" if metadata["non_silent"] else "failed"
            if not metadata["non_silent"]:
                report["tts"]["error"] = "Generated Chinese speech WAV is silent."
        capture_process = subprocess.run(
            [
                sys.executable, str(Path(__file__).resolve()),
                "--capture-child", str(source_path), "--run-dir", str(run_dir),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=MAX_SPEECH_SECONDS + TAIL_SECONDS + 8,
            **hidden_process_options(),
        )
        (run_dir / "loopback-stderr.txt").write_text(capture_process.stderr, encoding="utf-8")
        loopback_path = run_dir / "loopback.json"
        if loopback_path.is_file():
            report["loopback"] = json.loads(loopback_path.read_text(encoding="utf-8"))
        report["loopback"]["exit_code"] = capture_process.returncode
        if report["tts"].get("status") == "passed" and report["loopback"].get("status") == "passed":
            report["status"] = "passed"
        elif report["tts"].get("status") == "failed" or report["loopback"].get("status") == "failed":
            report["status"] = "failed"
        elif report["tts"].get("status") == "passed":
            report["status"] = "partial"
    except subprocess.TimeoutExpired as error:
        # subprocess.run kills and waits for only its own child on timeout.
        report["error"] = f"Owned probe process timed out after {error.timeout} seconds."
        report["status"] = "failed"
        loopback_path = run_dir / "loopback.json"
        if loopback_path.is_file():
            report["loopback"] = json.loads(loopback_path.read_text(encoding="utf-8"))
            report["loopback"]["status"] = "failed"
            report["loopback"]["error"] = "Owned capture process timed out and was terminated."
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        report["status"] = "failed"
    report["finished_at_utc"] = utc_now()
    report["run_dir"] = str(run_dir)
    report["report_path"] = str(output_root / "report.json")
    write_json(run_dir / "report.json", report)
    write_json(output_root / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())

