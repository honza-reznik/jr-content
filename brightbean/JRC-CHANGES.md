# Úpravy JR Content

Základ: Brightbean Studio `48801fdcd1009e6ed4863fa9942222a478a297e2`, import F2-B01.
Licence této odvozené verze: AGPL-3.0; původní licence a autorská práva zůstávají zachována.

## 2026-10-22 · F2-B01

- `config/settings/jrc_offline.py`: vypnutí Sentry, Unsplash, Intelligence, Google přihlášení,
  externího Redis a pošty ven před importem základu; společný seznam našich aplikací.
  Upstream nemá samostatnou telemetrii; jediný nalezený monitorovací klient je Sentry.
- `config/settings/jrc.py`: lokální vývoj, databáze `brightbean`, tajemství jen z prostředí,
  konzolová pošta, místní média, vypnuté poskytování sociálních sítí (vypínač využije F2-B07).
- `config/settings/jrc_test.py`: testovací nastavení upstreamu se stejnými našimi aplikacemi
  a vypnutými odchozími integracemi. Nastavení jednotlivých funkcí mohou testy dočasně přepsat.
- `tests/test_jrc_settings.py`: kontrola, že ani předaná konfigurace externích integrací
  nespustí Sentry a nezapne jejich běh v nastavení JR Content.
- `.dockerignore`: do sestavení se nepřenášejí místní tajemství, média, závislosti ani výstupy.

Stávající soubory upstreamu se v tomto bloku nemění. Původní Dockerfile se používá beze změn.
Compose, založení databáze a příkazy vývoje jsou v kořeni JR Content, mimo upstream podsložku.

## 2026-10-23 · F2-B07

- `apps/jrc_channels/`: nová aplikace s profilem `ChannelProfile` (`manual`/`custom`), službou
  `create_manual_channel` bez OAuth a bez plánování statistik, službou `set_channel_enabled`
  a ochranou stavu a tokenů při `SocialAccount.save()`. Vypnutí skryje kanál ve výběrech
  Brightbean a zachová dosavadní příspěvky. Hromadné `QuerySet.update()` signály Djanga
  obchází; pro místní změny stavu je povinná naše služba.
- `apps/jrc_channels/migrations/0001_initial.py`, `0002_hide_custom_oauth.py`: tabulka profilů
  a skrytí vlastního kanálu na stránce připojení přes OAuth.
- `apps/credentials/models.py`, `apps/credentials/migrations/0006_*.py`,
  `apps/social_accounts/migrations/0026_*.py`: volba `custom` v existujících polích sítí.
  Poskytovatel pro vlastní kanál se neregistruje.
- `config/settings/jrc_offline.py`: registrace naší aplikace ve vývojovém i testovacím nastavení.
  Nastavení `jrc.py` a `jrc_test.py` již z F2-B01 obsahují vypnutý přepínač sítí.
- `providers/__init__.py`: `ProvidersDisabledError(ValueError)`; vypínač
  `JRC_SOCIAL_PROVIDERS_ENABLED` zastaví vytvoření poskytovatele před jeho inicializací.
  Bez nastavení vypínače zůstává původní chování upstreamu.
- `apps/publisher/engine.py`: běžná publikace i opakované pokusy vyřadí profily `manual` a `custom`;
  jejich plánované příspěvky čekají na ruční publikaci.
- `apps/composer/views.py`: Pinterest a TikTok při vypnutých sítích vracejí HTTP 200
  s `available: false`, prázdnými možnostmi a srozumitelným důvodem.
- `apps/social_accounts/youtube_compliance.py`: `ProvidersDisabledError` ukončí i následné
  mazání starých OAuth dat. Regresní test prokázal, že bez této výjimky by původní kontrola
  po 29 dnech smazala ruční YouTube kanál i jeho obsah; zákaz poskytovatele se nesmí vykládat
  jako neplatný souhlas skutečného účtu.
  Oprava po review: `purge_connection`, `purge_organization`, denní úloha, `check_connection`
  i přímý úklid starých dat vynechávají účty s místním profilem. Ochrana nezávisí na vypínači
  poskytovatelů a platí také pro dříve naplánované úlohy a explicitně předaného poskytovatele.
  Naplánování zrušení organizace tak během ochranné lhůty zachová ruční YouTube i jeho příspěvky.
