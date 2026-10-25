# JR Content: změny FreeFrame

Import upstreamu `3829889` (MIT, původní licence a copyright zachovány).

## 2026-10-24 — F5-B05

- Podepsaná adresa jedné části nahrávaného videa platí `UPLOAD_PART_URL_SECONDS` (výchozí 900 s, dřív pevně 3600 s;
  `apps/api/config.py`, `services/s3_service.py`). Klient i Brightbean podepisují znovu pro každý pokus, takže to
  nahrávání nezpomalí; Brightbean adresu s delší platností odmítne.
- `docker-compose.yml`: FreeFrame dostává `MAX_UPLOAD_BYTES` (4 GiB, přepis `JRC_VIDEO_MAX_UPLOAD_BYTES`), aby limit
  platil i pro přímé volání API.

## 2026-10-24 — F5-B06

- Režim `?embed=1` skryje navigaci, horní lištu a pomocné panely dashboardu; v asset přehrávači zůstávají video, časový štítek a komentáře. Vložený režim se zachová při navigaci uvnitř dashboardu a asset navigace nese query parametry dál.
- Přidaný Vitest a živý Chromium průchod v iframe stejného původu při 1440 a 390 px. Snímky zůstávají v ignorované složce `ops/screenshoty/F5-B06/`; testovací video se vytváří místním FFmpegem.
## 2026-10-24 — F5-B04

- Vypršený nebo odvolaný token také na share cestách vrací 401, takže web obnoví relaci
  a zopakuje požadavek. Bez tokenu dál fungují běžné veřejné odkazy pro hosty.
- Všechny podpisy příloh komentářů a náhledů obsahu (včetně složek, přehledů a posterů)
  platí 180 s; po odebrání členství nelze vydat nové. Již vydaná adresa doběhne do vypršení.

- Řízené účty mají neměnitelné `users.jrc_managed` (migrace `jrc_f5b04`). Migrace označí
  stávající neprivilegované UUIDv5 účty, které zakládá jen synchronizace, odstraní případná
  hesla/pozvánky a odvolá staré přihlašovací tokeny. Synchronizace označuje i nové účty.
- Heslo, magický kód, přijetí pozvánky a nastavení/změna hesla u řízených účtů vracejí
  srozumitelné 403 s odkazem na přihlášení přes JR Content. Obnova platné relace zůstává
  povolená, bez požadavku na heslo. Projekty zakládá pouze neřízený superadmin.
- API i volitelné přihlášení kontrolují `token_version`; deaktivace a nastavení hesla ji
  zvyšují. Odblokování účtu neobnoví staré tokeny. Approve/Reject i změnu stavu videa
  řízeným účtem API odmítá; společná komponenta schvalování se řízeným účtům nezobrazuje.
- U řízených účtů rozhoduje o přístupu k videu vždy aktuální členství: autorství vlastního
  videa, staré přímé sdílení ani veřejný projekt odebrání nepřebijí. Role viewer nemůže
  zapisovat komentáře, odpovědi, kresby, přílohy ani reakce; zápis vyžaduje reviewer či vyšší.
  Stejná kontrola platí při úpravě vlastního staršího komentáře po změně role nebo odebrání.
- HLS řízených účtů je svázané s uživatelem, verzí jeho relace a projektem. Každý požadavek
  na seznam segmentů i samotný segment znovu ověřuje aktivní účet a členství v živém projektu.
  Segment se poté přesměruje na S3 adresu platnou nejvýše 180 s. Stejnou mez mají přímé
  streamy a stahování řízených účtů. Delší videa fungují bez obnovování celého přehrávače;
  odebrání člena odmítne další požadavek i s platným přehrávacím tokenem. Již stažená data
  nelze vzít zpět a dříve vydaná přímá adresa platí do vypršení.
- Nový formát HLS tokenu (`v=3`) jednorázově odmítne staré nevázané tokeny z doby před
  nasazením této úpravy, které jinak platily 24 hodin. Již otevřený přehrávač je třeba
  znovu načíst; novou adresu dostane až po kontrole současných oprávnění. Staré přímé S3
  adresy lze omezit pouze jejich původním vypršením.
- Opravy review: `projects.jrc_managed` (migrace `jrc_f5b04_projects`) označuje nové i starší
  synchronizované projekty. Jejich veřejné share odkazy vyžadují aktuální přihlášení a členství
  na všech cestách k obsahu, komentář navíc roli reviewer. Heslo odkazu ani autorství tuto
  kontrolu nenahrazují; vztahuje se i na místní účet v řízeném projektu. HLS ze share odkazu
  proto vždy nese uživatele a projekt, přímý stream/stažení má nejvýše 180 s.
