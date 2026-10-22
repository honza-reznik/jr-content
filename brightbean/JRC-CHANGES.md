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


## 2026-10-23 · F2-B10

- `apps/jrc_ui/`: přepínače funkcí, kontrola přímých adres před přihlášením a CSRF,
  kontext šablon bez onboardingu, vlastní stránka About a místní úplný text licence.
  Přidané testy ověřují GET/POST blokování, hlavní obrazovky a vypnutí RSS bez DNS/HTTP.
- `config/settings/jrc.py`: `JRC_FEATURES` vypíná inbox, statistiky, Trustpilot,
  onboarding/Get started, nápady, klientský portál, Intelligence, připojení sítí,
  Unsplash, RSS a externí přihlášení (Google, jehož klíč je už vypnutý od F2-B01). CSP povoluje pouze místní zdroje; obrázky a média také data/blob.
- `config/settings/jrc_test.py`: stejná vrstva rozhraní; původní testy mohou nadále
  testovat upstream funkce (`JRC_FEATURES = {}`); naše sada explicitně přepíná
  `MVP_FEATURES`, stejně jako skutečně běžící aplikace.
- `config/urls.py`: přidané `/about/` a `/about/license/`, pouze s nastavením JR Content.
- `apps/composer/views.py`: RSS při vypnutí končí před DNS a HTTP požadavkem.
- `apps/api/api.py`: název dokumentace API JR Content.
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
  jejich původní licence/NOTICE a kontrolní součty v přiloženém README.
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