- `conftest.py`: původní testy poskytovatelů a integrací je mají dočasně zapnuté, aby mohly dál
  ověřovat původní funkce s náhradami síťových odpovědí. Nastavení běžící aplikace se nemění.
  `apps/jrc_channels/tests/conftest.py` ponechává u našich testů vypínač vypnutý.
- `apps/social_accounts/tests/test_models.py`: vlastní kanál ověřuje nepřítomnost OAuth
  poskytovatele a souhlasu k odvolání místo jeho povinného vytváření.
- `apps/social_accounts/tests/test_sqlite_oauth_refresh.py`: izolované testovací databáze
  vytvářejí při zapnuté aplikaci také tabulku profilů, kterou čte ochranný signál.
  Původní ověření souběžné obnovy tokenů zůstává včetně všech osmi původně selhávajících případů.
- `apps/jrc_channels/tests/`: ověření založení, ochrany a vypnutí kanálů; statický průchod AST
  (strom struktury zdrojového kódu) s přesným seznamem dosavadních HTTP volání a jedinou výjimkou
  pro čtení `auth_type` v `social_accounts/views.py`; dynamické ověření úloh, signálu statistik,
  publikace, obou endpointů composeru a inboxu se zákazem síťových spojení.
  Oprava po review: statický audit sleduje i anotovaná přiřazení, řetězce aliasů,
  `getattr`, `importlib.import_module` a `__import__`; dynamická ochrana blokuje také
  `socket.getaddrinfo` a `socket.create_connection`. Pinterest endpoint se ověřuje
  na původním typu účtu. Služba ručních kanálů nově přijímá výhradně IG, FB, oba druhy
  LinkedIn, TikTok, YouTube a `custom`; ostatní sítě odmítne ještě před uložením účtu.

## 2026-10-23 · F2-B10

- `apps/jrc_ui/`: přepínače funkcí, kontrola přímých adres před přihlášením a CSRF,
  kontext šablon bez onboardingu, vlastní stránka About a místní úplný text licence.
  Přidané testy ověřují GET/POST blokování, hlavní obrazovky a vypnutí RSS bez DNS/HTTP.
  Po review společný seznam `FEATURE_SURFACES` přiřazuje webové cesty, REST prefixy
  a MCP nástroje k týmž vypínačům; vyhodnocuje se při každém požadavku.
  `tests/test_api_features.py` ověřuje i platný API klíč, všechny operace inboxu/odpovědí
  a statistik, obě MCP adresy, dávky, notifikace a vratnost přepínačů.
- `config/settings/jrc.py`: `JRC_FEATURES` vypíná inbox, statistiky, Trustpilot,
  onboarding/Get started, nápady, klientský portál, Intelligence, připojení sítí,
  Unsplash, RSS a externí přihlášení (Google, jehož klíč je už vypnutý od F2-B01). CSP povoluje pouze místní zdroje; obrázky a média také data/blob.
- `config/settings/jrc_test.py`: stejná vrstva rozhraní; původní testy mohou nadále
  testovat upstream funkce (`JRC_FEATURES = {}`); naše sada explicitně přepíná
  `MVP_FEATURES`, stejně jako skutečně běžící aplikace.
- `config/urls.py`: přidané `/about/` a `/about/license/`, pouze s nastavením JR Content.
- `apps/composer/views.py`: RSS při vypnutí končí před DNS a HTTP požadavkem.
- `apps/api/api.py`: název dokumentace API JR Content; kontrola vypnutých REST funkcí
  před ověřením klíče, i bez middleware.
- `apps/mcp/tools.py`: stejný dynamický filtr při výpisu i vyhledání nástroje;
  vypnuté nástroje inboxu a statistik nelze objevit ani přímo zavolat přes MCP.
- `apps/social_accounts/tests/test_views.py`: očekávané texty potvrzení odpojení
  používají JR Content; věcné kontroly jednotlivých typů odpojení zůstávají stejné.