- Web posílá aktuální přihlašovací token u všech share požadavků, včetně obsahu složek,
  stránkování, komentářů, verzí obrazu/zvuku a jednotlivého i hromadného stahování.
  Obnova sestaveného uploadu přes `/upload/abort` před spuštěním převodu ověřuje živou roli
  editor; pouhý úklid nedokončeného uploadu zůstává dostupný jeho autorovi.
- Již rozpracovaný upload řízeného účtu vyžaduje aktuální roli editor před podpisem další části,
  obnovením i dokončením. Osobní přehledy a upozornění filtrují aktuální členství už v SQL před
  stránkováním a podepisováním adres. Veřejnost projektu neobchází členství řízeného účtu ani
  v detailu projektu a otevřeném proudu událostí SSE (průběžné zprávy ze serveru).
- Přísnější e-mail synchronizace: NFC a malá písmena, kontrola délky po převodu, odmítnutí
  neviditelných řídicích znaků Unicode, chybných domén a adres; místní ukázkové domény zůstávají.
- Staré obsluhy v loginu a sdílené složce používají zámek `ff_session_tokens`; obnova cookies
  načítá aktuální pár až pod zámkem (`restoreSessionCookies`), odhlášení čeká na `removeTokens`.
  Regrese pokrývají souběh obnovy cookies s odhlášením, API, HLS a odvolání verze relace.

## 2026-10-24 — F5-B03

- Opravy bezpečnostního review: `auth.ts` drží sdílenou značku relace v localStorage,
  kterou mění přihlášení i odhlášení. SSO výměna a obnova tokenů si ji uloží před
  požadavkem a před uložením odpovědi znovu ověří; opožděná odpověď po odhlášení
  v jiné kartě už neobnoví relaci. Odmítnutí staré obnovy nesmaže novější relaci.
- Kontrola značky a celý zápis tokenů/cookies probíhají pod společným Web Lockem
  `ff_session_tokens`; stejný zámek drží změna značky a mazání při odhlášení.
  Platí i pro běžné přihlášení a odmítnutí obnovy. Volající čekají na dokončení;
  přesměrování po odhlášení nastane až po vyčištění. Bez Web Locks nebo při odmítnutí
  zámku se tokeny nezapíšou a přihlášení selže. Vitest vynucuje proložení přímo
  po přečtení značky ve dvou instancích modulu i čekající zápis po dokončeném odhlášení.
- Neověřený vstup na `/jrc-sso` nemaže dosavadní tokeny. Automatické `/jrc-logout`
  vyžaduje jednorázové oprávnění ve fragmentu a odpovídající SHA-256 cookie z POST
  Brightbeanu chráněného CSRF; fragment hned odstraní a cookie spotřebuje. Přímá
  návštěva nebo neplatné oprávnění nabídne potvrzení, bez změny relace.
- `lib/app-destination.ts` povolí pouze aplikační cesty `/projects`, `/assets`,
  `/notifications`, `/settings` a jejich podcesty. SSO ani `app/(auth)/login/page.tsx`
  tak nepřejdou přes přihlašovací či jinou veřejnou stránku na další přesměrování.
  Login kontroluje i vlastní parametr `from` a při neplatném cíli použije `/projects`;
  nepřijímá cizí adresy, kódované obcházení ani tečkové segmenty. Next router dostává
  cestu bez basePath. API také odmítá vstupenky s cílem mimo uvedené aplikační cesty.
- Regresní Vitest/pytest a Chromium ověřují všechny tři nálezy, včetně skutečně
  pozdržené úspěšné výměny i obnovy ve dvou kartách a celého řetězce s `from`.
- Veřejné `/jrc-sso` a `/jrc-logout`; middleware rozpozná cesty s předponou i bez předpony
  `NEXT_PUBLIC_BASE_PATH`, před kontrolou instalace a přihlášení.
- `/jrc-sso` okamžitě odstraní fragment i query z aktuální historie, porovná SHA-256 vstupenky
  s jednorázovou cookie nastavenou po CSRF POST v Brightbean a cookie smaže. Výměnu zavolá
  jednou přímo bez starých tokenů/cookies a bez opakování při 401. Úspěch uloží přes `setTokens`
  a nahradí položku historie cílem výhradně z odpovědi API, znovu ověřeným a doplněným o basePath.
  Procentní kódování, fragmenty, tečkové segmenty a externí adresy nejsou povolené.
