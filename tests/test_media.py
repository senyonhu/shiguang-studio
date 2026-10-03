"""Media import validation: source bytes stay unchanged and code cannot be media.

Optional real compressed-audio cases use the pinned local SDK OGG and encode our
own Chinese SAPI recording with an already installed FFmpeg; no audio is played.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import wave
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PIL import Image

from renpy_bridge.errors import BridgeError
from renpy_bridge.media import validate_media


ROOT = Path(__file__).resolve().parents[1]
SDK_OGG = ROOT / "runtimes/renpy-8.5.3/tutorial/game/tower_clock.ogg"
SDK_FONT = ROOT / "runtimes/renpy-8.5.3/launcher/game/fonts/Roboto-Regular.ttf"
OWN_VOICE = ROOT / "evidence/m0-audio/runs/20261001T211735Z-fa0b6f4a/chinese-test-voice.wav"
OGG_SHA256 = "559d5b3a2d46db92e239804741ae2c3e050a717339615dc3f501be794bda5d27"
FONT_SHA256 = "bde8a188e37aa936b167aecc5e5a3da40262f6e51fd54c584f2cf2b6b99d96ca"
VOICE_SHA256 = "ca3488ffc5edbf6a66db528252fe27d84e538b82a098a0f087f0e71687f30c47"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rejects(kind, path, code):
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(BridgeError) as error:
        validate_media(kind, path, {})
    assert error.value.code == code
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("kind", ["image", "font", "audio"])
@pytest.mark.parametrize("suffix", [".rpy", ".rpyc", ".py", ".exe", ".txt", ".Rpy"])
def test_control_and_unapproved_suffixes_rejected_before_read(kind, suffix, tmp_path):
    # No file is needed: extension rejection happens before content parsing.
    rejects(kind, tmp_path / ("controlled_source" + suffix), "INVALID_ASSET_FORMAT")


@pytest.mark.parametrize("kind,suffix,code", [
    ("image", ".png", "INVALID_IMAGE"), ("image", ".jpg", "INVALID_IMAGE"),
    ("image", ".jpeg", "INVALID_IMAGE"), ("image", ".webp", "INVALID_IMAGE"),
    ("image", ".bmp", "INVALID_IMAGE"), ("font", ".ttf", "INVALID_FONT"),
    ("font", ".otf", "INVALID_FONT"), ("audio", ".wav", "INVALID_AUDIO"),
    ("audio", ".ogg", "INVALID_AUDIO"), ("audio", ".mp3", "INVALID_AUDIO"),
])
def test_script_content_with_allowed_media_suffix_is_rejected(kind, suffix, code, tmp_path):
    path = tmp_path / ("forged" + suffix)
    path.write_text('init python:\n    raise RuntimeError("must not execute")\n', encoding="utf-8")
    rejects(kind, path, code)


@pytest.mark.parametrize("suffix,format_name", [
    (".png", "PNG"), (".jpg", "JPEG"), (".jpeg", "JPEG"),
    (".webp", "WEBP"), (".bmp", "BMP"), (".PNG", "PNG"),
])
def test_actual_images_and_fact_metadata_override(suffix, format_name, tmp_path):
    path = tmp_path / ("图像素材" + suffix)
    Image.new("RGB", (31, 19), (80, 120, 200)).save(path, format=format_name)
    before, stat = digest(path), path.stat()
    metadata = {"width": 9999, "has_alpha": True, "format": "FAKE", "custom": "保留"}
    result = validate_media("image", path, metadata)
    assert result["format"] == format_name
    assert (result["width"], result["height"], result["has_alpha"]) == (31, 19, False)
    assert result["custom"] == "保留" and metadata["width"] == 9999
    assert digest(path) == before and path.stat().st_mtime_ns == stat.st_mtime_ns


def test_png_renamed_jpeg_is_rejected(tmp_path):
    path = tmp_path / "renamed.jpg"
    Image.new("RGB", (10, 10)).save(path, format="PNG")
    rejects("image", path, "INVALID_ASSET_FORMAT")


def test_alpha_bbox_and_palette_transparency(tmp_path):
    path = tmp_path / "sprite.png"
    image = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    image.putpixel((2, 3), (255, 0, 0, 255))
    image.save(path)
    assert validate_media("image", path)["visible_bbox"] == [2, 3, 3, 4]
    image = Image.new("P", (10, 10), 0)
    image.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
    image.putpixel((4, 5), 1)
    image.save(path, transparency=0)
    result = validate_media("image", path)
    assert result["has_alpha"] and result["visible_bbox"] == [4, 5, 5, 6]


def test_fully_transparent_image_has_empty_visible_bbox(tmp_path):
    path = tmp_path / "empty.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(path)
    result = validate_media("image", path)
    assert result["has_alpha"] and result["visible_bbox"] is None


def make_font(path, *, cff=False):
    builder = FontBuilder(1000, isTTF=not cff)
    glyphs = [".notdef", "A"]
    builder.setupGlyphOrder(glyphs)
    builder.setupCharacterMap({65: "A"})
    if cff:
        charstrings = {}
        for glyph in glyphs:
            pen = T2CharStringPen(600, None)
            charstrings[glyph] = pen.getCharString()
        builder.setupCFF("MediaFixture-Regular", {"FullName": "Media Fixture Regular"}, charstrings, {})
    else:
        builder.setupGlyf({glyph: TTGlyphPen(None).glyph() for glyph in glyphs})
    builder.setupHorizontalMetrics({glyph: (600, 0) for glyph in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Media Fixture", "styleName": "Regular",
                            "psName": "MediaFixture-Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


@pytest.mark.parametrize("suffix,cff,format_name", [(".ttf", False, "TrueType"), (".otf", True, "OpenType-CFF")])
def test_real_font_tables_and_unicode_cmap(suffix, cff, format_name, tmp_path):
    path = tmp_path / ("fixture" + suffix)
    make_font(path, cff=cff)
    before = digest(path)
    result = validate_media("font", path, {"glyph_count": 999, "custom": "retained"})
    assert result["format"] == format_name and result["family"] == "Media Fixture"
    assert result["glyph_count"] == 2 and result["unicode_codepoint_count"] == 1
    assert result["custom"] == "retained" and digest(path) == before


def test_font_without_cmap_and_truncated_tables_are_rejected(tmp_path):
    path = tmp_path / "font.ttf"
    make_font(path)
    good = path.read_bytes()
    path.write_bytes(good[:-10])
    rejects("font", path, "INVALID_FONT")
    path.write_bytes(good)
    with TTFont(path) as font:
        del font["cmap"]
        font.save(tmp_path / "no-cmap.ttf")
    rejects("font", tmp_path / "no-cmap.ttf", "INVALID_FONT")


def test_font_outline_format_must_match_suffix(tmp_path):
    path = tmp_path / "renamed.otf"
    make_font(path, cff=False)
    rejects("font", path, "INVALID_ASSET_FORMAT")


def make_wav(path, *, frames=1600):
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x01\x00" * frames)


def test_complete_wav_payload_and_metadata(tmp_path):
    path = tmp_path / "voice.wav"
    make_wav(path)
    before = digest(path)
    result = validate_media("audio", path, {"duration": 999})
    assert result["format"] == "WAV" and result["codec"] == "PCM"
    assert (result["channels"], result["sample_rate"], result["sample_width"], result["frame_count"]) == (1, 16000, 2, 1600)
    assert result["duration"] == result["duration_seconds"] == .1
    assert digest(path) == before


@pytest.mark.parametrize("damage", ["header", "tail", "tail_with_riff_size_updated", "extra_incomplete_frame", "empty"])
def test_wav_header_and_complete_declared_samples(damage, tmp_path):
    path = tmp_path / "broken.wav"
    make_wav(path, frames=0 if damage == "empty" else 1600)
    data = bytearray(path.read_bytes())
    if damage == "header":
        data[8:12] = b"FAKE"
    elif damage in {"tail", "tail_with_riff_size_updated"}:
        del data[-3:]
        if damage.endswith("updated"):
            data[4:8] = (len(data) - 8).to_bytes(4, "little")
    elif damage == "extra_incomplete_frame":
        data += b"\x01"
        data[4:8] = (len(data) - 8).to_bytes(4, "little")
        data[40:44] = (len(data) - 44).to_bytes(4, "little")
    path.write_bytes(data)
    rejects("audio", path, "INVALID_AUDIO")


@pytest.fixture
def actual_sdk_ogg():
    if not SDK_OGG.is_file():
        pytest.skip("pinned SDK media is not installed")
    assert digest(SDK_OGG) == OGG_SHA256
    return SDK_OGG


def test_actual_sdk_vorbis_and_font_are_read_only(actual_sdk_ogg):
    result = validate_media("audio", actual_sdk_ogg)
    assert (result["format"], result["codec"], result["channels"], result["sample_rate"]) == ("OGG", "Vorbis", 1, 11025)
    assert result["duration"] == pytest.approx(2.0057142857)
    assert digest(actual_sdk_ogg) == OGG_SHA256
    assert digest(SDK_FONT) == FONT_SHA256
    font = validate_media("font", SDK_FONT)
    assert font["family"] == "Roboto" and font["unicode_codepoint_count"] > 800
    assert digest(SDK_FONT) == FONT_SHA256


@pytest.mark.parametrize("damage", ["truncate", "checksum"])
def test_actual_ogg_container_damage_is_rejected(actual_sdk_ogg, damage, tmp_path):
    path = tmp_path / "broken.ogg"
    data = bytearray(actual_sdk_ogg.read_bytes())
    if damage == "truncate":
        del data[-10:]
    else:
        data[-1] ^= 1
    path.write_bytes(data)
    rejects("audio", path, "INVALID_AUDIO")


@pytest.fixture(scope="module")
def own_speech_mp3(tmp_path_factory):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not OWN_VOICE.is_file():
        pytest.skip("local FFmpeg and own SAPI speech fixture required")
    assert digest(OWN_VOICE) == VOICE_SHA256
    path = tmp_path_factory.mktemp("media-mp3") / "own-chinese-speech.mp3"
    completed = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(OWN_VOICE),
                                "-map_metadata", "-1", "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
                               capture_output=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert digest(OWN_VOICE) == VOICE_SHA256
    return path


def test_actual_owned_speech_mp3_metadata_and_complete_frames(own_speech_mp3):
    before = digest(own_speech_mp3)
    result = validate_media("audio", own_speech_mp3)
    assert result["format"] == "MP3" and result["codec"] == "MPEG Layer III"
    assert result["channels"] == 1 and result["sample_rate"] == 32000
    # MPEG frame alignment adds encoder padding; metadata is stream duration,
    # rather than a claim that compressed samples equal the original WAV count.
    assert 2.6445 <= result["duration"] <= 2.7445
    assert result["frame_count"] > 60 and digest(own_speech_mp3) == before


def test_actual_mp3_truncated_final_frame_is_rejected(own_speech_mp3, tmp_path):
    path = tmp_path / "truncated.mp3"
    path.write_bytes(own_speech_mp3.read_bytes()[:-3])
    rejects("audio", path, "INVALID_AUDIO")


def test_other_audio_contents_cannot_be_renamed_mp3_or_ogg(actual_sdk_ogg, tmp_path):
    path = tmp_path / "renamed.mp3"
    path.write_bytes(actual_sdk_ogg.read_bytes())
    rejects("audio", path, "INVALID_AUDIO")
    path = tmp_path / "renamed.ogg"
    make_wav(path)
    rejects("audio", path, "INVALID_AUDIO")
