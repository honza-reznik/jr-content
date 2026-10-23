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
