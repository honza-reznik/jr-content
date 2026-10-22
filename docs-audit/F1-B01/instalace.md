# Brightbean Studio: reprodukovatelná instalace pro audit (F1-B01)

Auditovaná verze: `brightbeanxyz/brightbean-studio`, commit `48801fd` (2026-10-17), licence AGPL-3.0.
Vše běží v Dockeru jako samostatný projekt `jrc-audit-brightbean`. Prostředí `jrc` (jrc-dev,
jrc-postgres, jrc-s3) se nemění; `jrc-dev` se jen dočasně připojí do sítě auditu kvůli prohlížeči.

| Co | Kde |
|---|---|
| Web | `http://127.0.0.1:18010` (jen z Jarvise, přes SSH tunel `-L 127.0.0.1:18010:127.0.0.1:18010`) |
| Databáze | PostgreSQL 16 v kontejneru, **bez portu ven** |
| Média | svazek `jrc-audit-brightbean_media_data` (lokální disk, ne S3) |
| Data databáze | svazek `jrc-audit-brightbean_postgres_data` |
| Síť | `…_vnitrni` (bez internetu: worker, migrace, databáze), `…_web` (jen web, kvůli portu) |
| Nastavení a hesla | `audit/jrc-audit-brightbean.env` (mimo git, práva 600) |

Všechny příkazy se spouští v kořeni worktree `/home/honza/jr-content-wt/F1-B01`.
Zkratka použitá níže:

```sh
cd /home/honza/jr-content-wt/F1-B01
DC="docker compose -p jrc-audit-brightbean -f docs-audit/F1-B01/docker-compose.audit.yml"
```

## 1. První instalace

```sh
# 1) Zdrojový kód (35 MB, jen poslední commit; složka audit/ je v .gitignore)
git clone --depth 1 https://github.com/brightbeanxyz/brightbean-studio audit/brightbean-studio
# přesně auditovaná verze (kdyby mezitím přibyly novější commity)
git -C audit/brightbean-studio fetch --depth 1 origin 48801fdcd1009e6ed4863fa9942222a478a297e2
git -C audit/brightbean-studio checkout --detach 48801fdcd1009e6ed4863fa9942222a478a297e2

# 2) Nastavení s náhodnými hesly (existující soubor nepřepíše)
docs-audit/F1-B01/skripty/priprav-env.sh

# 3) Sestavení obrazu jrc-audit-brightbean:48801fd (~9 min, 1,9 GB; stahuje python:3.12-slim,
#    balíčky Debianu vč. FFmpeg, Node.js 20, balíčky Pythonu a npm)
$DC build migrate

# 4) Spuštění: databáze → migrace → web + worker
$DC up -d
$DC ps                                     # migrate: Exited (0), ostatní Up
curl -s http://127.0.0.1:18010/health/      # {"status": "ok"}
```

Žádné klíče sociálních sítí, Google, Unsplash, Sentry ani „Intelligence“ se nevyplňují. E-maily se
jen vypisují do logu webu (`EMAIL_BACKEND_TYPE=console`), např. pozvánky:

```sh
docker logs jrc-audit-brightbean-app-1 2>&1 | grep -o 'http://127.0.0.1:18010/[^ "<]*' | sort -u
```

## 2. První účet a testovací data

1. Otevři `http://127.0.0.1:18010/accounts/signup/` a zaregistruj se (registrace je v auditu
   otevřená, `SIGNUP_MODE=open`). V auditu: `honza.admin@jrc.test`.
2. Vlevo nahoře vytvoř workspace **Ella** (workspace = jeden klient).
3. Připojení skutečných kanálů nejde (vše „Not configured“). Pro vyzkoušení se vytvoří vymyšlené
   kanály bez tokenů:

   ```sh
   docker exec -i jrc-audit-brightbean-app-1 python manage.py shell \
     < docs-audit/F1-B01/skripty/falesne-kanaly.py
   ```

   Worker je v síti bez internetu, takže jeho pokusy o stažení statistik k těmto kanálům skončí
   chybou DNS v logu a nic neodejde ven.

## 3. Proklikání v prohlížeči (Playwright v jrc-dev)

```sh
docs-audit/F1-B01/skripty/proklik.sh <krok.js> [složka-na-screenshoty]
```

Skript připojí `jrc-dev` do sítě `jrc-audit-brightbean_web` (`docker network connect`), zkopíruje
do něj `pw.js` a krok a spustí Chromium. Krok je modul `module.exports = async (page, ctx, h) => {…}`,
`h.go('/cesta/')`, `h.shot('jmeno')`. Přihlášení se drží v `/tmp/bb-state*.json` uvnitř `jrc-dev`
(proměnná `BB_STATE`, pro klienta jiný soubor). Prohlížeč smí ven jen na `cdn.jsdelivr.net`
(aplikace odtud načítá knihovny JavaScriptu), ostatní požadavky mimo stack blokuje.
Chromium u jména `app` vynucuje HTTPS, proto skript používá IP adresu kontejneru. Po `down`/`up`
dostane kontejner jinou adresu, a uložené přihlášení prohlížeče proto přestane platit: krok musí
nejdřív projít `/accounts/login/`.

## 4. Automatické testy Brightbean

```sh
PW=$(grep ^POSTGRES_PASSWORD audit/jrc-audit-brightbean.env | cut -d= -f2)
$DC run --rm --no-deps -T -e DB_HOST=postgres -e DB_PASSWORD="$PW" \
  -e DJANGO_SETTINGS_MODULE=config.settings.test migrate \
  sh -c 'pytest -p no:cacheprovider -o addopts="" -q --tb=line'
# 2026-10-21: 2714 passed, 1 skipped, 9 min 49 s
$DC run --rm --no-deps -T migrate sh -c 'ruff check . && ruff format --check .'
```

Testy si vytvoří vlastní databázi `brightbean_test` ve stejném PostgreSQL a na konci ji smažou.

## 5. Zastavení a nové spuštění

```sh
# odpojit jrc-dev od sítě auditu (jinak ji down nesmaže)
docker network disconnect jrc-audit-brightbean_web jrc-dev 2>/dev/null || true
$DC down          # kontejnery a sítě pryč, svazky (data) zůstávají
$DC up -d         # znovu naběhne se stejnými daty, bez nového sestavení
curl -s http://127.0.0.1:18010/health/
```

Úplné smazání (jen po rozhodnutí PM): `$DC down -v` smaže i svazky, `docker image rm
jrc-audit-brightbean:48801fd` obraz, `rm -r audit/brightbean-studio audit/jrc-audit-brightbean.env`
klon a nastavení.

## Odchylky od návodu Brightbean a proč

- Vlastní compose soubor místo `docker-compose.yml` z repozitáře: originál vystavuje web a databázi
  na všech adresách (`8000:8000`, `5432:5432`) a heslo databáze je `postgres`.
- `config.settings.development` a `runserver` (vývojový server Django) místo produkčního nastavení:
  produkční vyžaduje HTTPS a reverzní proxy (Caddy), v auditu zbytečné.
- Služba `tailwind` (průběžná kompilace CSS) vynechána: CSS je zkompilované v obrazu.
