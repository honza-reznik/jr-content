SHELL := /bin/sh
.DEFAULT_GOAL := up
# Povolen je jen JR Content; oddělený jrc-* projekt slouží k ověření worktree.
export JRC_PROJECT ?= jrc
# Jen písmena, číslice a pomlčky (review F2-B01: žádné shellové znaky v příkazech).
ifneq ($(shell printf '%s' '$(subst ','',$(JRC_PROJECT))' | grep -Ex 'jrc(-[a-z0-9-]+)?'),$(JRC_PROJECT))
$(error JRC_PROJECT musí být jrc nebo jrc-<malá písmena, číslice, pomlčky>)
endif
override COMPOSE := docker compose --project-name $(JRC_PROJECT) --file docker-compose.yml --env-file .env
# Správa existujících kontejnerů nepotřebuje hesla ani místní .env.
# Zástupné hodnoty se používají pouze pro načtení konfigurace, nikdy pro up.
override ADMIN_COMPOSE := PGPASSWORD=unused GARAGE_RPC_SECRET=unused AWS_ACCESS_KEY_ID=unused AWS_SECRET_ACCESS_KEY=unused JRC_BB_SECRET_KEY=unused JRC_BB_ENCRYPTION_KEY_SALT=unused JRC_FF_JWT_SECRET=unused JRC_FF_SYNC_PASSWORD=unused JRC_FF_S3_ACCESS_KEY=unused JRC_FF_S3_SECRET_KEY=unused docker compose --project-name $(JRC_PROJECT) --file docker-compose.yml --env-file /dev/null
export JRC_UID := $(shell id -u)
export JRC_GID := $(shell id -g)
# Buildx zapisuje provozní údaje pouze do worktree, i v omezené relaci agenta.
export BUILDX_CONFIG := $(CURDIR)/.cache/docker-buildx
CMD ?= bash
export CMD

.PHONY: build build-bb env up down shell test test-smoke test-bb test-ff test-ff-smoke test-isolation test-ff-offline ff-account build-ff reset logs ps
build:
	docker build --tag jrc-dev:local --file docker/Dockerfile docker

env:
	@sh scripts/workspace.sh env
	@docker image inspect jrc-dev:local >/dev/null 2>&1 || $(MAKE) build
	docker run --rm --name $(JRC_PROJECT)-env --network none --pull never \
	  --user "$(JRC_UID):$(JRC_GID)" --volume "$(CURDIR):/workspace" \
	  jrc-dev:local node scripts/init-env.cjs

build-bb:
	docker build --tag jrc-bb:local brightbean
	# Zdrojový bind mount skryje CSS sestavené v obrazu; přeneseme je do worktree.
	docker run --rm --network none --pull never --user "$(JRC_UID):$(JRC_GID)" \
	  --volume "$(CURDIR)/brightbean:/source" jrc-bb:local sh -c \
	  'mkdir -p /source/theme/static/css/dist && cp /app/theme/static/css/dist/styles.css /source/theme/static/css/dist/styles.css'

build-ff:
	docker build --tag jrc-ff-api:local --file docker/Dockerfile.ff-api .
	docker build --tag jrc-ff-web:local --file docker/Dockerfile.ff-web .

up: env
	$(MAKE) build
	$(MAKE) build-bb
	$(MAKE) build-ff
	$(COMPOSE) up --detach --build --wait --wait-timeout 120
	@sh scripts/workspace.sh remember

down:
	@sh scripts/workspace.sh remember
	$(ADMIN_COMPOSE) down

shell:
	@sh scripts/workspace.sh check
	@if [ -t 0 ]; then \
		$(COMPOSE) exec jrc-dev sh -c "$$CMD"; \
	else \
		$(COMPOSE) exec -T jrc-dev sh -c "$$CMD"; \
	fi

test: test-smoke test-bb test-ff

test-smoke:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-dev bash scripts/smoke.sh
	$(COMPOSE) exec -T jrc-dev node scripts/test-init-env.cjs
	$(COMPOSE) exec -T jrc-bb-web python - web < scripts/smoke-bb.py
	$(COMPOSE) exec -T jrc-bb-worker python - worker < scripts/smoke-bb.py

test-bb:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-bb-web sh -c 'ruff check . && ruff format --check .'
	$(COMPOSE) exec -T jrc-bb-web pytest -p no:cacheprovider -o addopts="" -q --tb=short --ds=config.settings.jrc_test

test-isolation:
	@sh scripts/workspace.sh check
	sh scripts/test-service-isolation.sh $(COMPOSE)
	docker run --rm --pull never --network host --user "$(JRC_UID):$(JRC_GID)" \
	  --env-file .env --volume "$(CURDIR)/scripts:/jrc-scripts:ro" \
	  jrc-dev:local node /jrc-scripts/test-local-gate.cjs

test-ff-offline:
	@sh scripts/workspace.sh check
	$(COMPOSE) run --rm --no-deps -T jrc-ff-web node /jrc-scripts/test-ff-offline.cjs

test-ff: test-isolation test-ff-offline
	@sh scripts/workspace.sh check
	@for service in jrc-ff-worker jrc-ff-maintenance jrc-ff-beat; do \
	  $(COMPOSE) exec -T $$service python - < scripts/smoke-ff-internal.py || exit $$?; \
	done
	$(COMPOSE) exec -T -e STALE_UPLOAD_TIMEOUT_HOURS=24 -e SOFT_DELETE_RETENTION_DAYS=30 jrc-ff-api python -m pytest apps/api/tests -q -p no:cacheprovider
	# Upstream jednotkové testy předpokládají kořenovou URL; /video ověřuje živý test a build.
	$(COMPOSE) exec -T -e NEXT_PUBLIC_BASE_PATH= jrc-ff-web pnpm test --maxWorkers=2
	$(COMPOSE) exec -T jrc-ff-web pnpm exec tsc --noEmit
	$(COMPOSE) exec -T jrc-ff-web pnpm lint

# Podepsané adresy S3 obsahují 127.0.0.1, proto ověření běží v Dockeru se sítí hostitele.
test-ff-smoke:
	@sh scripts/workspace.sh check
	docker run --rm --network host --pull never --user "$(JRC_UID):$(JRC_GID)" \
	  --env-file .env --volume "$(CURDIR):/jrc" --workdir /jrc \
	  jrc-ff-api:local python scripts/smoke-ff.py

ff-account:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T -e PYTHONPATH=/workspace jrc-ff-api python /jrc-scripts/init-freeframe-account.py

reset:
	$(ADMIN_COMPOSE) down --volumes
	$(MAKE) up

logs:
	$(ADMIN_COMPOSE) logs --tail 100

ps:
	$(ADMIN_COMPOSE) ps
