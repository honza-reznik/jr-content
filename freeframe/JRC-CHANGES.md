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

## 2026-10-23 — F5-B07

- `apps/web/components/review/video-player.tsx`: štítek času nad skutečným obdélníkem
  obrazu, také na telefonu a celé obrazovce. FPS bere z video souboru právě vybrané
  verze; stejnou hodnotu používá i čas ve spodní liště.
- `video-timecode-overlay.tsx`: čas skutečně vykresleného snímku přes
  `requestVideoFrameCallback`; během posunu štítek skrytý do dokončení posunu
  a příchodu snímku. Starší prohlížeče mají zálohu přes události přehrávače.
  Štítek nebrání klikání ani kreslení poznámek.
- `apps/web/lib/video-timecode.ts`: počet snímků a non-drop časový kód podle
  metadat, včetně zlomkových FPS. Non-drop čísluje všechny snímky souvisle;
  u 29,97 fps se proto číselný čas postupně liší od skutečných sekund.
  Při neznámém FPS zobrazuje sekundy místo vymyšleného počtu snímků.
- `apps/web/hooks/use-video-player.ts`: volba zachování překryvu při celé
  obrazovce; bez elementového API nebo při jeho odmítnutí použije celé okno,
  včetně ukončení klávesou Escape a obnovení posouvání stránky.
- Regresní testy překryvu a skutečného hooku; původní test přepínače času
  používá přístupný název tlačítka místo pořadí prvků.
- `scripts/test-video-timecode.cjs` (kořen JR Content): živý test v Dockeru
  `jrc-f5b07`, lokální videa FFmpeg 25/30 fps. Porovnává čas se skutečnou
  polohou čtverce ve snímku; desktop 1440 a telefon 390, přehrávání, pauza,
  posun, táhlo, oba režimy celé obrazovky a screenshoty.

## 2026-10-23 · F5-B01

- `apps/api/routers/jrc_sync.py`, registrace v `main.py`: interní opakovatelné PUT
  `/jrc-sync/accounts/{source_uuid}` a `/jrc-sync/projects/{source_uuid}`.
  Přístup má jen aktivní superadmin se shodným `JRC_FF_SYNC_EMAIL` (servisní účet).
  Účty vznikají bez hesla a pozvánky; nevytváří se e-mailová úloha.
  E-mail se kontroluje jen na základní tvar (ne `EmailStr`), aby prošly vyhrazené
  testovací domény místních dat jako `example.invalid` (ukázkový klient). Profil a blokace
  se aktualizují při každém volání. Projekt je týmový a neveřejný, vlastníkem je servisní účet.
- Identifikátory se odvozují pomocí UUIDv5 ze stabilního jmenného prostoru a UUID Brightbean.
  Ztracená místní vazba se tak může obnovit bez nového účtu/projektu. Transakční zámek
  PostgreSQL chrání souběžné založení. Konfliktní e-mail se odmítá (409), nepřebírá se
  cizí účet podle e-mailu; privilegovaný nebo smazaný účet/projekt se nepřepisuje.
- Role a odebrání členství používají stávající projektové API. Nové uživatelské
  rozhraní nevzniká; přihlášení jednou vstupenkou a omezení běžného FreeFrame jsou další bloky F5.
- `tests/test_jrc_sync.py`: přístup běžného uživatele/cizího správce, bezpečné založení,
  stabilní identita, konflikt e-mailu, ochrana privilegovaných a smazaných záznamů,
  neveřejný projekt a servisní vlastník. Živý test v kořeni JR Content ověřuje i skutečné role
  klienta, zákaz nahrávání, oddělení klientů a odvolání přístupu při platném přístupovém tokenu.
