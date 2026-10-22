# JR Content: změny FreeFrame

Import upstreamu `3829889` (MIT, původní licence a copyright zachovány).

## 2026-10-23 — F2-B02

Původní soubory FreeFrame nejsou změněné. Přidán tento přehled. Provozní úpravy žijí
v kořeni JR Content:

- `docker-compose.yml`: API, převodní worker (fronta `transcoding`, 4 CPU), údržba
  (`maintenance`), jediný beat, Next.js pod `/video` a Redis s trvalou frontou.
  Worker, údržba, beat a Redis mají jen vnitřní síť bez internetu. E-mailový worker
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
  síťová kontrola vnitřních služeb; jednotkové testy webu používají výchozí
  prázdný basePath upstreamu, nasazení `/video` ověřuje build a živý test; součást celkového `make test`.
  `make test-ff-smoke` vytvoří 1080p video FFmpegem a ověří živé multipart
  nahrání i převod na stav `ready` přes API a Garage.

Původní `docker-compose*.yml` FreeFrame se pro JR Content nepoužívají.
Provozní postup je v `docs-arch/F2-B02/freeframe-provoz.md` (doplněk pro PM do KB).
