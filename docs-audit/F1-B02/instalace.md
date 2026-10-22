# FreeFrame: místní audit F1-B02

Auditovaná revize: `38298891c65f01cb96c3c01f18fd27ee775b61e5` (main, 19. 10. 2026).
Je to průzkum konkrétní vývojové revize, nikoli doporučení nasadit main do produkce.
V době auditu stable/latest ukazovaly na v1.19.0 (`6a440f1f1f3cfce6a266bab07c4cff886bac1bb2`).

## Příprava

Předpoklad: Docker, Compose a místní obraz `jrc-dev:local` z F0-B03. Společné služby `jrc`
se nepřepínají. Všechny následující příkazy jsou z kořene worktree F1-B02.

```sh
cd /home/honza/jr-content-wt/F1-B02
mkdir -p audit/results
git clone --depth 1 https://github.com/Techiebutler/freeframe audit/freeframe
# Pokud main již pokročil, stáhnout a přepnout přesně auditovanou revizi:
git -C audit/freeframe fetch --depth 1 origin 38298891c65f01cb96c3c01f18fd27ee775b61e5
git -C audit/freeframe checkout --detach 38298891c65f01cb96c3c01f18fd27ee775b61e5
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/audit:/audit" -v "$PWD/docs-audit/F1-B02:/tools:ro" \
  jrc-dev:local python3 /tools/init-env.py
BUILDX_CONFIG="$PWD/.cache/docker-buildx" docker compose \
  -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml up -d --build
```

`init-env.py` generuje náhodná hesla pouze do ignorovaného `audit/.env` s oprávněním 600.
Při opakování existující soubor nepřepisuje. Cizí kód, balíčky, videa a pracovní výsledky
zůstávají v ignorovaném `audit/`. Do gitu nepatří hesla ani odpovědi API s tokeny.

## Provoz a odlišnosti od výchozího balíku

| Služba | Místní adresa |
|---|---|
| Web | http://127.0.0.1:13020 |
| API a jeho popis | http://127.0.0.1:18020/docs |
| S3 (Silo) | http://127.0.0.1:13920 |

Databáze a Redis nemají publikované porty. Compose používá vlastní síť a svazky
`jrc-audit-freeframe_*`. Nikdy nespouštět původní compose bez úprav: publikuje porty na všech
rozhraních, přestože README tvrdí přístup pouze z hostitele.

Vlastní compose zachovává aplikaci bez úprav. Používá dostupný PostgreSQL 16 namísto 15,
Python a FFmpeg ze společného vývojového obrazu, Node 24 namísto 20. API závislosti mají
samostatné virtuální prostředí v novém obrazu. Web běží ve vývojovém režimu jako upstream dev
stack; měření RAM webu tedy není měření produkčního sestavení. Npm rozsahy verzí nejsou přesně
zamčené upstream dev postupem; reprodukuje se postup a revize aplikace, nikoli bitově shodné balíčky.

Převádění má jeden úkol současně a rozpočet čtyř CPU (`TRANSCODER_CPU_LIMIT=4`); standardní
kvality 1080p, 720p, 360p. E-mailový pracovník ani plánované mazání neběží. SMTP směřuje na
nepoužitý místní port; žádná skutečná pošta, cloud ani sociální účty se nepřipojují.

```sh
# Stav a záznamy (záznamy nekopírovat hromadně do KB, mohou obsahovat podepsané URL):
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml ps
# Zastavení se zachováním dat:
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml down
# Opětovné spuštění ze stejných dat:
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml up -d
# Počkat, až API přijímá spojení (kontejnery hlásí „healthy“ dřív):
until curl -sf http://127.0.0.1:18020/setup/status >/dev/null; do sleep 2; done
# Pozn.: web se instaluje `npm install` bez zámku verzí, nové sestavení může dostat jiné závislosti.
```

Ověřeno 21. 10. 2026 (kodér A): po `down` a `up -d` je web i API připravené za ~27 s
(`curl http://127.0.0.1:13020/login` → 200, `curl http://127.0.0.1:18020/setup/status` →
`needs_setup: false`), data i videa zůstala a `audit-ui.cjs` znovu prošel.

## Živé zkoušky a měření

Jednorázové kontejnery níže nepouštějí server, jen klienta HTTP/prohlížeč. `--network host`
mu dovolí číst služby na místní smyčce a správně použít podepsané S3 adresy. Porty se tím
nepřidávají. Samostatný kontejner používá tentýž obraz a Chromium jako `jrc-dev`, aby neměnil
jeho pracovní složku. Skript prohlížeče odmítá požadavky na jiné adresy než localhost/127.0.0.1.

