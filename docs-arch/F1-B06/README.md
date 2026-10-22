# F1-B06: ověřovací skripty k ADR-002 až ADR-004

ADR a rozpis bloků jsou v KB (`vyvoj/rozhodnuti/ADR-00{2,3,4}-*.md`, `vyvoj/audity/F1-B06-rozpis.md`).
Tady jsou jen malé skripty, kterými se ověřila tvrzení, která šla ověřit bez psaní aplikace.
Všechno běží v Dockeru (obraz `jrc-dev:local`), nic se neinstaluje do systému.

| Skript | Co ověřuje | Výsledek 2026-10-22 |
|---|---|---|
| `overeni-subtree.sh` | ADR-004: `git subtree add --squash` převezme kód Brightbean do podsložky, naše úprava a pozdější oprava z upstreamu se spojí bez konfliktu, v historii je zapsaná verze upstreamu | prošlo (OK 1–3) |
| `overeni-iframe.cjs` | ADR-003: přehrávač FreeFrame jde vložit do cizí stránky (`<iframe>`), token podepsaný sdíleným tajemstvím FreeFrame přijme, klientku jde přidat do projektu přes API a ve vloženém přehrávači je přihlášená bez formuláře; desktop i telefon 390×844 | prošlo (`screenshoty/vysledek-iframe.json`) |

## Spuštění

```sh
cd /home/honza/jr-content-wt/F1-B06

# ADR-004 (bez sítě, ~10 s)
docker run --rm --name jrc-f1b06-overeni --network none --user "$(id -u):$(id -g)" -e HOME=/tmp/h \
  -v /home/honza/jr-content-wt/F1-B01/audit/brightbean-studio:/src/bb:ro \
  -v "$PWD/docs-arch/F1-B06:/tools:ro" jrc-dev:local sh /tools/overeni-subtree.sh

# ADR-003: nejdřív spustit auditní stack FreeFrame (docs-audit/F1-B02/instalace.md ve worktree F1-B02)
cd /home/honza/jr-content-wt/F1-B02
docker compose -p jrc-audit-freeframe -f docs-audit/F1-B02/compose.yml up -d
cd /home/honza/jr-content-wt/F1-B06
docker run --rm --name jrc-f1b06-iframe --network host \
  --env-file /home/honza/jr-content-wt/F1-B02/audit/.env --user "$(id -u):$(id -g)" -e HOME=/tmp/h \
  -v /home/honza/jr-content-wt/F1-B02/audit/results:/results:ro \
  -v "$PWD/docs-arch/F1-B06:/tools:ro" -v "$PWD/docs-arch/F1-B06/screenshoty:/out" \
  jrc-dev:local node /tools/overeni-iframe.cjs
# na konci stack zastavit
docker compose -p jrc-audit-freeframe -f /home/honza/jr-content-wt/F1-B02/docs-audit/F1-B02/compose.yml down
```

Pozn.: `overeni-iframe.cjs` přidá klientku z auditu (`audit-client.py`) do auditního projektu
jako `reviewer` (stejně jako by to dělala synchronizace z Brightbean). Stránka „Brightbean“ je jen
simulace (malý server na `127.0.0.1:13999` po dobu běhu skriptu). Podstrčená stránka přes
`page.route` nestačí: Chromium ji bere jako veřejnou adresu a vložení `127.0.0.1` zablokuje
(`ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS_CHECKS`).