- `templates/base.html`: podmíněná navigace, skryté nápady/onboarding/Trustpilot,
  místní JS/CSS místo CDN, značka a favicon JR Content, odkaz na About a licenci.
- `templates/account/login.html`, `templates/account/signup.html`: skryté nefunkční externí
  přihlášení; přímé Google login/callback URL jsou také vypnuté.
- `templates/layouts/workspace_settings.html`: skrytý klientský portál.
- `templates/calendar/partials/_no_channels_empty_state.html`: místní vytvoření příspěvku
  nahrazuje tlačítko připojení sítě, když je tato funkce vypnutá.
- `templates/composer/compose.html`: skrytý Unsplash, návrat do kalendáře místo nápadů.
- `templates/social_accounts/list.html`, `templates/social_accounts/partials/_account_card.html`:
  skryté připojení, opětovné připojení a obnovení webhooků.
- `templates/ninja/swagger_cdn.html`: místní Swagger JS/CSS a favicon, vypnutý vzdálený
  validátor. `static/vendor/`: konkrétní verze flatpickr, Chart.js, SortableJS a Swagger UI,
  jejich původní licence/NOTICE a kontrolní součty v přiloženém README. Ze Swagger CSS
  a Chart.js odstraněny pouze odkazy na nepřibalené vývojové mapy, které blokovaly čisté sestavení.
- `static/img/jrc-logo.svg`, `templates/jrc_ui/`: vlastní značka, informace o odvozené
  verzi a licence; zachován odkaz na původní Brightbean Studio. Původní logo a upstream
  kód zůstávají ve zdrojích. Hlavičky označují upravené soubory a datum.
- Ostatní upravené HTML šablony (`account/`, `accounts/`, `analytics/`, `approvals/`,
  `calendar/`, `client_portal/`, `composer/`, `credentials/`, `intelligence/`, `layouts/`,
  `media_library/`, `members/`, `notifications/`, `oauth2_provider/`, `organizations/`,
  `partials/`, `workspaces/`): pouze jméno JR Content v titulcích a textu místo Brightbean,
  případně vlastní logo/favicon. Změněné šablony mají datovanou hlavičku.

Skrytá upstream karta Trustpilot zůstává beze změny (včetně původního textu),
aby ji nadále ověřovaly původní testy; v MVP se vůbec nevykresluje.

## 2026-10-23 · F6-B01

- `apps/jrc_production/views.py`, `templates/jrc_production/content_detail.html`:
  záložka Caption ukládá samostatné prosté texty kanálů jako verzované dokumenty
  `jrc_texts`, kontroluje limit podle pravidel účtu a promítá uložený text do
  `platform_specific_caption`. Zastaralá karta nebo souběžný koncept vrací chybu
  místo přepsání novější verze; změny mezi posledními verzemi se zobrazují po slovech.
- `apps/jrc_production/tests/test_captions.py`: oprávnění, limit IG včetně emoji,
  vznik verze, kopie do varianty, porovnání slov a odmítnutí zastaralého uložení.

## 2026-10-23 · F2-B12

- `apps/social_accounts/tests/test_youtube_compliance.py`: retenční test používá čtyři pevné
  okamžiky v časovém pásmu Europe/Prague a datum historie odvozuje z Los Angeles, stejně jako
  úklid aplikace. Výsledek testu tak nezávisí na hodině spuštění.

Žádná upstream funkce, původní licence ani autorské upozornění nebyly odstraněny.
Vypínače lze změnit v našem nastavení bez odstranění nebo obnovení upstream kódu.
Ověřovací skripty a snímky jsou v kořeni JR Content (`scripts/F2-B10/`,
`ops/screenshoty/F2-B10/`), mimo upstream podsložku. Žádné skutečné sociální účty.

## 2026-10-23 · F3-B01

- `apps/jrc_production/`: nové modely `PostProduction` (pouze čtyři výrobní fáze),
  `StageChange` (historie změn) a `ManualPublication` (údaje o ručním zveřejnění kanálové kopie),
  včetně úvodní migrace. Databázové omezení nepovolí uložit schvalovací či publikační stav
  do výrobní fáze. Smazání uživatele zachová historii, smazání příspěvku odstraní jeho rozšíření.
