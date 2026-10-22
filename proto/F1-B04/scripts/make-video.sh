#!/bin/sh
# Vyrobí lokálně testovací videa pro prototyp přehrávače (spouštět v kontejneru).
# Obě: 30 s, 30 snímků/s, barevné pruhy SMPTE + tón 440 Hz, BEZ časového kódu v obraze.
# Přes pruhy jede zleva doprava černý čtverec 120x120 px (x = (šířka-120) * t / 30). Není to časový kód,
# jen pohyb: automatický test z jeho polohy v obraze pozná, který snímek se právě zobrazuje.
#   media/test-1080p.mp4      1920x1080 (16:9, na šířku)
#   media/test-reel-1080x1920.mp4  1080x1920 (9:16, na výšku jako Reels)
# Formát: MP4 s H.264 High + AAC, "faststart" (přehrávání začne před stažením celého souboru),
# klíčový snímek každou sekundu (rychlý a přesný posun).
set -eu
cd "$(dirname "$0")/.."
mkdir -p media

encode() { # $1 = šířka, $2 = výška, $3 = výstup
  start=$(date +%s)
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "smptehdbars=size=$1x$2:rate=30:duration=30" \
    -f lavfi -i "sine=frequency=440:sample_rate=48000:duration=30" \
    -f lavfi -i "color=c=black:size=120x120:rate=30:duration=30" \
    -filter_complex "[0:v][2:v]overlay=x='(W-w)*t/30':y=60:eval=frame[v]" \
    -map "[v]" -map 1:a \
    -c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p -preset medium -crf 23 \
    -g 30 -keyint_min 30 -sc_threshold 0 \
    -c:a aac -b:a 128k -ac 2 \
    -movflags +faststart -shortest "$3"
  end=$(date +%s)
  echo "Hotovo: $3 za $((end - start)) s"
  ffprobe -hide_banner -loglevel error -show_entries format=duration,size,bit_rate:stream=codec_name,profile,width,height,r_frame_rate -of compact "$3"
}

encode 1920 1080 media/test-1080p.mp4
encode 1080 1920 media/test-reel-1080x1920.mp4
