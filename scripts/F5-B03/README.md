# Ověření přihlášení do videa

Pouze vlastní projekt Dockeru `jrc-f5b03`, testovací účty a místní proxy. Nový `.env` musí
mít `JRC_APP_PORT=13213` a `JRC_S3_PORT=13933`; samotné předání proměnných do `make up`
je do souboru neukládá. PostgreSQL ani BB se přímo nevystavují; rezervované porty jsou
15463 a 18033.

```sh
JRC_PROJECT=jrc-f5b03 JRC_APP_PORT=13213 JRC_POSTGRES_PORT=15463 \
  JRC_S3_PORT=13933 JRC_BB_PORT=18033 make up
JRC_PROJECT=jrc-f5b03 make ff-account
mkdir -p ops/test-output/F5-B03
JRC_PROJECT=jrc-f5b03 docker compose exec -T -e JRC_PROJECT=jrc-f5b03 jrc-bb-web \
  python manage.py shell < scripts/F5-B03/seed.py > ops/test-output/F5-B03/fixture.json
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" \
  --env-file .env --env JRC_PROJECT=jrc-f5b03 --volume "$PWD:/jrc" --workdir /jrc \
  jrc-dev:local node scripts/F5-B03/browser.cjs
JRC_PROJECT=jrc-f5b03 make test
```

`fixture.json` obsahuje vygenerované heslo pouze vymyšleného účtu. Je v ignorované složce,
nevypisovat ani necommitovat; po ověření jej smazat. Nespouštět současně dva pytesty Brightbean
(sdílely by testovací databázi).

Chromium projde skutečným přihlášením Brightbean, otevře konkrétní projekt FreeFrame bez
druhého hesla a přes `/auth/me` ověří identitu. Kontroluje historii přes Chromium DevTools,
HTTP adresy, smazání kontrolní cookie, opakování vstupenky i platnou cizí vstupenku bez
kontrolní cookie. Po odhlášení z BB musí zmizet FF tokeny i cookies a chráněná stránka znovu
vyžaduje přihlášení. Snímky jsou v `ops/screenshoty/F5-B03/`.

Po testu zkontrolovat logy, jen bez výpisu jejich případného citlivého obsahu:

```sh
if JRC_PROJECT=jrc-f5b03 docker compose logs --no-color jrc-bb-web jrc-ff-api jrc-proxy 2>&1 \
  | grep -Eq 'v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|eyJhbGci'; then
  echo 'CHYBA: vstupenka nebo token v logu'
else
  echo 'OK: logy bez vstupenek a tokenů'
fi
```

Úklid pouze tohoto projektu a jeho vlastního obrazu Brightbean:

```sh
JRC_PROJECT=jrc-f5b03 docker compose -p jrc-f5b03 -f docker-compose.yml down -v
docker image rm jrc-bb:jrc-f5b03
rm -- ops/test-output/F5-B03/fixture.json
```