- `apps/jrc_production/services.py`: `pipeline_stage(post)` přesně převádí souhrnný stav
  Brightbean podle ADR-002 b1, výrobní fázi čte jen pro `draft`. Starší koncept bez rozšíření
  čte jako Idea bez zápisu do databáze. `create_content(workspace, title, user)` založí obsah,
  výrobní rozšíření a koncept pro každý zapnutý kanál daného klienta v jedné transakci
  (při chybě se neuloží žádná část). Bez zapnutého kanálu vrátí validační chybu, aby nevznikl
  obsah skrytý v upstream seznamech. Oprávnění řeší volající rozhraní v navazujících blocích.
- `config/settings/jrc_offline.py`: registrace nové aplikace pro běh i testy.
- `apps/jrc_production/tests/`: převody všech stavů včetně vícekanálových kombinací,
  návrat ze schválení do střihu, výběr zapnutých kanálů správného klienta, zrušení neúplného
  zápisu při chybě a životnost historie. Síťová volání jsou v těchto testech blokována.

Stavový automat a modely upstreamu se v tomto bloku nemění. Nástěnka, změny fází přes rozhraní
a služba označení ruční publikace patří navazujícím blokům F3-B02 a F8-B02.


## 2026-10-23 · F3-B02

- `apps/jrc_production/services.py`: `create_content()` sama ověřuje aktivní členství,
  oprávnění zakládat obsah a dostupnost klienta; místní obsah smí založit i vestavěná
  klientská role bez změny globálních oprávnění composeru/API. `move_content()` zamyká
  obsah při přesunu, kontroluje oprávnění, původní fázi a všechny kanálové kopie. Výrobní
  fáze zaznamenává do historie; kontrolu a schválení deleguje službám Brightbean.
  Plánování schválených ručních i vlastních kanálů používá stavový automat Brightbean
  a `sync_post_scheduled_at()`; nepoužívá bránu pro přímé plánování skutečných účtů,
  která odmítá i schválený obsah v povinném schvalovacím režimu. Ruční publikaci
  tato nástěnka nenahrazuje (F8-B02).
- `apps/jrc_production/urls.py`, `views.py`, `templates/jrc_production/board.html`:
  nová nástěnka `/board/` s osmi odvozenými fázemi, filtrem klienta a kanálu,
  formulářem pro založení a přesuny s kontrolou oprávnění. Telefon má volbu zobrazené
  fáze, všechny akce jsou dostupné i bez přetahování. Filtr nikdy nerozšiřuje přístup.
- `config/urls.py`: nástěnka a její akce registrované jen v JR Content nastavení.
- `templates/base.html`: odkaz Content board v navigaci JR Content.
- `apps/jrc_production/tests/`: oprávnění služeb i HTTP (cizí prostor, role, vlastní
  oprávnění, archivovaný klient), filtry, víc kanálů, historie, schvalování včetně
  klientského kroku, atomické plánování, časová pásma, zastaralé přesuny, CSRF.

Upstream role, schvalovací služby a stavový automat zůstávají beze změny.
## 2026-10-23 · F2-B08

- `apps/jrc_channels/forms.py`, `views.py`, `urls.py`, `templates/jrc_channels/`: profil klienta
  s ručním založením kanálu, názvem a odkazem, vratným zapnutím/vypnutím přes službu F2-B07.
  Změny vyžadují členství v daném workspace a oprávnění `manage_social_accounts`; cizí kanál
  nelze upravit přes podvržené ID. Odkazy se jen validují a ukládají, nestahují se ani neověřují DNS.
- `apps/jrc_channels/management/commands/jrc_seed_demo.py`: opakovatelné demo Ella, Instagram,
  Forendors s adresami `example.invalid`, bez hesla a bez skutečných účtů. Volba `--owner-email`
  dá přístup existujícímu místnímu uživateli. Opakování zachová vlastní názvy, odkazy, stav i obsah.
