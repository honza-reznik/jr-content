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
```

Ověření instalace, testy a konkrétní scénáře budou doplněny po běhu.
