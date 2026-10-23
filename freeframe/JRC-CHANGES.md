# JR Content: změny FreeFrame

Import upstreamu `3829889` (MIT, původní licence a copyright zachovány).

## 2026-10-23 — F2-B02

Provozní úpravy žijí převážně v kořeni JR Content. Po review je změněn také
`apps/web/app/layout.tsx`: `next/font/local` a původní DM Sans v `app/fonts/`
s licencí SIL OFL 1.1 a přesným zdrojem/otisky v jeho README. Při prvním spuštění
ani sestavení se písmo nestahuje. Přehled úprav:

- `docker-compose.yml`: API, převodní worker (fronta `transcoding`, 4 CPU), údržba
  (`maintenance`), jediný beat, Next.js pod `/video` a Redis s trvalou frontou.
  Všechny aplikační služby (také API, web, Brightbean, PG a Garage) mají jen
  vnitřní síť bez internetu. Jediná dočasná `jrc-ff-gate` přenáší místní porty
  na pevné vnitřní TCP cíle; výjimkou je ještě vývojový `jrc-dev`.
  Brána používá již dostupný obraz jrc-dev a pouze malý Node.js skript,
  nemá tajemství ani přístup k pracovní kopii. E-mailový worker
  neběží, SMTP míří na nepoužitý port 9 na 127.0.0.1.
- `docker/Dockerfile.ff-api`: místní vývojový obraz s přesnými Python závislostmi
  upstreamu, FFmpegem a pytest. `docker/Dockerfile.ff-web`: pnpm 10.15.0,
  instalace přes `apps/web/pnpm-lock.yaml` s `--frozen-lockfile`, vypnutá telemetrie.
- `scripts/init-freeframe-db.sh`: databáze `freeframe` na společném PostgreSQL 18.6.
- `scripts/init-freeframe-account.py`: idempotentní servisní superadmin `jrc-sync`;
  přihlašovací e-mail `JRC_FF_SYNC_EMAIL`, heslo pouze `JRC_FF_SYNC_PASSWORD` z `.env`.
- `scripts/init-env.cjs`, `.env.example`: náhodné JWT tajemství, heslo servisního
  účtu a vlastní video S3 klíč, zachování existujících hodnot při upgradu.
- `docker/garage-entrypoint.sh`: bucket `jrc-video` a samostatný klíč, také při
  spuštění nad dříve vytvořeným svazkem Garage.
- `Makefile`: sestavení, servisní účet, pytest/Vitest/tsc/lint v `make test-ff`;
  `test-isolation` kontroluje sítě/porty všech služeb včetně inicializací
  a skutečné IPv4/IPv6 spojení z každé běžící aplikační služby.
  `test-ff-offline` ověřuje studenou kompilaci, požadavky Chromium, načtení
  místního písma a produkční sestavení; zachytí i neúspěšný pokus serveru
  o cizí spojení. Obě kontroly jsou součástí test-ff; jednotkové testy používají výchozí
  prázdný basePath upstreamu, nasazení `/video` ověřuje build a živý test; součást celkového `make test`.
  `make test-ff-smoke` vytvoří 1080p video FFmpegem a ověří živé multipart
  nahrání i převod na stav `ready` přes API a Garage.

Původní `docker-compose*.yml` FreeFrame se pro JR Content nepoužívají.
Provozní postup je v `docs-arch/F2-B02/freeframe-provoz.md` (doplněk pro PM do KB).