- `config/urls.py`: adresa `/workspaces/<id>/profile/` jen v režimu JR Content.
- `templates/base.html`, `templates/layouts/workspace_settings.html`: dostupný odkaz
  „Client profile“ v hlavní navigaci, nastavení a u kanálů; původní upstream režim zůstává zachovaný.
- `apps/jrc_channels/tests/test_profile.py`, `test_seed_demo.py`: oprávnění, oddělení klientů,
  validace odkazů, vratné vypnutí bez ztráty obsahu a opakované demo se zákazem sítě.
  Vizuální a funkční scénář pro 1440 a 390 px je `scripts/F2-B08/check-profile.cjs` v kořeni projektu.
## 2026-10-23 · F2-B03 (oprava po bezpečnostním review)

- `config/urls.py`: místní knihovna médií už není veřejná. Originály, náhledy
  a historické verze předává chráněnému Django view. Veřejné avatary a ikony
  kontrolují i normalizovanou cestu, aby přes `../` nešlo obejít ochranu knihovny.
- `apps/media_library/file_views.py`: přihlášení a kontrola skutečného vlastníka
  souboru v databázi; členství ve workspace, u společné knihovny členství v její
  organizaci. Neznámé soubory a cizí workspace vracejí 404. Odpovědi zakazují
  ukládání do cache, oprávnění se ověřuje při každém požadavku včetně HEAD.
  Proxy pouze předává požadavek aplikaci a nemá přístup k disku s médii.
- `tests/test_media_routing.py`: anonymní přístup, cizí workspace téže organizace,
  oprávněný člen, originály i všechny náhledy/verze, společná knihovna jiné
  organizace, odvolání členství, odhlášení, osiřelé soubory a obcházení přes
  veřejné cesty. Stávající kontroly routování a ochrany před únikem `.env` zachovány.
## 2026-10-23 · F3-B03

- `apps/jrc_production/views.py`, `urls.py` a `templates/jrc_production/content_detail.html`:
  přidána stránka obsahu s kartami Script, Video, Caption, Approval a History. Historie
  spojuje výrobní `StageChange` a `ApprovalAction`; ostatní připravené karty ukazují prázdný
  stav. Stránka ukazuje souhrnnou výrobní fázi, stav příspěvku, kanály a datum publikace.
- Stránka je čitelná členům workspace včetně klienta. Nečlen dostane zákaz přístupu;
  klient ani divák nevidí interní týmové poznámky. Úprava obsahu odkazuje do původního composeru.
- `config/urls.py` registruje lokální cestu detailu pod workspace.
- `apps/jrc_production/tests/test_content_detail.py` ověřuje admina, klienta, zákaz přístupu
  nečlenovi, citlivost interních poznámek a obě části historie.
- Oprava review: komentáře `ApprovalAction` zobrazí klientovi jen tehdy, když je vytvořil klientský
  nebo čtenářský člen stejného workspace; poznámky interních týmových kroků zůstávají skryté.
  HTTP testy kontrolují obě role a interní `PostComment` se na detailu nevypisuje.

## 2026-10-23 · F4-B01

- `apps/jrc_texts/`: dokumenty scénářů a kanálových popisků, neměnné uložené verze,
  omezení jedné rozpracované verze a unikátního pořadí. Služby ověřují členství v prostoru,
  oprávnění zapisovat a vlastníka rozpracované verze. Editace vychází z konkrétní poslední
  uložené verze; uložení, průběžný zápis a zahození vyžadují UUID a očekávanou revizi.
  Konflikt má `status_code = 409` pro navazující HTTP rozhraní. Shodný obsah novou verzi nezaloží.
  Oprava po review: všechny zápisy vyžadují `create_posts` a autora příspěvku nebo
  `edit_others_posts`, stejně jako composer (vestavěný klient zatím pouze čte).
  Koncept po smazaném uživateli nebo odebrání jeho členství smí zahodit správce prostoru
  (owner/manager či vlastní role s `manage_workspace_settings`), pokud smí upravit příspěvek;
  nový `edit` takový koncept nahradí pod stejným zámkem dokumentu. Živý cizí koncept
  správce nepřevezme a kontrola UUID i revize při zahození zůstává povinná.
