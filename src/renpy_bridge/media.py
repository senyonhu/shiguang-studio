"""Read-only validation for media copied into the managed asset container.

The caller owns path containment and symlink checks. This module never copies,
plays, executes, or writes a file. Compressed audio validation checks metadata
and container/frame completeness; native Ren'Py decoding remains a separate QA.
"""

from __future__ import annotations

import math
import warnings
import wave
from pathlib import Path

from fontTools.ttLib import TTFont
from mutagen.mp3 import MP3, MPEGFrame
from mutagen.ogg import OggPage
from mutagen.oggvorbis import OggVorbis
from PIL import Image

from .errors import BridgeError


ALLOWED_EXTENSIONS = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"}),
    "font": frozenset({".ttf", ".otf"}),
    "audio": frozenset({".wav", ".ogg", ".mp3"}),
}
_IMAGE_FORMATS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".bmp": "BMP"}


def _mismatch(path: Path, expected: str, actual: str) -> None:
    raise BridgeError("INVALID_ASSET_FORMAT", "素材扩展名与实际格式不一致", path=str(path),
                      expected=expected, actual=actual)


def _image(path: Path) -> dict:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            actual = image.format
            expected = _IMAGE_FORMATS[path.suffix.lower()]
            if actual != expected:
                _mismatch(path, expected, str(actual))
            image.load()
            if image.width <= 0 or image.height <= 0:
                raise ValueError("empty image")
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            box = image.convert("RGBA").getchannel("A").getbbox() if has_alpha else None
            return {"format": actual, "width": image.width, "height": image.height,
                    "mode": image.mode, "has_alpha": has_alpha,
                    "visible_bbox": list(box) if box else None}


def _font(path: Path) -> dict:
    # WOFF, collections, and renamed outline formats are not native font assets.
    expected = "TrueType" if path.suffix.lower() == ".ttf" else "OpenType-CFF"
    with TTFont(str(path), lazy=False, checkChecksums=2, ignoreDecompileErrors=False) as font:
        actual = "OpenType-CFF" if font.sfntVersion == "OTTO" else "TrueType"
        if font.flavor is not None or font.sfntVersion not in {"\x00\x01\x00\x00", "true", "OTTO"}:
            _mismatch(path, expected, str(font.flavor or font.sfntVersion))
        if actual != expected:
            _mismatch(path, expected, actual)
        file_size = path.stat().st_size
        if any(table.offset + table.length > file_size for table in font.reader.tables.values()):
            raise ValueError("truncated font table")
        required = {"head", "hhea", "hmtx", "maxp", "cmap", "name"}
        required |= {"glyf", "loca"} if actual == "TrueType" else set()
        if not required.issubset(font.keys()) or (actual == "OpenType-CFF" and not {"CFF ", "CFF2"}.intersection(font.keys())):
            raise ValueError("missing font tables")
        font.ensureDecompiled()
        cmap = font.getBestCmap()
        glyphs = font.getGlyphOrder()
        if not cmap or not glyphs or not set(cmap.values()).issubset(glyphs):
            raise ValueError("font has no valid Unicode cmap")
        if font["maxp"].numGlyphs != len(glyphs):
            raise ValueError("invalid glyph count")
        names = font["name"]
        return {"format": actual, "family": names.getDebugName(1) or "",
                "style": names.getDebugName(2) or "", "glyph_count": len(glyphs),
                "unicode_codepoint_count": len(cmap)}


def _wav(path: Path) -> dict:
    with path.open("rb") as source:
        header = source.read(12)
    if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
        raise ValueError("missing RIFF/WAVE header")
    if int.from_bytes(header[4:8], "little") + 8 != path.stat().st_size:
        raise ValueError("incomplete or extra RIFF data")
    with wave.open(str(path), "rb") as audio:
        channels, rate, width, count = audio.getnchannels(), audio.getframerate(), audio.getsampwidth(), audio.getnframes()
        if channels <= 0 or rate <= 0 or width not in {1, 2, 3, 4} or count <= 0 or audio.getcomptype() != "NONE":
            raise ValueError("invalid PCM stream parameters")
        # Reading every declared PCM byte catches a valid-looking header with a
        # missing tail; chunks are bounded even for a long voice recording.
        remaining = count
        while remaining:
            requested = min(remaining, 65536)
            if len(audio.readframes(requested)) != requested * channels * width:
                raise ValueError("truncated PCM frames")
            remaining -= requested
        if audio.readframes(1):
            raise ValueError("unaligned extra PCM bytes")
    return {"format": "WAV", "codec": "PCM", "channels": channels, "sample_rate": rate,
            "sample_width": width, "frame_count": count, "duration": count / rate,
            "duration_seconds": count / rate}


