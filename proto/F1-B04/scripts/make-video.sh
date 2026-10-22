#!/bin/sh
# Vyrobí lokálně testovací video pro prototyp přehrávače (spouštět v kontejneru).
# 30 s, 1920x1080, 30 snímků/s, barevné pruhy SMPTE + tón 440 Hz, BEZ časového kódu v obraze.
# Přes pruhy jede zleva doprava černý čtverec 120x120 px (x = (1920-120) * t / 30). Není to časový kód,
# jen pohyb: automatický test z jeho polohy v obraze pozná, který snímek se právě zobrazuje.
# Výstup: media/test-1080p.mp4 (H.264 High + AAC, "faststart" = přehrávání začne před stažením celého souboru).
set -eu
cd "$(dirname "$0")/.."
mkdir -p media
OUT=media/test-1080p.mp4
start=$(date +%s)
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "smptehdbars=size=1920x1080:rate=30:duration=30" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=30" \
  -f lavfi -i "color=c=black:size=120x120:rate=30:duration=30" \
  -filter_complex "[0:v][2:v]overlay=x='(W-w)*t/30':y=60:eval=frame[v]" \
  -map "[v]" -map 1:a \
  -c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p -preset medium -crf 23 \
  -g 30 -keyint_min 30 -sc_threshold 0 \
  -c:a aac -b:a 128k -ac 2 \
  -movflags +faststart -shortest "$OUT"
end=$(date +%s)
echo "Hotovo: $OUT za $((end - start)) s"
ffprobe -hide_banner -loglevel error -show_entries format=duration,size,bit_rate:stream=codec_name,profile,width,height,r_frame_rate -of compact "$OUT"