- `apps/jrc_texts/migrations/0002_protect_final_versions.py`: PostgreSQL trigger odmítá změnu
  a smazání uložené verze při potvrzení transakce. Odložená kontrola dovolí kaskádu při smazání
  celého dokumentu/obsahu a anonymizaci autora po skutečném smazání jeho účtu. Samostatný
  přímý SQL zásah do uložené verze zůstává zakázaný.
- `apps/jrc_texts/editor/`: serverové schéma Tiptap a převod Markdownu převzaté z prototypu F1-B03.
  Python spouští omezený místní Node proces s JSON přes standardní vstup/výstup, bez sítě.
  `Dockerfile` instaluje uzamčené závislosti a skripty do `/opt/jrc-texts`, mimo zdrojový bind mount.
- `config/settings/jrc_offline.py`: registrace aplikace při běhu i testech.

Editor v rozhraní, porovnání verzí, kopie popisku do Brightbean a HTTP/MCP rozhraní patří dalším blokům.
## 2026-10-23 · F5-B01

- `apps/jrc_video/`: vazby `VideoProject` (workspace → projekt) a `VideoAccount`
  (uživatel → účet), migrace a synchronizace přes interní API servisním účtem.
  Signály členství, profilu uživatele a archivace klienta se provádějí po potvrzení
  databázové změny. Odstranění účtu/prostoru nejprve odebere vzdálený přístup;
  média FreeFrame zůstávají zachovaná. Neznámé a vlastní role dostanou pouze viewer.
- Převod rolí dle ADR-003: owner/manager/editor/contributor → editor, client → reviewer,
  viewer → viewer. Synchronizace odstraní i přebytečná vzdálená členství, zachová
  servisního vlastníka a udržuje projekty neveřejné. Blokování účtu vypne účet FreeFrame.
- `python manage.py jrc_sync_freeframe` opraví všechny existující účty, projekty a členství;
  `--pending` zpracuje jen uložené neúspěšné změny `VideoSyncRequest`. Při chybě vrátí nenulový
  kód a záznam ponechá k opakování. Po obnovení dostupnosti API spusť příkaz znovu.
  Při výpadku API změna členství v Brightbean zůstává uložená a vzdálená oprávnění se
  změní až při úspěšné opravě. Pravidelné automatické opakování tento blok nezavádí.
  Selhání po uložení (např. blokace uživatele bez členství) se jen zapíše do logu,
  uživatel nedostane chybovou stránku.
- Přístupové a obnovovací tokeny se sdílejí jen v paměti procesu; neukládají se do DB,
  logů ani KB. Heslo servisního účtu přichází pouze z prostředí Dockeru. HTTP klient
  používá časové limity, nesleduje přesměrování a do chyb nekopíruje těla odpovědí.
- `tests/test_sync.py`: role, opakování, oprava členství, blokace, archivace, odstranění,
  transakce, neúspěšné pokusy, bezpečné chyby a obnova tokenu. Živé ověření obou aplikací
  je `scripts/F5-B01/check-sync.sh` v kořeni JR Content (jen projekt `jrc-f5b01`).
- `apps/jrc_channels/tests/test_network_inventory.py`: eviduje nový interní HTTP vstup
  `FreeFrameClient._send`, směrovaný pevně na službu `jrc-ff-api:8000` bez přesměrování.
- Oprava review (F5-B01): chyba jednoho účtu nebo člena nezastaví synchronizaci ostatních.
  Odebírání členů běží první a každý krok samostatně; chyby se posbírají, `VideoSyncRequest`
  zůstane k opakování a na konci se vrátí jedna souhrnná chyba (`last_error` max. 255 znaků).
  Účet s neúspěšným PUT, ale známou vazbou, se dál odebírá/omezuje podle Brightbean, nic navíc
  nedostane. `VideoAccount` se ukládá v krátké samostatné transakci hned po úspěšném PUT.