- Chyba má prostou stránku s návratem do JR Content, bez vstupenky a technického výpisu.
  Oprávněné nebo potvrzené `/jrc-logout` smaže tokeny z localStorage a cookies včetně `ff_setup_done`, kontrolní cookie
  a vrátí se na pevnou `/accounts/login/`. Pomocná `removeTokens` nemaže historii navigací
  na původní login; dosavadní `clearTokens` zachovává chování pro upstream.
- Vitest pokrývá stránky (včetně React StrictMode), middleware, odmítnutí podstrčené vstupenky,
  jednorázové volání výměny a nezávislou kontrolu cíle. Živý Chromium scénář a data jsou
  v `scripts/F5-B03/`; snímky v ignorované složce `ops/screenshoty/F5-B03/`.

## 2026-10-23 — F2-B11

- Společný Compose vypíná telemetrii i hlášení npm/pip. Také `jrc-dev` běží jen
  ve vnitřní síti. Místní brány před spuštěním aplikace odeberou výchozí trasy
  a zvýšená oprávnění; DNS nepředává cizí dotazy. Test izolace zahrnuje všechny služby.
- Test brány výslovně volá health API z prohlížeče, blokuje cizí HTTP i WebSocket
  požadavky a nespoléhá na vedlejší požadavek přihlašovací stránky.
- `make test-ff-vitest` používá jednoho pracovníka pro nižší zátěž při souběžném
  ověřování bloků. Časové limity upstream testů zůstávají původní.

## 2026-10-23 — F2-B02

Provozní úpravy žijí převážně v kořeni JR Content. Po review je změněn také
`apps/web/app/layout.tsx`: `next/font/local` a původní DM Sans v `app/fonts/`
s licencí SIL OFL 1.1 a přesným zdrojem/otisky v jeho README. Při prvním spuštění
ani sestavení se písmo nestahuje. Přehled úprav:

- `docker-compose.yml`: API, převodní worker (fronta `transcoding`, 4 CPU), údržba
  (`maintenance`), jediný beat, Next.js pod `/video` a Redis s trvalou frontou.
  Všechny aplikační služby (také API, web, Brightbean, PG a Garage) mají jen
  vnitřní síť bez internetu. Jediná dočasná `jrc-ff-gate` přenáší místní porty
  na pevné vnitřní TCP cíle; výjimku vývojového `jrc-dev` odstranil F2-B11.
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

## 2026-10-23 — F2-B04

- Společný vstup Caddy na `JRC_APP_PORT` vydává web pod `/video` a API pod
  `/video-api/`; při předání API odstraní předponu. Uvicorn zná veřejnou
  předponu přes `--root-path`, takže přesměrování a OpenAPI zůstanou pod ní.
  Caddy jako důvěryhodná proxy přepisuje `X-Real-IP` adresou klienta; API podle
  ní omezuje počet požadavků a podvržená hlavička limit neobejde.
- Zabezpečení společného vstupu (review): `DISABLE_DOCS=true` vypíná veřejné
  `/docs`, `/redoc` a `/openapi.json`; `/video-api/admin/*` vrací 403 (správa
  instance jen vnitřně). API dostává požadavky bez hlavičky `Cookie` (ověřuje
  Bearer token). Všechny odpovědi mají `X-Content-Type-Options: nosniff`
  a `Referrer-Policy: same-origin`; `/video` smí být vložen jen do stejné adresy
  (`X-Frame-Options: SAMEORIGIN`, `frame-ancestors 'self'`), Brightbean nikam
  (`DENY`). Záložka Users v nastavení správce FreeFrame proto v prohlížeči nefunguje.
- `NEXT_PUBLIC_API_URL`, `FRONTEND_URL` a `CORS_ALLOW_ORIGINS` používají
  společný místní port. `NEXT_PUBLIC_BASE_PATH=/video` zůstává zapnuté.
  Serverový `apps/web/middleware.ts` používá přednostně `API_INTERNAL_URL`,
  stejně jako načítání značky na serveru, místo veřejné adresy prohlížeče.
  Při prvním úspěšném ověření dokončené instalace dál provede kontrolu
  přihlášení; nastavení cache cookie už nepřeskočí tuto kontrolu.
  Regresní testy zahrnují anonymní první návštěvu, relaci, nedostupné API
  i nedokončenou instalaci.
- Proxy předává `/video` i `/video/*`. Next.js tak může standardně
  přesměrovat `/video/` na `/video`, bez smyčky mezi aplikací a proxy.
