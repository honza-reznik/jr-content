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