- `VideoAccountSyncRequest` (migrace 0002): záznam k opakování pro účet bez členství
  (blokace); `--pending` ho opraví. Smazání uživatele volá `POST /jrc-sync/accounts/{id}/deactivate`
  i bez místní vazby (podle odvozeného UUID, nikdy podle e-mailu). Bez vazby selhání smazání
  nezastaví (zapíše se varování), s vazbou ano.
- Při startu varování v logu, když chybí `JRC_FF_SYNC_EMAIL` nebo `JRC_FF_SYNC_PASSWORD`
  (synchronizace je pak vypnutá). Hodnoty se nevypisují.
- Provozní poznámka: plná synchronizace (`jrc_sync_freeframe` i každá změna prostoru)
  odebere z projektu FreeFrame i členy přidané ručně mimo JR Content.

## F2-B11 — offline provoz

- `Dockerfile`: sestavení Tailwindu používá `npm ci --no-audit --no-fund`, bez
  odesílání kontroly závislostí do npm a bez hlášení o příspěvcích.
- Běhové proměnné pro vypnutí telemetrie a hlášení npm/pip předává společný Compose.

## 2026-10-23 · F4-B02

- Karta Script v detailu obsahu používá místně sestavený Tiptap: nadpisy, tučné písmo,
  kurzíva, seznamy, checklist a historie úprav. Edit text / Save / Discard, explicitní
  Resume editing po návratu, průběžný zápis konceptu, seznam neměnných verzí a čtení starších.
- `apps/jrc_texts/views.py`, `urls.py`: přihlášené místní HTTP rozhraní pouze pro scénář
  konkrétního příspěvku. Každý požadavek ověřuje členství; zápisy navíc původní služby,
  CSRF, UUID a revizi konceptu. Čtení nic nezakládá, cizí koncept neposílá text a odpovědi
  se neukládají do cache. Klient čte uložené verze. Obnova osiřelého konceptu nabízí
  stejná oprávnění správce jako F4-B01.
- Prohlížeč řadí průběžné zápisy před Save/Discard, varuje při odchodu s nezapsaným
  textem a při chybě uchová text k opakování či zkopírování. Konflikt druhé karty
  zablokuje zápis; informace o úpravách jiného člena se obnovuje při neaktivním editoru.
- `Dockerfile` a kořenový `Makefile`: npm/esbuild sestaví editor do místního souboru,
  přenos do worktree stejně jako Tailwind; žádná CDN ani nové požadavky na internet za běhu.
- HTTP regrese v `apps/jrc_texts/tests/test_script.py`; skutečný Chromium průchod,
  vymyšlená data a snímky při 1440/390 px v `scripts/F4-B02/`.
## 2026-10-23 · F3-B04

- `apps/approvals/models.py`, migrace `0004_*`: výchozí viditelnost týmového komentáře je interní;
  přibyl stav vyřešeno.
- `apps/approvals/comments.py`, `views.py` a `templates/approvals/partials/comment_form.html`:
  služba komentářů používá interní výchozí hodnotu pro tým, vynucuje externí viditelnost klienta,
  nepovoluje odpověď do cizího vlákna a filtruje interní odpovědi klientům.
- `apps/jrc_production/` a `templates/jrc_production/content_detail.html`: detail obsahu má záložku
  komentářů, odpovědi a uzavření/obnovení vlákna pro členy s oprávněním schvalovat. Klient může
  přidávat komentáře a odpovědi, ale nevidí interní obsah ani ovládání vyžádání změn.
- `apps/jrc_production/tests/test_content_detail.py`: HTTP ověření interního výchozího nastavení,
  odpovědí, řešení vlákna a hranic klienta.
## 2026-10-23 · F5-B02

- `apps/jrc_video/tickets.py`: `issue_ticket(user, audience="web"|"api", next_path=None)` vystaví
  vstupenku pro FreeFrame (formát a pravidla v `freeframe/JRC-CHANGES.md`, F5-B02). Vstupenku dostane
  jen aktivní uživatel (ověřeno znovu v DB, ne podle starého objektu) se synchronizovaným
  `VideoAccount`; `next` jen pro `web` a jen jako cesta uvnitř FreeFrame. Klíč `JRC_FF_SSO_SECRET`
  čte `config/settings/jrc.py` z prostředí; dostává ho jen `jrc-bb-web` (worker ne). Bez klíče
  (kratšího než 32 znaků) se vstupenka nevydá.