- API middleware pro dokončení instalace a omezení počtu požadavků čte
  cestu bez `root_path`. Výjimky pro health, setup, upload a HLS tím platí
  i pod `/video-api`; dvanáct regresních případů ověřuje obě nasazení
  a zachování odmítnutí chráněných cest.
- Dočasné místní porty webu/API i port PostgreSQL odstraněny. `jrc-ff-gate`
  zůstává pouze pro S3: podepsané adresy S3 se předávají přímo a beze změny.
  Web, API a workery dál mají jen vnitřní síť bez internetu.
- Ověřovací skripty brány, nahrávání a časového štítku používají společný vstup.
  `scripts/test-video-proxy.cjs` ověřuje skutečné heslové přihlášení,
  chybové odpovědi/CORS a HLS přehrávání v Chromium na 1440/390 px;
  video vyrábí FFmpegem a pracuje jen v `jrc-f2b04`.

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
  testovací domény místních dat jako `example.invalid` (ukázkový klient): přesně jeden `@`,
  bez bílých a řídicích znaků a `,;<>"`. Ukládá se malými písmeny a konflikt s cizím účtem
  se hledá bez ohledu na velikost písmen (409). Profil a blokace
  se aktualizují při každém volání. Projekt je týmový a neveřejný, vlastníkem je servisní účet.
- Identifikátory se odvozují pomocí UUIDv5 ze stabilního jmenného prostoru a UUID Brightbean.
  Ztracená místní vazba se tak může obnovit bez nového účtu/projektu. Transakční zámek
  PostgreSQL chrání souběžné založení. Konfliktní e-mail se odmítá (409), nepřebírá se
  cizí účet podle e-mailu; privilegovaný nebo smazaný účet/projekt se nepřepisuje.
- `POST /jrc-sync/accounts/{source_uuid}/deactivate`: deaktivuje řízený účet podle odvozeného
  UUID (při smazání uživatele v Brightbean), účet nikdy nezakládá; chybějící vrací `found: false`.
- Role a odebrání členství používají stávající projektové API. Nové uživatelské
  rozhraní nevzniká; přihlášení jednou vstupenkou a omezení běžného FreeFrame jsou další bloky F5.
- `tests/test_jrc_sync.py`: přístup běžného uživatele/cizího správce, bezpečné založení,
  stabilní identita, konflikt e-mailu, ochrana privilegovaných a smazaných záznamů,
  neveřejný projekt a servisní vlastník. Živý test v kořeni JR Content ověřuje i skutečné role
  klienta, zákaz nahrávání, oddělení klientů a odvolání přístupu při platném přístupovém tokenu.

## 2026-10-23 · F5-B02

- `apps/api/services/jrc_ticket.py`, `apps/api/routers/jrc_auth.py`, registrace v `main.py`:
  `POST /auth/jrc-exchange {"ticket": …}` vymění vstupenku Brightbean za běžné tokeny FreeFrame.
  Vstupenka `v1.<data>.<podpis>`: HMAC-SHA256 klíčem `JRC_FF_SSO_SECRET` (jen v `.env`, generuje
  `make env`, dostává ho jen `jrc-ff-api`). Podpis se ověří v konstantním čase dřív, než se data
  přečtou. Platnost nejvýš 60 s (kontroluje FreeFrame, ne jen Brightbean), čas vydání nejvýš 5 s
  v budoucnosti, přesné složení údajů (`v`, `iss`, `aud`, `acct`, `sub`, `iat`, `exp`, `nonce`,
  volitelně `next`).
- Jednorázovost: otisk `nonce` se zapíše do Redis `SET NX EX 120` hned po ověření podpisu a času,
  ještě před dotazem do DB; vstupenka je spotřebovaná i při odmítnutí účtu. Souběžné pokusy uspějí
  nejvýš jednou. Nedostupný Redis = 503 (bez tokenů), nikdy nepouští.
- Vazba na účet: `sub` musí být UUIDv5 odvozené z `acct` (stejný jmenný prostor jako `/jrc-sync`),
  takže vstupenka nikdy necílí na účet založený mimo JR Content. Účet musí existovat, nebýt smazaný,
  mít stav `active` a nesmí být superadmin ani servisní účet.
- Vazba na cíl: `aud=web` (přihlašovací stránka, F5-B03) vrátí přístupový i obnovovací token
  a ověřené `next` (jen cesta uvnitř FreeFrame, žádný jiný web). `aud=api` (server Brightbean
  jménem uživatele) vrátí jen přístupový token a jde použít jen na vnitřní adrese
  `jrc-ff-api:8000`; přes společný vstup (Caddy nastavuje `X-Real-IP`) se odmítne.
