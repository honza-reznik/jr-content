"""Real FFmpeg regression: a successful encode must have playable HLS variants."""
import asyncio
import json
import math
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from packages.transcoder.base import TranscodeJob
from packages.transcoder.ffmpeg_transcoder import FFmpegTranscoder, complete_hls_master


def test_empty_master_uses_measured_segment_bandwidth(tmp_path):
    master = tmp_path / "master.m3u8"
    master.write_text("#EXTM3U\n#EXT-X-VERSION:3\n")
    for number in range(2):
        variant = tmp_path / str(number)
        variant.mkdir()
        (variant / "playlist.m3u8").write_text(
            "#EXTM3U\n#EXTINF:2,\nseg_000.ts\n#EXTINF:0.5,\nseg_001.ts\n#EXT-X-ENDLIST\n"
        )
        (variant / "seg_000.ts").write_bytes(b"a" * (1000 + number))
        (variant / "seg_001.ts").write_bytes(b"b" * (400 + number))
    complete_hls_master(tmp_path)
    text = master.read_text()
    assert text.count("#EXT-X-STREAM-INF:") == 2
    assert "BANDWIDTH=6400\n0/playlist.m3u8" in text
    assert "BANDWIDTH=6416\n1/playlist.m3u8" in text
    complete_hls_master(tmp_path)
    assert master.read_text() == text


def test_valid_master_is_preserved(tmp_path):
    master = tmp_path / "master.m3u8"
    original = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=6000,CODECS="avc1.640028"\n0/playlist.m3u8\n'
    master.write_text(original)
    complete_hls_master(tmp_path)
    assert master.read_text() == original


def test_empty_master_without_variants_is_rejected(tmp_path):
    (tmp_path / "master.m3u8").write_text("#EXTM3U\n")
    with pytest.raises(RuntimeError, match="no variants"):
        complete_hls_master(tmp_path)


@pytest.mark.parametrize("has_audio", [False, True], ids=["silent", "audio"])
@pytest.mark.parametrize("copy_source", [False, True], ids=["encode", "copy"])
def test_real_transcode_has_decodable_master_and_variants(tmp_path, monkeypatch, has_audio, copy_source):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg and ffprobe required")
    monkeypatch.setenv("TRANSCODER_SOURCE_COPY", "true" if copy_source else "false")
    monkeypatch.setenv("TRANSCODER_CPU_LIMIT", "2")
    monkeypatch.setenv("TRANSCODER_BACKEND", "cpu")
    source = tmp_path / "source.mp4"
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=1280x720:rate=12:duration=5",
    ]
    if has_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-threads", "2", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-g", "24", str(source)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=30)
    stored = tmp_path / "stored"
    s3 = MagicMock()
    s3.generate_presigned_url.return_value = str(source)

    def upload(filename, _bucket, key, **_kwargs):
        destination = stored / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(filename, destination)

    s3.upload_file.side_effect = upload
    job = TranscodeJob(
        media_id="silent-test", version_id="v1", input_s3_key="source.mp4",
        output_s3_prefix="processed/test/v1", qualities=["720p"] if copy_source else ["720p", "360p"],
    )
    result = asyncio.run(FFmpegTranscoder(s3, "test-bucket").transcode(job))
    assert result.success, result.error
    root = stored / job.output_s3_prefix
    master = root / "master.m3u8"
    text = master.read_text()
    variants = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert len(variants) == (1 if copy_source else 2)
    for line in text.splitlines():
        if line.startswith("#EXT-X-STREAM-INF:"):
            bandwidth = int(line.split("BANDWIDTH=", 1)[1].split(",", 1)[0])
            assert bandwidth > 0 and math.isfinite(bandwidth)
    for playlist in [master, *(root / name for name in variants)]:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(playlist)],
            check=True, capture_output=True, text=True, timeout=30,
        )
        streams = json.loads(probe.stdout)["streams"]
        assert any(stream["codec_type"] == "video" for stream in streams)
        assert any(stream["codec_type"] == "audio" for stream in streams) == has_audio
        subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(playlist), "-threads", "2", "-f", "null", "-"],
            check=True, capture_output=True, timeout=30,
        )
    if not copy_source:
        assert result.mp4_key and (stored / result.mp4_key).is_file()
