"""Can this upload become a video at all? (JR Content, P1-B26)

A conversion used to find out only when it ran, and it treated every failure
the same way: three retries a minute apart, in a queue with one slot shared
with conversions that take five minutes or more. A text file named `.mp4`
therefore sat at "Converting" for 13 minutes before it was reported as failed.

Two questions are kept apart here, because they need opposite answers:

* the *file* is not a recording (no container ffmpeg recognises, no picture or
  sound, a codec nothing can decode, a cut-off MP4 with no index) -- that will
  never change, so it is refused at once and never retried;
* the *read* failed (storage unreachable, a timeout, a signed address refused)
  -- that says nothing about the file, so it is left to the normal, bounded
  retries and an upload is never refused because of it.

Only a positive sign of the first kind counts as unreadable. Anything not
recognised falls to the second, safe side.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from .redact import redact_presigned_urls

PLAYABLE = "playable"
UNREADABLE = "unreadable"
UNKNOWN = "unknown"

# What ffmpeg/ffprobe say about the bytes themselves.
_UNREADABLE_MARKERS = (
    "invalid data found when processing input",
    "moov atom not found",
    "decoder not found",
    "no decoder found",             # "Decoding requested, but no decoder found for: none"
    "unknown decoder",
    "could not find codec parameters",
    "does not contain any stream",
    "unsupported codec",
    "invalid frame dimensions",
)
# "Decoder (codec xyz) not found for input stream #0:0" names the codec in between.
_UNREADABLE_PATTERNS = (re.compile(r"decoder \([^)]*\) not found"),)
# What they say when the read itself went wrong. These win over the markers
# above: a dropped connection in the middle of a file can also leave "invalid
# data" behind, and that must stay retryable.
_TRANSIENT_MARKERS = (
    "server returned",
    "connection",
    "timed out",
    "timeout",
    "i/o error",
    "input/output error",
    "network is unreachable",
    "temporary failure",
    "name or service not known",
    "broken pipe",
    "resource temporarily unavailable",
    "http error",
    "cannot allocate memory",
    "out of memory",
)
# Demuxers that read something which is not a recording: a terminal text
# dump, and single still images (png_pipe, jpeg_pipe, svg_pipe, ...).
_NOT_RECORDINGS = frozenset({"tty", "image2"})

PROBE_TIMEOUT_SECONDS = 30
DECODE_TIMEOUT_SECONDS = 60


class UnreadableSource(Exception):
    """The uploaded file is not something that can be converted. Never retried."""


@dataclass(frozen=True)
class SourceCheck:
    verdict: str
    reason: str = ""

    @property
    def unreadable(self) -> bool:
        return self.verdict == UNREADABLE


def is_unreadable_source(error: str | None) -> bool:
    """True only when the error text positively says the bytes are not usable media."""
    lowered = (error or "").lower()
    if not lowered or any(marker in lowered for marker in _TRANSIENT_MARKERS):
        return False
    return any(marker in lowered for marker in _UNREADABLE_MARKERS) or any(
        pattern.search(lowered) for pattern in _UNREADABLE_PATTERNS
    )


def _format_names(data: dict) -> set[str]:
    raw = (data.get("format") or {}).get("format_name") or ""
    return {name.strip() for name in str(raw).split(",") if name.strip()}


def _not_a_recording(names: set[str]) -> bool:
    return bool(names) and all(name in _NOT_RECORDINGS or name.endswith("_pipe") for name in names)


def _first_stream(streams: list, codec_type: str) -> dict | None:
    for stream in streams:
        if not isinstance(stream, dict) or stream.get("codec_type") != codec_type:
            continue
        # A cover picture in an audio file is not a video track.
        if codec_type == "video" and int((stream.get("disposition") or {}).get("attached_pic") or 0):
            continue
        return stream
    return None


def _failed(stderr: str, what: str) -> SourceCheck:
    detail = redact_presigned_urls((stderr or "").strip())[-500:]
    if is_unreadable_source(stderr):
        return SourceCheck(UNREADABLE, detail or what)
    return SourceCheck(UNKNOWN, detail or what)


def check_media_source(
    url: str,
    *,
    probe_timeout: float = PROBE_TIMEOUT_SECONDS,
    decode_timeout: float = DECODE_TIMEOUT_SECONDS,
    run=subprocess.run,
) -> SourceCheck:
    """Probe the file and decode its first frame (or first audio frame).

    `url` is a presigned GET for the stored object; it never appears in the
    returned reason. A caller inside a request passes short timeouts: running
    out of time is UNKNOWN, never UNREADABLE. `run` is injectable for tests.
    """
    # ffmpeg's own network read limit (microseconds), below the process limit,
    # so a stalled read reports itself as a timeout instead of being killed.
    rw_timeout = str(int(max(1.0, min(probe_timeout, decode_timeout) - 0.5) * 1_000_000))
    probe_cmd = [
        "ffprobe", "-v", "error", "-rw_timeout", rw_timeout,
        "-print_format", "json", "-show_streams", "-show_format", url,
    ]
    try:
        probe = run(probe_cmd, capture_output=True, text=True, errors="replace", timeout=probe_timeout)
    except subprocess.TimeoutExpired:
        return SourceCheck(UNKNOWN, "ffprobe timed out")
    except OSError as exc:
        return SourceCheck(UNKNOWN, f"ffprobe could not run: {exc}")
    if probe.returncode != 0:
        return _failed(probe.stderr, "ffprobe failed")
    try:
        data = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        return SourceCheck(UNKNOWN, "ffprobe returned unreadable output")
    if not isinstance(data, dict):
        return SourceCheck(UNKNOWN, "ffprobe returned unreadable output")
    if _not_a_recording(_format_names(data)):
        return SourceCheck(UNREADABLE, "the file is a still image or text, not a recording")
    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    if video is None and audio is None:
        return SourceCheck(UNREADABLE, "the file has no picture or sound")

    # A container can be fine and its codec still undecodable here. Decoding
    # one frame is the only way to know; it reads only the start of the file.
    selector = ["-map", "0:V:0", "-frames:v", "1"] if video is not None else ["-map", "0:a:0", "-frames:a", "1"]
    decode_cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-rw_timeout", rw_timeout,
        "-i", url, *selector, "-f", "framecrc", "-",
    ]
    try:
        decoded = run(decode_cmd, capture_output=True, text=True, errors="replace", timeout=decode_timeout)
    except subprocess.TimeoutExpired:
        return SourceCheck(UNKNOWN, "decoding the first frame timed out")
    except OSError as exc:
        return SourceCheck(UNKNOWN, f"ffmpeg could not run: {exc}")
    if decoded.returncode != 0:
        return _failed(decoded.stderr, "decoding the first frame failed")
    frames = [line for line in (decoded.stdout or "").splitlines() if line.strip() and not line.startswith("#")]
    if not frames:
        # Exit 0 and nothing decoded: say why if ffmpeg did, else leave it to the conversion.
        stderr = decoded.stderr or ""
        if is_unreadable_source(stderr):
            return SourceCheck(UNREADABLE, redact_presigned_urls(stderr.strip())[-500:])
        return SourceCheck(UNKNOWN, "no frame was decoded")
    return SourceCheck(PLAYABLE)