- `apps/jrc_video/client.py`: `FreeFrameUserClient(user).request(method, path, payload)` volá API
  FreeFrame jménem uživatele (podklad F5-B05). Vymění vstupenku `api` přímo na `jrc-ff-api:8000`,
  přístupový token drží jen v paměti procesu do minuty před vypršením, po 401 jednou zkusí novou
  vstupenku. Před každým voláním ověří, že uživatel není zablokovaný (i když FreeFrame ještě
  synchronizaci nedostal). Odmítne odpověď s obnovovacím tokenem a cestu, která nezačíná `/`
  (ochrana proti přesměrování na jiný server; platí i pro servisního klienta).
- `tests/test_tickets.py`: obsah a podpis, nové `nonce`, cíle a `next`, zablokovaný, smazaný
  a nesynchronizovaný uživatel, chybějící klíč, společný testovací vektor s FreeFrame, přímé volání
  bez hlavičky a přesměrování, opakované použití tokenu, oddělení uživatelů, obnova po 401,
  vadné odpovědi, vstupenka ani token v chybách a logu.
- Kořen JR Content: `JRC_FF_SSO_SECRET` v `.env.example`, `scripts/init-env.cjs` (96 hex znaků,
  zachová existující hodnotu), `docker-compose.yml` (jen `jrc-bb-web` a `jrc-ff-api`), `Makefile`
  (`ADMIN_COMPOSE`). Živý test `scripts/F5-B02/check-ticket.sh` (jen `jrc-f5b02`).

## 2026-10-24 · F2-B05 — úložiště S3

- `config/settings/jrc.py`: média v soukromém bucketu Garage `jrc-app` (vlastní klíč jen pro
  tento bucket). Server používá vnitřní adresu `jrc-s3:3900`, prohlížeč dostává podepsané adresy
  platné 15 minut na místní bránu S3 (`JRC_S3_PUBLIC_URL`). `/media/` se už neobsluhuje
  (`SERVE_MEDIA = False`). CSP `img-src`, `media-src`, `connect-src` doplněna jen o přesný původ
  S3, vynucená. Podepsaný POST pro nástroje MCP je vypnutý (mířil by na vnitřní adresu).
- `config/settings/jrc_offline.py`: registrace `apps.jrc_storage`; `STORAGE_BACKEND=local`
  pro import základu zůstává, aby base.py nedoplnil do CSP `https://` bez portu.
- `apps/jrc_storage/`: `JrcS3Storage` (podpis pro adresu prohlížeče, provoz přes vnitřní adresu),
  příkazy `jrc_configure_s3` (CORS bucketu jen pro původ aplikace, GET/HEAD) a
  `jrc_migrate_media_to_s3` (jednorázová kopie místního svazku do S3; místní soubory se nemažou).
  Oprava po review: existující objekt v S3 se nikdy nepřepisuje; dokončení se zapíše značkou
  `.jrc/media-migration-complete.json` v bucketu až po úplném bezchybném průchodu a pak už příkaz nic
  nekopíruje (stará záloha neobnoví vyměněná ani smazaná média). Přerušená migrace pokračuje,
  nečitelný adresář je chyba s cestou, vynechá se jen provozní `.jrc-working-dir` (média s tečkou ne).
  Oba spouští `jrc-bb-migrate` při každém `make up`.
- `apps/media_library/storage.py`: `is_s3_backend()` uznává i podtřídy S3 backendu (kontrola MRO).
- `templates/media_library/asset_edit.html`: Cropper s `checkCrossOrigin: false`. Jinak by přidal
  k podepsané adrese `?timestamp=` a S3 by obrázek odmítl (403). Ořez dělá server podle `getData()`.
- Testy: `apps/jrc_storage/tests/` (podpis pro veřejnou adresu, kopie médií, CORS, nastavení a CSP).
