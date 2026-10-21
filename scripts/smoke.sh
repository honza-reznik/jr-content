#!/usr/bin/env bash
set -euo pipefail
cd /workspace
mkdir -p ops/test-output/F0-B03 ops/screenshoty/F0-B03

printf 'Ověření nástrojů v jrc-dev\n'
node --version
python3 --version
git --version
make --version | head -n 1
ffmpeg -version | sed -n '1p'

result=$(PGCONNECT_TIMEOUT=10 psql -X -v ON_ERROR_STOP=1 -Atc 'SELECT 1')
[ "$result" = 1 ]
printf 'OK PostgreSQL: SELECT 1\n'

python3 scripts/smoke-s3.py

ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i testsrc2=size=320x240:rate=25 -t 2 \
    -c:v libx264 -threads 2 -pix_fmt yuv420p \
    ops/test-output/F0-B03/test-video.mp4
ffprobe -v error -show_entries format=duration:stream=codec_name,width,height \
    -of json ops/test-output/F0-B03/test-video.mp4 > ops/test-output/F0-B03/video-info.json
python3 - <<'PY'
import json
from pathlib import Path
info = json.loads(Path('ops/test-output/F0-B03/video-info.json').read_text())
assert abs(float(info['format']['duration']) - 2.0) < 0.05, info
assert info['streams'][0] == {'codec_name': 'h264', 'width': 320, 'height': 240}, info
print('OK FFmpeg: místně vytvořené video H.264, 320×240, 2 sekundy')
PY
node scripts/smoke-browser.cjs
printf 'OK všechny smoke testy prostředí\n'
