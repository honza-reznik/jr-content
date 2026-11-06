"""The one place that opens an uploaded file with ffmpeg or ffprobe (JR Content P1-B26).

An upload is untrusted input, and ffmpeg is happy to follow it elsewhere: a
text file that starts with `#EXTM3U` is an HLS playlist whose segments are
fetched from any URL it names, an `ffconcat` script opens any file or URL it
lists, an SDP file opens network sockets. Probing such a file from inside the
API or the worker would make the server request internal addresses on the
uploader's behalf (SSRF) or read local files.

So every command that reads an uploaded file gets its input from here, never as
a bare `-i <url>`:

* `-protocol_whitelist` -- only what reading this one source needs: the signed
  storage address (http/https over tcp/tls) or a local temporary file. It is
  inherited by everything a demuxer opens from it.
* `-format_whitelist` -- only real media containers (and, for images, image
  decoders). Playlists, concat scripts, SDP, image sequences with patterns and
  every other demuxer that can open another resource are not on it.
* `-probesize` / `-analyzeduration` -- an explicit bound on what is read and
  decoded just to recognise the file.

Our own output (the HLS ladder a conversion has just written) is not an
upload; `own_hls_input` still restricts it to local files.
"""
from __future__ import annotations

# ffmpeg matches a demuxer whose name lists several ("mov,mp4,m4a,3gp,3g2,mj2",
# "matroska,webm") when any one of them is listed here.
MEDIA_DEMUXERS = (
    "mov", "matroska", "avi", "mpeg", "mpegts", "mpegvideo", "asf", "flv",
    "ogg", "wav", "mp3", "aac", "flac", "aiff",
)
IMAGE_DEMUXERS = (
    "png_pipe", "jpeg_pipe", "webp_pipe", "gif", "gif_pipe", "tiff_pipe", "bmp_pipe", "mov",
)
URL_PROTOCOLS = "http,https,tcp,tls"
FILE_PROTOCOLS = "file"
# What is read and analysed only to recognise the streams (ffmpeg's own
# defaults, stated so they cannot drift upwards through a global setting).
PROBE_BYTES = 5_000_000
ANALYZE_MICROSECONDS = 5_000_000


def _protocols(source: str) -> str:
    lowered = str(source).lower()
    if lowered.startswith(("http://", "https://")):
        return URL_PROTOCOLS
    if "://" in lowered or lowered.startswith(("-", "pipe:", "concat:", "subfile,", "data:")):
        # Not a signed address and not a plain path: refuse to build a command.
        raise ValueError("Uploaded media is read only from storage or a local temporary file")
    return FILE_PROTOCOLS


def source_options(source: str, *, images: bool = False) -> list[str]:
    """Input options that must precede an uploaded `source` in an ffmpeg/ffprobe command."""
    return [
        "-protocol_whitelist", _protocols(source),
        "-format_whitelist", ",".join(IMAGE_DEMUXERS if images else MEDIA_DEMUXERS),
        "-probesize", str(PROBE_BYTES),
        "-analyzeduration", str(ANALYZE_MICROSECONDS),
    ]


def ffmpeg_input(source: str, *, images: bool = False) -> list[str]:
    """`[options..., "-i", source]` for an ffmpeg command reading an upload."""
    return [*source_options(source, images=images), "-i", source]


def ffprobe_input(source: str, *, images: bool = False) -> list[str]:
    """`[options..., source]` for an ffprobe command reading an upload (put it last)."""
    return [*source_options(source, images=images), source]


def own_hls_input(playlist: str) -> list[str]:
    """Our own freshly written HLS ladder: the hls demuxer, local files only."""
    return ["-protocol_whitelist", FILE_PROTOCOLS, "-format_whitelist", "hls,mpegts,mov", "-i", playlist]