- Všechna odmítnutí jsou stejná odpověď 401 `Invalid or expired ticket`; důvod jde jen do logu
  (bez vstupenky a tokenů). Odpověď má `Cache-Control: no-store`. Chybějící nebo krátký klíč = 503.
- Oprava po review: tělo požadavku se čte ručně (ne přes model FastAPI), protože standardní
  odpověď 422 by vrátila odmítnutý vstup, tedy platnou nespotřebovanou vstupenku. Jakýkoliv jiný
  tvar než `{"ticket": "<text>"}` (seznam, objekt, další klíče, neplatný JSON, větší než 2112 B)
  je stejná 401; i 401 a 503 mají `Cache-Control: no-store`.
- `jrc-redis` běží s `--appendfsync always`: každý `SET NX` je na disku dřív, než se vrátí tokeny,
  takže použitá vstupenka nejde znovu uplatnit ani po pádu stroje. Kontroluje `make test-isolation`.
- `tests/test_jrc_exchange.py` (95 testů; mimo níže uvedené i chybné tvary těla bez ozvěny): platné web/api, dvojí použití, souběh 8 požadavků,
  prošlá, příliš dlouhá, z budoucnosti, špatný podpis (cizí klíč, `JWT_SECRET`, změněný obsah,
  useknutí, nekanonické base64, token FreeFrame místo vstupenky), cizí, neřízený, neexistující,
  vypnutý, čekající, smazaný účet, superadmin a servisní účet, cíl `api` přes veřejný vstup,
  nebezpečné `next`, výpadek Redis, chybějící klíč, skutečný Redis `SET NX` a společný testovací
  vektor s Brightbean.
## 2026-10-24 — F2-B05

- `apps/api/config.py`: nastavení `CORS_DEV_ORIGINS` (výchozí `true` jako upstream). JR Content
  nastavuje `false`, takže API i CORS bucketu `jrc-video` povolují jen původ aplikace
  (`FRONTEND_URL`, `CORS_ALLOW_ORIGINS`), bez `localhost:3000/3001`. Upstream testy CORS běží
  s `CORS_DEV_ORIGINS=true`; nový test `test_startup_cors_without_dev_origins`.
- Stránka `/video` má ve společném vstupu vynucenou CSP s přesným původem S3 pro náhledy,
  segmenty HLS a nahrávání po částech (`docker/Caddyfile`).

## 2026-10-25 — F5-B08

- Vložený režim (`?embed=1`): `?version=<id>` otevře zadanou verzi místo nejnovější (`ReviewProvider.preferredVersionId`,
  `lib/embed-version.ts`; cizí id se ignoruje). Čas v přehrávači, u komentářů a v hlavičce spodního panelu je ve vloženém
  režimu `m:ss` (výběr formátu v přehrávači zůstává). Mimo vložený režim beze změny.
- Testy: `lib/__tests__/embed-version.test.ts`, `components/review/__tests__/review-provider-embed-version.test.tsx`,
  `app/(dashboard)/projects/[id]/assets/[assetId]/__tests__/embed-version.test.tsx`.

## 2026-10-25 — F5-B09

- Přihlašovací JWT už se pro SSE neposílá v URL. Web si ho před připojením vymění přes Bearer
  API za tiket platný 5 minut, svázaný s uživatelem, projektem, verzí relace a účelem `sse`.
  Obnova tiketu po vypršení neobnovuje přihlašovací relaci; změna verze relace dál vyžaduje
  přihlášení a odebrání členství ukončí stream.
- HLS přístupové tokeny platí 30 minut, jsou svázané s prefixem videa a mají účel `hls`. Verze
  tokenu se zvýšila na 4, aby se dříve vydané 24hodinové tokeny zneplatnily při nasazení. Odkazy
  na segmenty neplatí déle než HLS token; řízené účty navíc dál ověřují členství při každém
  manifestu a segmentu.
- Caddy přístupový záznam odstraňuje hodnoty `token`, `ticket` a podpisů AWS/Google. Filtr
  záznamu uvicorn obdobně maskuje tokeny, tikety a podpisy bez odstranění ostatních údajů URL.
- Přidány testy platnosti, účelového omezení, odmítnutí přihlašovacího JWT v SSE URL a maskování
  API záznamů. Celoaplikační ověření poběží po dokončení implementace.
