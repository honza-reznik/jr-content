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

Žádná upstream funkce, původní licence ani autorské upozornění nebyly odstraněny.
Vypínače lze změnit v našem nastavení bez odstranění nebo obnovení upstream kódu.
Ověřovací skripty a snímky jsou v kořeni JR Content (`scripts/F2-B10/`,
`ops/screenshoty/F2-B10/`), mimo upstream podsložku. Žádné skutečné sociální účty.

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