def _ogg_complete(path: Path) -> None:
    # Mutagen provides page parsing and checksum encoding. Compare its read-only
    # byte representation with the original page; never call a file save API.
    streams = {}
    with path.open("rb") as source:
        while source.tell() < path.stat().st_size:
            start = source.tell()
            page = OggPage(source)
            end = source.tell()
            source.seek(start)
            if page.write() != source.read(end - start):
                raise ValueError("invalid Ogg page checksum or lacing")
            previous = streams.get(page.serial)
            if previous is None:
                if not page.first or page.sequence != 0 or not page.packets or not page.packets[0].startswith(b"\x01vorbis"):
                    raise ValueError("expected Vorbis stream beginning")
            elif previous.last or page.first or page.sequence != previous.sequence + 1 or page.continued != (not previous.complete):
                raise ValueError("broken Ogg page sequence")
            streams[page.serial] = page
    if not streams or any(not page.last or not page.complete or page.position <= 0 for page in streams.values()):
        raise ValueError("missing complete Ogg end-of-stream")


def _mp3_complete(path: Path, info) -> int:
    # MPEGFrame is a pinned Mutagen 1.47.0 parser, not a PCM decoder. Its seek
    # result exposes a frame whose declared payload extends beyond the file.
    end = path.stat().st_size
    frames = 0
    with path.open("rb") as source:
        if end >= 128:
            source.seek(end - 128)
            if source.read(3) == b"TAG":
                end -= 128
        source.seek(info.frame_offset)
        while source.tell() < end:
            start = source.tell()
            frame = MPEGFrame(source)
            if source.tell() <= start or source.tell() > end:
                raise ValueError("truncated MPEG frame")
            if frame.layer != 3 or (frame.version, frame.sample_rate, frame.channels) != (info.version, info.sample_rate, info.channels):
                raise ValueError("inconsistent MPEG Layer III stream")
            frames += 1
    if not frames:
        raise ValueError("no complete MPEG frames")
    return frames


def _compressed_audio(path: Path) -> dict:
    if path.suffix.lower() == ".ogg":
        audio = OggVorbis(str(path))
        _ogg_complete(path)
        actual, codec = "OGG", "Vorbis"
        extra = {}
    else:
        audio = MP3(str(path))
        if audio.info.layer != 3 or audio.info.sketchy:
            raise ValueError("not a recognized MPEG Layer III stream")
        extra = {"frame_count": _mp3_complete(path, audio.info)}
        actual, codec = "MP3", "MPEG Layer III"
    info = audio.info
    duration = float(info.length)
    if not math.isfinite(duration) or duration <= 0 or info.channels <= 0 or info.sample_rate <= 0 or info.bitrate <= 0:
        raise ValueError("invalid compressed stream metadata")
    return {"format": actual, "codec": codec, "channels": int(info.channels),
            "sample_rate": int(info.sample_rate), "bitrate": int(info.bitrate),
            "duration": duration, "duration_seconds": duration,
            "metadata_parser": "mutagen-1.47.0", **extra}


def validate_media(kind: str, path: Path | str, metadata: dict | None = None) -> dict:
    """Return caller metadata overlaid with facts read from an allowed medium.

    Suffix matching is case insensitive. JPEG .jpg/.jpeg are equivalent; all
    other image/font suffixes must match the decoded format. .ogg supports
    Vorbis streams, .mp3 supports MPEG Layer III, .wav supports complete PCM.
    Script/executable suffixes are rejected for every kind before file parsing.
    """
    path = Path(path)
    if kind not in ALLOWED_EXTENSIONS or path.suffix.lower() not in ALLOWED_EXTENSIONS[kind]:
        raise BridgeError("INVALID_ASSET_FORMAT", "该素材类型不允许此文件扩展名", kind=kind, path=str(path),
                          extension=path.suffix.lower(), allowed=sorted(ALLOWED_EXTENSIONS.get(kind, ())))
    try:
        if kind == "image":
            facts = _image(path)
        elif kind == "font":
            facts = _font(path)
        else:
            facts = _wav(path) if path.suffix.lower() == ".wav" else _compressed_audio(path)
    except BridgeError:
        raise
    except Exception as exc:
        raise BridgeError({"image": "INVALID_IMAGE", "font": "INVALID_FONT", "audio": "INVALID_AUDIO"}[kind],
                          "素材内容无法完整解析", path=str(path), reason=str(exc)) from exc
    return {**(metadata or {}), **facts}