```sh
# Testovací video, pouze při prvním běhu (žádné stahování videa):
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/audit/results:/results" \
  jrc-dev:local ffmpeg -hide_banner -loglevel error \
  -f lavfi -i testsrc2=size=1920x1080:rate=25 \
  -f lavfi -i sine=frequency=1000:sample_rate=48000 -t 60 \
  -c:v libx264 -preset ultrafast -crf 23 -threads 2 -pix_fmt yuv420p \
  -c:a aac -movflags +faststart /results/test-1080p.mp4

# V jednom terminálu (ukončí se při úspěšném konci HTTP scénáře):
printf idle > audit/results/phase
./docs-audit/F1-B02/measure.sh

# Ve druhém terminálu; před měřením dokončit instalaci a testy, nechat 3 vzorky v klidu:
docker run --rm --network host --env-file audit/.env \
  --user "$(id -u):$(id -g)" -v "$PWD/audit/results:/results" \
  -v "$PWD/docs-audit/F1-B02:/tools:ro" \
  jrc-dev:local python3 /tools/audit-api.py
# Opakování vytvoří nový projekt; první účet zůstává. V1 a v2 mají shodný testovací obraz,
# ověřuje se skutečné samostatné nahrání, převod a přepínání, ne rozdíl ve střihu.
docker run --rm --network host --env-file audit/.env \
  --user "$(id -u):$(id -g)" -v "$PWD/audit/results:/results" \
  -v "$PWD/docs-audit/F1-B02:/tools:ro" \
  jrc-dev:local python3 /tools/audit-client.py
docker run --rm --network host --env-file audit/.env \
  --user "$(id -u):$(id -g)" -v "$PWD/audit/results:/results" \
  -v "$PWD/docs-audit/F1-B02:/tools:ro" \
  -v "$PWD/docs-audit/F1-B02/screenshoty:/screens" \
  jrc-dev:local node /tools/audit-ui.cjs
```

```sh
# Vlastní pole místo stavů, kanálů, data publikace a popisku (po audit-api.py):
docker run --rm --network host --env-file audit/.env \
  --user "$(id -u):$(id -g)" -v "$PWD/audit/results:/results" \
  -v "$PWD/docs-audit/F1-B02:/tools:ro" \
  jrc-dev:local python3 /tools/audit-fields.py
```

Měření převodu: před spuštěním zkontrolovat `docker stats --no-stream` a `uptime`, aby souběžně
neběželo nic náročného. `measure.sh` zapisuje i `audit/results/host.tsv` (load celého počítače
a nejvytíženější cizí kontejner), takže je vidět, co běželo souběžně.

API scénář nastaví první účet `honza.audit@example.com`; heslo je `AUDIT_PASSWORD` v `audit/.env`.
Účet je výhradně testovací. Klientský scénář získá pozvánku z oprávněné odpovědi správce
přes API a přijme ji, aniž by posílal e-mail. Ověří přihlášení Elly, komentář, schválení,
založení vlastního projektu a odmítnutí cizího neveřejného projektu.

V pracovním `audit/results/` zůstanou výsledky (JSON), časové vzorky Dockeru (TSV) a video.
Při neúspěchu HTTP scénáře zastavit měřič Ctrl+C. `measure.sh` sleduje pouze tento compose
projekt; 100 % CPU znamená jedno vytížené logické jádro, ne celý počítač.

## Automatické testy původního projektu

Pytest spouštět s výchozím nastavením úklidu (`-e STALE_UPLOAD_TIMEOUT_HOURS=24
-e SOFT_DELETE_RETENTION_DAYS=30`). Náš compose má úklid vypnutý (`0`), a s ním 8 testů
úklidu selže. Výsledky 21. 10. 2026: pytest 1521 prošlo / 1 náhodné selhání / 3 přeskočeny,
Vitest 1011 prošlo, `tsc` bez chyb, lint 0 chyb a 18 varování. Upstream používá pnpm se zámkem
verzí; náš dev postup instaluje web přes `npm install` (verze nejsou bitově zamčené).

```sh
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml \
  exec -T -e STALE_UPLOAD_TIMEOUT_HOURS=24 -e SOFT_DELETE_RETENTION_DAYS=30 api python -m pytest apps/api/tests -q -p no:cacheprovider
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml \
  exec -T web npm test -- --maxWorkers=2
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml \
  exec -T web npx tsc --noEmit
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml \
  exec -T web npm run lint
```
