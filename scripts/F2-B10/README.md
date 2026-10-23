# Ověření úklidu rozhraní F2-B10

Všechny programy aplikace i prohlížeč běží v Dockeru. Použít jen postradatelný projekt
`jrc-f2b10` nad worktree F2-B10. Názvy účtů jsou vymyšlené; seed nezakládá účty sociálních sítí.
Snímky a protokoly jsou ignorované gitem. Soubor relace se ukládá jen do vlastního svazku médií,
nikoli do repozitáře nebo KB. Testovací konfigurace zachovává upstream funkce pro regresní sadu;
testy `apps/jrc_ui/tests/` a živý browser ověřují vypnuté MVP funkce.
Součástí celé sady je `test_api_features.py`: platný místní API klíč, skryté MCP
nástroje ve výpisu i volání (včetně dávek a notifikací), všechny REST operace
inboxu/odpovědí a statistik a společné opětovné zapnutí bez restartu.

```sh
export JRC_PROJECT=jrc-f2b10
JRC_APP_PORT=13020 JRC_POSTGRES_PORT=15440 JRC_S3_PORT=13920 JRC_BB_PORT=18020 make up
make test
# Až po dokončení pytest: změna sítě během testů může přerušit jejich spojení s databází.
docker compose -p jrc-f2b10 --env-file .env exec -T jrc-bb-web python manage.py shell < scripts/F2-B10/seed-ui.py
docker network disconnect jrc-f2b10-network jrc-f2b10-jrc-bb-web-1
docker run --rm --network jrc-f2b10_internal --user "$(id -u):$(id -g)" \
  --volume "$PWD:/workspace" --volume jrc-f2b10-bb-media:/seed:ro \
  jrc-dev:local node scripts/F2-B10/check-ui.cjs
# Pokud je potřeba znovu spouštět make test, nejprve obnovit síť vlastního webu:
docker network connect --alias jrc-bb-web jrc-f2b10-network jrc-f2b10-jrc-bb-web-1
# Po ověření odstranit jen tento projekt a jeho vlastní data:
docker compose -p jrc-f2b10 --env-file .env down -v
```

Výsledky: `ops/test-output/F2-B10/browser-report.json`, snímky
`ops/screenshoty/F2-B10/{login,calendar,compose,media,settings,about,api-docs}-{1440,390}.png`
a `navigation-390.png`. Browser hlídá každý požadavek, odmítne cizí doménu, ověřuje
stav hlavních stránek a místních knihoven, skryté odkazy, mobilní nabídku a přechod do editoru.
