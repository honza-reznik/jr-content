SHELL := /bin/sh
.DEFAULT_GOAL := up
# Povolen je jen JR Content; oddělený jrc-* projekt slouží k ověření worktree.
export JRC_PROJECT ?= jrc
# Compose profile SMTP is enabled only when an SMTP destination is configured.
export COMPOSE_PROFILES := $(if $(strip $(JRC_SMTP_HOST)$(shell sed -n 's/^JRC_SMTP_HOST=//p' .env 2>/dev/null | head -n 1)),smtp,)
# Jen písmena, číslice a pomlčky (review F2-B01: žádné shellové znaky v příkazech).
ifneq ($(shell printf '%s' '$(subst ','',$(JRC_PROJECT))' | grep -Ex 'jrc(-[a-z0-9-]+)?'),$(JRC_PROJECT))
$(error JRC_PROJECT musí být jrc nebo jrc-<malá písmena, číslice, pomlčky>)
endif
# Ochrana dat hlavního prostředí (F0-B09): reset a down projektu jrc jen z hlavní složky.
ifeq ($(JRC_PROJECT),jrc)
ifneq ($(CURDIR),/home/honza/jr-content)
ifneq ($(filter reset down,$(MAKECMDGOALS)),)
$(error Ve worktree nelze '$(filter reset down,$(MAKECMDGOALS))' hlavního projektu jrc. Použij JRC_PROJECT=jrc-<blok>)
endif
ifneq ($(filter test-up,$(MAKECMDGOALS)),)
$(error test-up je jen pro oddělený ověřovací projekt jrc-<blok>)
endif
endif
endif
# F2-B09: test záloh nikdy nesmí cílit na hlavní projekt (kontrola už při čtení Makefile).
ifeq ($(JRC_PROJECT),jrc)
ifneq ($(filter test-backup,$(MAKECMDGOALS)),)
$(error 'test-backup' je zakázáno pro hlavní projekt jrc. Použij JRC_PROJECT=jrc-<název> (prázdný ověřovací projekt))
endif
# F2-B09a: obnova do jrc jen po skutečné havárii (JRC_RESTORE_MAIN=yes-empty), jen z hlavní složky; prázdnotu svazků
# a absenci kontejnerů pak ověřuje scripts/backup/backup.sh (bez toho se nic nespustí ani nesmaže).
ifneq ($(filter restore,$(MAKECMDGOALS)),)
ifneq ($(JRC_RESTORE_MAIN),yes-empty)
$(error 'restore' je zakázáno pro hlavní projekt jrc (pojistka proti přepsání dat). Použij JRC_PROJECT=jrc-<název> (prázdný ověřovací projekt). Po skutečné havárii bez dat v jrc: JRC_RESTORE_MAIN=yes-empty, viz zalohy.md)
endif
ifneq ($(CURDIR),/home/honza/jr-content)
$(error Obnovu hlavního projektu jrc lze spustit jen z /home/honza/jr-content)
endif
endif
endif
override COMPOSE := docker compose --project-name $(JRC_PROJECT) --file docker-compose.yml --env-file .env
# Správa existujících kontejnerů nepotřebuje hesla ani místní .env.
# Zástupné hodnoty se používají pouze pro načtení konfigurace, nikdy pro up.
override ADMIN_COMPOSE := PGPASSWORD=unused GARAGE_RPC_SECRET=unused AWS_ACCESS_KEY_ID=unused AWS_SECRET_ACCESS_KEY=unused JRC_BB_SECRET_KEY=unused JRC_BB_ENCRYPTION_KEY_SALT=unused JRC_FF_JWT_SECRET=unused JRC_FF_SYNC_PASSWORD=unused JRC_FF_SSO_SECRET=unused JRC_FF_S3_ACCESS_KEY=unused JRC_FF_S3_SECRET_KEY=unused JRC_BB_S3_ACCESS_KEY=unused JRC_BB_S3_SECRET_KEY=unused docker compose --project-name $(JRC_PROJECT) --file docker-compose.yml --env-file /dev/null
export JRC_UID := $(shell id -u)
export JRC_GID := $(shell id -g)
export JRC_SOURCE_VERSION := $(shell git rev-parse --short HEAD)
# Změna bind mountu sama službu nerestartuje; otisk v labelu vynutí novou konfiguraci.
export JRC_CADDYFILE_SHA256 := $(shell sha256sum docker/Caddyfile | cut -d ' ' -f 1)
# Buildx zapisuje provozní údaje pouze do worktree, i v omezené relaci agenta.
export BUILDX_CONFIG := $(CURDIR)/.cache/docker-buildx
CMD ?= bash
export CMD

.PHONY: test-https-proxy restore-lock-check backup backup-verify restore backup-offsite restore-offsite test-backup test-offsite test-backup-guards build build-bb env check-project-ports up down shell test test-fast test-e2e test-restore-e2e test-smoke test-proxy test-s3 test-bb test-bb-fast test-ff test-ff-smoke test-isolation test-ff-offline test-ff-vitest test-ff-tsc test-ff-lint test-f5-b06-embed test-f5-b05-upload test-f5-b08-video test-f7-b02-approval ff-account build-ff reset logs ps test-smtp-relay
.PHONY: check-clean-worktree test-build-source test-up test-p1b02-regressions
check-clean-worktree:
	sh scripts/P1-B02/check-clean-worktree.sh

test-build-source:
	docker run --rm --pull never --network none --user "$(JRC_UID):$(JRC_GID)" \
	  --volume "$(CURDIR)/scripts/P1-B02:/test:ro" \
	  jrc-dev:$(JRC_PROJECT) sh /test/test-build-source.sh /test/check-clean-worktree.sh

test-smtp-relay:
	@sh scripts/P1-B02/test-smtp-relay-scope.sh
	@sh scripts/P1-B02/test-smtp-relay.sh

test-p1b02-regressions:
	$(COMPOSE) exec -T jrc-bb-web pytest -p no:cacheprovider -o addopts="" -q --tb=short --ds=config.settings.jrc_test \
	  apps/jrc_video/tests/test_player.py apps/jrc_video/tests/test_upload.py \
	  apps/jrc_production/tests/test_publish.py apps/members/tests/test_invite_copy_link.py \
	  apps/members/tests/test_public_prefix.py

# Build a dirty tree only for an isolated test project; production build targets
# above continue to reject dirty trees. The archive remains the HEAD snapshot.
test-up: env check-project-ports
	@set -eu; version="$$(git rev-parse --short HEAD)"; \
	  git archive --format=tar.gz --prefix="jr-content-$$version/" --output=brightbean/.jrc-source.tar.gz HEAD
	$(COMPOSE) up --detach --build --wait --wait-timeout 120
	@sh scripts/workspace.sh remember

build:
	docker build --tag jrc-dev:$(JRC_PROJECT) --file docker/Dockerfile docker

env:
	@docker image inspect jrc-dev:$(JRC_PROJECT) >/dev/null 2>&1 || $(MAKE) build
	@sh scripts/workspace.sh env
	docker run --rm --name $(JRC_PROJECT)-env --network none --pull never \
	  --user "$(JRC_UID):$(JRC_GID)" --volume "$(CURDIR):/workspace" \
	  --env JRC_APP_PORT --env JRC_POSTGRES_PORT --env JRC_S3_PORT --env JRC_BB_PORT \
	  --env JRC_MODE --env JRC_PUBLIC_ORIGIN --env JRC_BASE_PATH \
	  jrc-dev:$(JRC_PROJECT) node scripts/init-env.cjs

build-bb:
	$(MAKE) --no-print-directory check-clean-worktree
	# Worktree nesmějí přepsat obraz jinému ověřovacímu projektu.
	@set -eu; archive=brightbean/.jrc-source.tar.gz; version="$$(git rev-parse --short HEAD)"; \
	  git archive --format=tar.gz --prefix="jr-content-$$version/" --output="$$archive" HEAD; \
	  docker build --build-arg JRC_SOURCE_VERSION="$$version" --tag jrc-bb:$(JRC_PROJECT) brightbean
	# Zdrojový bind mount skryje CSS sestavené v obrazu; přeneseme je do worktree.
	docker run --rm --network none --pull never --user "$(JRC_UID):$(JRC_GID)" \
	  --volume "$(CURDIR)/brightbean:/source" jrc-bb:$(JRC_PROJECT) sh -c \
	  'mkdir -p /source/theme/static/css/dist /source/apps/jrc_texts/static/jrc_texts/dist && cp /app/theme/static/css/dist/styles.css /source/theme/static/css/dist/styles.css && cp /opt/jrc-texts/script.js /source/apps/jrc_texts/static/jrc_texts/dist/script.js'

build-ff: check-clean-worktree env
	docker build --tag jrc-ff-api:$(JRC_PROJECT) --build-arg JRC_PROJECT=$(JRC_PROJECT) --file docker/Dockerfile.ff-api .
	docker build --tag jrc-ff-web:$(JRC_PROJECT) --build-arg JRC_PROJECT=$(JRC_PROJECT) --build-arg JRC_BASE_PATH="$${JRC_BASE_PATH-$$(sed -n 's/^JRC_BASE_PATH=//p' .env)}" --file docker/Dockerfile.ff-web .

# F2-B09a: během obnovy hlavního projektu se up/reset/down odmítají (zámek .jrc-restore.lock).
restore-lock-check:
	@sh scripts/restore-lock.sh check

check-project-ports:
	@sh scripts/check-project-ports.sh

up: restore-lock-check env check-project-ports
	$(MAKE) build
	$(MAKE) build-bb
	$(MAKE) build-ff
	$(COMPOSE) up --detach --build --wait --wait-timeout 120
	@sh scripts/workspace.sh remember

down:
	@sh scripts/restore-lock.sh check
	@sh scripts/workspace.sh remember
	$(ADMIN_COMPOSE) down

shell:
	@sh scripts/workspace.sh check
	@if [ -t 0 ]; then \
		$(COMPOSE) exec jrc-dev sh -c "$$CMD"; \
	else \
		$(COMPOSE) exec -T jrc-dev sh -c "$$CMD"; \
	fi

test: check-project-ports
	@failed=0; \
	for suite in test-smoke test-build-source test-bb test-ff test-backup-guards test-smtp-relay test-offsite; do \
	  printf '\n=== %s ===\n' "$$suite"; \
	  if $(MAKE) --no-print-directory "$$suite"; then \
	    printf 'VÝSLEDEK: %s OK\n' "$$suite"; \
	  else \
	    result=$$?; failed=1; printf 'VÝSLEDEK: %s CHYBA (kód %s)\n' "$$suite" "$$result"; \
	  fi; \
	done; \
	printf '\n=== Souhrn make test ===\n'; \
	if [ "$$failed" -eq 0 ]; then printf 'Všechny sady prošly.\n'; else printf 'Nejméně jedna sada selhala.\n'; fi; \
	exit "$$failed"

# Rychlá kontrola vlastních změn: prostředí, naše aplikace Brightbean a živý průchod FreeFrame.
test-fast:
	@failed=0; \
	for suite in test-smoke test-bb-fast test-ff-smoke; do \
	  printf '\n=== %s ===\n' "$$suite"; \
	  if $(MAKE) --no-print-directory "$$suite"; then \
	    printf 'VÝSLEDEK: %s OK\n' "$$suite"; \
	  else \
	    result=$$?; failed=1; printf 'VÝSLEDEK: %s CHYBA (kód %s)\n' "$$suite" "$$result"; \
	  fi; \
	done; \
	printf '\n=== Souhrn make test-fast ===\n'; \
	if [ "$$failed" -eq 0 ]; then printf 'Všechny sady prošly.\n'; else printf 'Nejméně jedna sada selhala.\n'; fi; \
	exit "$$failed"

# F10-B02: kompletní scénář „Reel pro Ellu“ v čistém jrc-f10b02; po běhu smaže jen jeho data a obrazy.
test-e2e:
	@sh scripts/F10-B02/test-e2e.sh

# F10-B04: skutečný E2E průchod, záloha, obnova do prázdného projektu a kontrola Chromiem.
test-https-proxy:
	@sh scripts/P1-B01/test-https.sh

test-restore-e2e:
	@sh scripts/F10-B04/test-restore-e2e.sh

test-smoke: test-proxy test-s3
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-dev bash scripts/smoke.sh
	$(COMPOSE) exec -T jrc-dev node scripts/test-init-env.cjs
	$(COMPOSE) exec -T jrc-bb-web python - web < scripts/smoke-bb.py
	$(COMPOSE) exec -T jrc-bb-worker python - worker < scripts/smoke-bb.py
	$(COMPOSE) exec -T jrc-bb-web python manage.py shell < scripts/P1-B01/check-redis.py

# Prohlížeč i HTTP požadavky běží v Dockeru proti skutečnému místnímu portu.
test-proxy:
	@sh scripts/workspace.sh check
	docker run --rm --pull never --network host --user "$(JRC_UID):$(JRC_GID)" \
	  --env-file .env --volume "$(CURDIR):/jrc" \
	  jrc-dev:$(JRC_PROJECT) node /jrc/scripts/test-proxy.cjs

# Úložiště S3 z pohledu prohlížeče (místní brána): klíče, soukromí jrc-app, CORS, kvóty (F2-B05).
test-s3:
	@sh scripts/workspace.sh check
	docker run --rm --pull never --network host --user "$(JRC_UID):$(JRC_GID)" \
	  --env-file .env --volume "$(CURDIR)/scripts:/jrc-scripts:ro" \
	  jrc-dev:$(JRC_PROJECT) python3 /jrc-scripts/test-s3-storage.py
	# Výpis bucketu obsahuje identifikátory klíčů; kontrolujeme jen řádek kvóty.
	$(COMPOSE) exec -T jrc-s3 sh -c 'for b in jrc-app jrc-video; do garage bucket info $$b | grep -q "maximum size" || exit 1; done'
	@printf 'OK S3: kvóty jrc-app a jrc-video nastavené\n'

test-bb:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-bb-web sh -c 'ruff check . && ruff format --check .'
	$(COMPOSE) exec -T jrc-bb-web pytest -p no:cacheprovider -o addopts="" -q --tb=short --ds=config.settings.jrc_test

test-bb-fast:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-bb-web sh -c 'pytest -p no:cacheprovider -o addopts="" -q --tb=short --ds=config.settings.jrc_test $$(ls -d apps/jrc_*)'

test-isolation:
	@sh scripts/workspace.sh check
	sh scripts/test-service-isolation.sh $(COMPOSE)
	# PostgreSQL už brána nevystavuje; dostupnost ověřujeme uvnitř vnitřní sítě.
	$(COMPOSE) exec -T jrc-dev psql -X -Atc 'SELECT 1' | grep -qx 1
	# F5-B02: jednorázovost vstupenek po pádu stroje vyžaduje trvalý zápis každého SET NX.
	$(COMPOSE) exec -T jrc-redis redis-cli CONFIG GET appendonly | grep -qx yes
	$(COMPOSE) exec -T jrc-redis redis-cli CONFIG GET appendfsync | grep -qx always
	@echo 'OK jrc-redis: appendonly yes, appendfsync always'
	docker run --rm --pull never --network host --user "$(JRC_UID):$(JRC_GID)" \
	  --env-file .env \
	  --volume "$(CURDIR)/scripts:/jrc-scripts:ro" \
	  jrc-dev:$(JRC_PROJECT) node /jrc-scripts/test-local-gate.cjs

test-ff-offline:
	@sh scripts/workspace.sh check
	$(COMPOSE) run --rm --no-deps -T jrc-ff-web node /jrc-scripts/test-ff-offline.cjs

test-ff-tsc:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-ff-web pnpm exec tsc --noEmit

test-ff: test-isolation test-ff-offline
	@sh scripts/workspace.sh check
	# Produkční Next musí pod běžným UID obnovovat cache i předem vykreslené stránky.
	$(COMPOSE) exec -T jrc-ff-web sh -c 'if [ "$$JRC_MODE" = prod ]; then test -w /opt/jrc-web/.next/cache && test -w /opt/jrc-web/.next/server/app; fi'
	@for service in jrc-ff-worker jrc-ff-maintenance jrc-ff-beat; do \
	  $(COMPOSE) exec -T $$service python - < scripts/smoke-ff-internal.py || exit $$?; \
	done
	# Upstream test_smoke ověřuje /docs; v provozu je vypne DISABLE_DOCS (ověřuje test brány).
	# Upstream testy CORS počítají s vývojovými původy localhost; provoz je vypíná (F2-B05).
	$(COMPOSE) exec -T -e STALE_UPLOAD_TIMEOUT_HOURS=24 -e SOFT_DELETE_RETENTION_DAYS=30 -e DISABLE_DOCS=false -e CORS_DEV_ORIGINS=true -e S3_PUBLIC_PATH= jrc-ff-api python -m pytest apps/api/tests -q -p no:cacheprovider
	# Upstream jednotkové testy předpokládají kořenovou URL; /video ověřuje živý test a build.
	$(MAKE) --no-print-directory test-ff-vitest
	$(MAKE) --no-print-directory test-ff-tsc
	$(MAKE) --no-print-directory test-ff-lint

# Podepsané adresy S3 obsahují 127.0.0.1, proto ověření běží v Dockeru se sítí hostitele.
test-ff-smoke:
	@sh scripts/workspace.sh check
	docker run --rm --network host --pull never --user "$(JRC_UID):$(JRC_GID)" \
	  --env-file .env \
	  --volume "$(CURDIR):/jrc" --workdir /jrc \
	  jrc-ff-api:$(JRC_PROJECT) python scripts/smoke-ff.py

# F5-B06 embedded viewer regression; Chromium and the app stay on the local host network.
test-f5-b06-embed:
	@sh scripts/workspace.sh check
	docker run --rm --network host --pull never --user "$(JRC_UID):$(JRC_GID)" \
	  --env-file .env --env JRC_PROJECT=$(JRC_PROJECT) \
	  --volume "$(CURDIR):/jrc" --workdir /jrc \
	  jrc-dev:$(JRC_PROJECT) node scripts/F5-B06/embed.cjs

# F5-B05: nahrání verze videa v Chromiu (video 1080p / 1 min z FFmpegu, přerušení a pokračování, převod).
# Jen projekt jrc-f5b05 (porty 13333/14053/15583/18153); trvá několik minut, proto není ve `make test`.
test-f5-b05-upload:
	@sh scripts/workspace.sh check
	sh scripts/F5-B05/check-upload.sh

# F5-B08: záložka Video v Chromiu (přehrávač, verze, komentář 0:17, Final for publishing, worker).
# Jen projekt jrc-f5b08 (porty 13363/14083/15613/18183); trvá několik minut, proto není ve `make test`.
test-f5-b08-video:
	@sh scripts/workspace.sh check
	sh scripts/F5-B08/check-video-tab.sh

# Jen projekt jrc-f7b02 (porty 13373/14093/15623/18193); trvá několik minut, proto není ve `make test`.
test-f7-b02-approval:
	@sh scripts/workspace.sh check
	sh scripts/F7-B02/check-approval.sh

test-ff-lint:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-ff-web pnpm lint

test-ff-vitest:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T -e NEXT_PUBLIC_BASE_PATH= -e JRC_PUBLIC_ORIGIN= jrc-ff-web pnpm test --maxWorkers=1 $(VITEST_ARGS)

ff-account:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T -e PYTHONPATH=/workspace jrc-ff-api python /jrc-scripts/init-freeframe-account.py

reset:
	@sh scripts/restore-lock.sh check
	$(ADMIN_COMPOSE) down --volumes
	$(MAKE) up

logs:
	$(ADMIN_COMPOSE) logs --tail 100

ps:
	$(ADMIN_COMPOSE) ps

# --- F2-B09: zálohy a obnova (lokálně, jen v Dockeru; podrobně aplikace/provoz/zalohy.md) ---
# Záloha jen čte běžící prostředí a zapisuje do backups/<projekt>/<datum-čas>/ (mimo git).
backup:
	@sh scripts/backup/backup.sh backup

# Přepočítá všechny kontrolní součty zálohy (BACKUP=backups/<projekt>/<datum-čas>).
backup-verify:
	@sh scripts/backup/backup.sh verify "$(BACKUP)"

# P1-B04: z ověřené místní zálohy uloží jen manifest a oba databázové dumpy, celé šifrované.
backup-offsite:
	@sh scripts/backup/offsite.sh backup

# Dešifruje databázovou zálohu do backups/ ve formátu F2-B09, poté lze spustit make restore.
restore-offsite:
	@sh scripts/backup/offsite.sh restore "$(BACKUP)" "$(AGE_KEY_FILE)"

# Obnova jen do PRÁZDNÉHO projektu. jrc jen s JRC_RESTORE_MAIN=yes-empty (nic z jrc neexistuje či je prázdné);
# neprázdné svazky odmítne vždy.
restore:
	@sh scripts/backup/backup.sh restore-run "$(BACKUP)"

# Pojistky záloh bez běžících služeb: jednotkové testy a odmítnutí obnovy do projektu jrc.
test-backup-guards:
	docker run --rm --pull never --network none --user "$(JRC_UID):$(JRC_GID)" \
	  --env PYTHONDONTWRITEBYTECODE=1 --volume "$(CURDIR):/w:ro" --workdir /w/scripts/backup \
	  jrc-dev:$(JRC_PROJECT) python3 -m unittest -b -q
	sh scripts/backup/test-guards.sh

# Úplný průchod: vlastní data, záloha, obnova do jiného prázdného projektu, porovnání, Chromium.
test-backup: check-project-ports
	sh scripts/backup/test-backup.sh

test-offsite:
	sh scripts/backup/test-offsite.sh
