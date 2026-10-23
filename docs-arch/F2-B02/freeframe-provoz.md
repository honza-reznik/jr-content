# FreeFrame v JR Content (F2-B02)

Doplněk pro PM do `KB/aplikace/provoz/vyvojove-prostredi.md`; kodér smí v KB
upravovat pouze svůj blok. Převzetí tohoto textu je součást předání AK7.

`make up` sestaví FreeFrame, založí databázi `freeframe` na PostgreSQL 18.6,
provede `alembic upgrade head` a připraví účet `jrc-sync` s právy správce celé instance.
Přihlášení: e-mail z `JRC_FF_SYNC_EMAIL` (výchozí `jrc-sync@example.com`), heslo
z `JRC_FF_SYNC_PASSWORD` v místním `.env`. Heslo ani přístupové tokeny nekopíruj do logů.
`make ff-account` účet bezpečně připraví znovu; při změně hesla zneplatní staré tokeny.

Od F2-B04 je web na společném vstupu `http://127.0.0.1:13000/video/login`
(`JRC_APP_PORT`) a API na `http://127.0.0.1:13000/video-api/health`.
Dokumentace API (`/docs`, `/redoc`, `/openapi.json`) je vypnutá (`DISABLE_DOCS`)
a správa `/video-api/admin/*` je přes společný vstup zablokovaná (403).
Dočasné porty 13022/18022 (`JRC_FF_WEB_PORT`, `JRC_FF_API_PORT`) už neexistují.
Pro SSH stačí přesměrovat společný port a port S3 (`JRC_S3_PORT`).
`NEXT_PUBLIC_BASE_PATH=/video`, `NEXT_TELEMETRY_DISABLED=1`; původní Compose FreeFrame
se nepoužívá. Závislosti webu instaluje pouze Docker, `pnpm install --frozen-lockfile`.
Po změně zámku balíčků sestav obraz znovu a obnov postradatelný svazek webových závislostí.

Převodní worker odebírá frontu `transcoding`, jeden převod současně, limit 4 CPU.
Údržba odebírá frontu `maintenance`; jediný beat (plánovač úloh) jí zadává hodinový
úklid nahrávání a připomínky, obnovení uvízlých převodů v půl a denní odstranění
smazaných záznamů ve 3:00 UTC. Nedokončené nahrávání se uklízí po 24 hodinách,
smazané záznamy po 30 dnech. Vyhledávání osiřelých souborů je vypnuté.
Všechny aplikační služby včetně API, webu, Brightbean, databáze a Garage jsou
pouze ve vnitřní síti bez internetu. Brána `jrc-ff-gate` od F2-B04 přenáší
jen místní port S3 na pevný vnitřní cíl (podepsané adresy videí z prohlížeče);
PostgreSQL je dostupný jen uvnitř, např. `make shell CMD='psql -Atc "SELECT 1"'`.
Jen brána a vývojový `jrc-dev` mají běžnou síť. Brána přenáší TCP beze změny,
včetně WebSocketů a podpisů S3; neumožňuje volbu libovolného cíle. Používá již
místní obraz jrc-dev, běží bez tajemství a s jediným připojeným skriptem pro bránu.
DM Sans je v repozitáři s licencí SIL OFL 1.1 (`freeframe/apps/web/app/fonts/`),
Next.js ho načítá přes `next/font/local`, žádné Google Fonts při startu ani sestavení.
E-mailový worker neběží; SMTP je 127.0.0.1:9 bez TLS, pošta neodchází.

Videa jsou v Garage bucketu `jrc-video` v existujícím svazku `jrc-s3-data`.
FreeFrame používá vlastní `JRC_FF_S3_ACCESS_KEY`/`JRC_FF_S3_SECRET_KEY`, nikoli
klíč původního bucketu `jrc-local`. Inicializace přidá bucket i nad existujícími daty.
Redis používá svazek `jrc-redis-data`, ukládá frontu na disk (AOF); `make down`
data zachová. Web má postradatelné svazky `jrc-ff-web-modules` a `jrc-ff-web-next`.
`make reset` smaže i databázi, videa a frontu daného projektu.

```sh
make test-ff        # pytest, Vitest, TypeScript, lint, zákaz internetu vnitřních služeb
make test-ff-smoke  # živé API, přihlášení správce, 1080p -> Garage -> ready, web 200
make test-isolation # sítě/porty všech služeb, spojení IPv4/IPv6 z běžících služeb
make test-ff-offline # čerstvá cache, Chromium + místní písmo, audit serveru, offline build
make test          # celé F0 + Brightbean + FreeFrame před předáním bloku
```

Jednotkové testy webu běží s prázdným `NEXT_PUBLIC_BASE_PATH`, jak předpokládá
upstream. Cestu `/video` ověřuje živý test a produkční sestavení; vlastní testy
pomocné funkce `base-path` pokrývají oba způsoby nasazení.
`test-ff` zahrnuje také obě nové kontroly izolace a webu bez internetu. Studený web
i produkční sestavení běží v jednorázovém kontejneru jen na internal s oddělenou
dočasnou kopií zdrojů bez `.next`. Test sleduje požadavky prohlížeče a pomocí
`NODE_OPTIONS` i DNS/TCP pokusy procesů Next.js; cizí doména znamená selhání,
i pokud spojení zablokovala síť. API testovacího prohlížeče používá vnitřní adresu.

Živý test běží v jednorázovém Docker kontejneru se sítí hostitele, protože podepsané
adresy videí obsahují místní 127.0.0.1. Nemění jejich adresu ani podpis a nepoužívá
internet. Vytváří jen vymyšlený projekt a třísekundové video. Výsledek zůstává
v `ops/test-output/F2-B02/`, mimo git. Automatická sada upstreamu používá převážně
napodobenou databázi; migrace a živý test navíc ověřují skutečný PostgreSQL a Garage.

Oddělené ověření F2-B02: `JRC_PROJECT=jrc-f2b02`, porty uložené v jeho `.env`
13012 (dev), 15434 (PG), 13912 (Garage), 18012 (Brightbean), 13022 a 18022 (FreeFrame);
stav F2-B02, dočasné porty FreeFrame a PG zrušil F2-B04.
Pouze tento projekt lze po testu uklidit přes
`JRC_PROJECT=jrc-f2b02 docker compose -p jrc-f2b02 --env-file .env -f docker-compose.yml down -v`.
Výchozí `jrc` nad main ani ostatní projekty bloků se nepřepínají.
