"""JR Content P1-B26 review: ffmpeg/ffprobe must not follow an upload to other addresses.

A text file that starts with `#EXTM3U` is an HLS playlist, and ffmpeg fetches
the segments it names from anywhere; an `ffconcat` script opens what it lists.
Probing an upload like that from the API or the worker made the server request
internal addresses (SSRF). Every command that reads an upload now takes its
input from `packages/transcoder/untrusted.py`.

These tests serve the hostile files from a local HTTP server that records every
request, run the real ffmpeg/ffprobe through each production path, and assert
that nothing but the upload itself was requested. A control run without the
options shows the same file does make ffprobe connect, so a green run means the
options worked, not that the attack never applied.
"""
import asyncio
import http.server
import re
import shutil
import subprocess
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from packages.transcoder import image_processor
from packages.transcoder.base import TranscodeJob
from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder
from packages.transcoder.source_check import UNREADABLE, check_media_source, is_unreadable_source
from packages.transcoder.untrusted import ffmpeg_input, ffprobe_input, source_options

ROOT = Path(__file__).resolve().parents[3]


class _Recorder(http.server.BaseHTTPRequestHandler):
    files: dict = {}
    hits: list = []

    def do_GET(self):  # noqa: N802 - http.server API
        type(self).hits.append(self.path.split("?")[0])
        body = type(self).files.get(self.path.split("?")[0], b"")
        self.send_response(200 if body else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    handler = type("Handler", (_Recorder,), {"files": {}, "hits": []})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{srv.server_port}"
    handler.files.update({
        "/hls.mp4": (f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\n{base}/secret-hls.ts\n#EXT-X-ENDLIST\n").encode(),
        "/concat.mp4": f"ffconcat version 1.0\nfile '{base}/secret-concat.ts'\n".encode(),
        "/sdp.mp4": b"v=0\no=- 0 0 IN IP4 127.0.0.1\ns=x\nc=IN IP4 127.0.0.1\nt=0 0\nm=video 9 RTP/AVP 96\n",
    })
    try:
        yield base, handler
    finally:
        srv.shutdown()
        srv.server_close()


def _foreign(handler, own):
    return [path for path in handler.hits if path != own]


HOSTILE = ["/hls.mp4", "/concat.mp4", "/sdp.mp4"]


def test_control_the_playlist_does_make_plain_ffprobe_connect(server):
    base, handler = server
    subprocess.run(["ffprobe", "-v", "error", f"{base}/hls.mp4"], capture_output=True, timeout=30)
    assert "/secret-hls.ts" in handler.hits, "without the options ffprobe follows the playlist"


@pytest.mark.parametrize("name", HOSTILE)
def test_the_content_check_refuses_and_connects_nowhere_else(server, name):
    base, handler = server
    result = check_media_source(f"{base}{name}")
    assert result.verdict == UNREADABLE, result.reason
    assert _foreign(handler, name) == []


@pytest.mark.parametrize("name", HOSTILE)
def test_the_conversion_fails_without_retry_and_connects_nowhere_else(server, name):
    base, handler = server
    transcoder = FFmpegTranscoder(MagicMock(), "bucket", "http://s3")
    job = TranscodeJob(media_id="m", version_id="v", input_s3_key="raw/x", output_s3_prefix="out")
    with patch.object(FFmpegTranscoder, "_get_presigned_url", return_value=f"{base}{name}"):
        result = asyncio.run(transcoder.transcode(job))
        with pytest.raises(Exception):
            asyncio.run(transcoder.get_video_metadata("raw/x"))
        with pytest.raises(Exception):
            asyncio.run(transcoder.generate_thumbnails("raw/x", 1))
    assert result.success is False and result.unreadable_source is True, result.error
    assert _foreign(handler, name) == []


def test_the_timecode_probe_of_a_comment_connects_nowhere_else(server, monkeypatch):
    import apps.api.routers.comments as comments
    from apps.api.services import s3_service

    base, handler = server
    monkeypatch.setattr(s3_service, "generate_internal_presigned_get_url", lambda key, expires_in=0: f"{base}/hls.mp4")
    media = MagicMock(s3_key_raw="raw/x", id="m")
    assert comments._probe_source_timecode(media, MagicMock(timebase=25)) is None
    assert _foreign(handler, "/hls.mp4") == []


@pytest.mark.parametrize("pipeline", ["image", "audio"])
def test_downloaded_uploads_are_read_as_local_files_only(server, pipeline, tmp_path):
    """Images and audio are downloaded first; a playlist among them must not reach the network."""
    base, handler = server
    s3 = MagicMock()
    s3.download_file.side_effect = lambda bucket, key, path: Path(path).write_bytes(handler.files["/hls.mp4"])
    process = image_processor.process_image if pipeline == "image" else image_processor.process_audio
    with pytest.raises(subprocess.CalledProcessError) as failed:
        process(s3, "bucket", "raw/x", "out")
    assert handler.hits == []
    stderr = failed.value.stderr.decode("utf-8", "replace") if isinstance(failed.value.stderr, bytes) else failed.value.stderr
    assert is_unreadable_source(stderr), "refused as not media, so the worker does not retry it"


@pytest.mark.parametrize("source, protocols", [
    ("http://jrc-s3:3900/jrc-video/raw/x?X-Amz-Signature=s", "http,https,tcp,tls"),
    ("https://s3.example/raw/x", "http,https,tcp,tls"),
    ("/tmp/tmpabc.img", "file"),
])
def test_each_source_gets_only_the_protocols_it_needs(source, protocols):
    options = source_options(source)
    assert options[options.index("-protocol_whitelist") + 1] == protocols
    allowed = options[options.index("-format_whitelist") + 1].split(",")
    assert not {"hls", "concat", "sdp", "rtp", "rtsp", "image2", "dash", "tee", "data"} & set(allowed)
    assert int(options[options.index("-probesize") + 1]) <= 5_000_000
    assert ffmpeg_input(source)[-2:] == ["-i", source] and ffprobe_input(source)[-1] == source


@pytest.mark.parametrize("source", ["concat:/a|/b", "pipe:0", "file:///etc/passwd", "-i", "data:,x", "subfile,,start,0,end,1,,:/x"])
def test_anything_but_a_signed_address_or_a_plain_path_is_refused(source):
    with pytest.raises(ValueError):
        source_options(source)


def test_valid_media_still_converts_with_the_options(tmp_path):
    src = tmp_path / "ok.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True, timeout=60)
    out = tmp_path / "frame.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *ffmpeg_input(str(src)), "-frames:v", "1", str(out)],
                   check=True, timeout=60)
    assert out.stat().st_size > 0


def test_no_production_code_builds_its_own_ffmpeg_input():
    """The one place: a bare `"-i"` anywhere else would bypass the whitelists."""
    offenders = []
    for path in list((ROOT / "apps" / "api").rglob("*.py")) + list((ROOT / "packages").rglob("*.py")):
        if "tests" in path.parts or path.name == "untrusted.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"""["']-i["']""", line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == []


def test_every_ffprobe_call_on_an_upload_uses_the_helper():
    """ffprobe takes its input as a bare argument, so each call site must name the helper."""
    missing = []
    for path in list((ROOT / "apps" / "api").rglob("*.py")) + list((ROOT / "packages").rglob("*.py")):
        if "tests" in path.parts:
            continue
        text = path.read_text()
        for match in re.finditer(r'(?<!label=)"ffprobe"', text):
            if "ffprobe_input(" not in text[match.start(): match.start() + 900]:
                missing.append(f"{path.relative_to(ROOT)}:{text[:match.start()].count(chr(10)) + 1}")
    assert missing == []


def test_prlimit_bounds_the_check_inside_the_api():
    if not shutil.which("prlimit"):
        pytest.skip("prlimit not installed")
    seen = []

    def run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "", "x: Invalid data found when processing input")

    check_media_source("http://s3/raw/x?sig", run=run)
    assert seen[0][0].endswith("prlimit") and any(arg.startswith("--as=") for arg in seen[0])
