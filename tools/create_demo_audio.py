"""Generate original demo music/SFX and actual installed Chinese SAPI speech."""
from pathlib import Path
import base64
import hashlib
import json
import math
import subprocess
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MATERIALS = ROOT / "evidence/m8-demo/materials"


def wav(path, signal, rate=32000):
    pcm = np.clip(signal * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        output.writeframes(pcm.tobytes())


def describe(path):
    with wave.open(str(path), "rb") as stream:
        rate, frames = stream.getframerate(), stream.getnframes()
        samples = np.frombuffer(stream.readframes(frames), dtype="<i2") / 32768
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "sample_rate": rate, "duration_seconds": frames / rate,
                "channels": stream.getnchannels(), "rms": float(np.sqrt(np.mean(samples ** 2))),
                "non_silent": bool(np.max(np.abs(samples)) > 0.01)}


def main():
    folder = MATERIALS / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    lines = json.loads((MATERIALS / "voice-lines.json").read_text(encoding="utf-8"))["lines"]
    voices = []
    for line in lines:
        path = folder / Path(line["relative_path"]).name
        if path.exists():
            raise RuntimeError(f"Refuse to overwrite an existing generated voice: {path}")
        encoded = base64.b64encode(line["text"].encode()).decode()
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(ROOT / "tools/probe_voice.ps1"), "-OutputPath", str(path),
                                 "-TextBase64", encoded], capture_output=True, timeout=60,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace"))
        metadata = json.loads(result.stdout.decode("utf-8-sig"))
        voices.append({**line, **describe(path), "generation": metadata, "method": "installed SAPI Chinese TTS"})
    rate = 32000
    # Original quiet eight-note melody, a synthetic bell timbre. No recorded
    # copyrighted music, and no synthetic tone substitutes for speech.
    notes = [60, 64, 67, 72, 69, 67, 64, 62]
    music = np.zeros(rate * 12)
    for index, note in enumerate(notes):
        t = np.arange(rate * 3) / rate
        frequency = 440 * 2 ** ((note - 69) / 12)
        sound = sum(math.exp(-h) * np.sin(2 * np.pi * frequency * h * t) for h in range(1, 5))
        sound *= (1 - np.exp(-t * 30)) * np.exp(-t * 1.5) * 0.25
        start = int(index * 1.25 * rate)
        end = min(start + len(sound), len(music))
        music[start:end] += sound[:end-start]
    music *= np.minimum(np.arange(len(music)) / (rate * 0.2), 1)
    music *= np.minimum(np.arange(len(music))[::-1] / (rate * 0.8), 1)
    wav(folder / "bgm.wav", music)
    t = np.arange(int(rate * 0.8)) / rate
    chime = 0.15 * (np.sin(2 * np.pi * 880 * t) + 0.5 * np.sin(2 * np.pi * 1320 * t))
    chime *= (1 - np.exp(-t * 80)) * np.exp(-t * 7)
    wav(folder / "chime.wav", chime)
    report = {"status": "generated", "voices": voices,
              "music": {**describe(folder / "bgm.wav"), "method": "original eight-note synthetic bell melody", "midi_notes": notes},
              "sfx": {**describe(folder / "chime.wav"), "method": "original two-frequency chime"},
              "engine_playback_verification": "not_run", "physical_audio_verification": "not_run"}
    (MATERIALS / "audio-generation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "voices": len(voices), "report": str(MATERIALS / "audio-generation.json")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
