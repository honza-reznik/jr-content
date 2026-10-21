SHELL := /bin/sh
.DEFAULT_GOAL := up
# Pevný projekt a soubor zabrání zásahu do jiných Compose projektů.
override COMPOSE := docker compose --project-name jrc --file docker-compose.yml --env-file .env
# Správa existujících kontejnerů nepotřebuje hesla ani místní .env.
# Zástupné hodnoty se používají pouze pro načtení konfigurace, nikdy pro up.
override ADMIN_COMPOSE := PGPASSWORD=unused GARAGE_RPC_SECRET=unused AWS_ACCESS_KEY_ID=unused AWS_SECRET_ACCESS_KEY=unused docker compose --project-name jrc --file docker-compose.yml --env-file /dev/null
export JRC_UID := $(shell id -u)
export JRC_GID := $(shell id -g)
# Buildx zapisuje provozní údaje pouze do worktree, i v omezené relaci agenta.
export BUILDX_CONFIG := $(CURDIR)/.cache/docker-buildx
CMD ?= bash
export CMD

.PHONY: build env up down shell test reset logs ps
build:
	docker build --tag jrc-dev:local --file docker/Dockerfile docker

env:
	@sh scripts/workspace.sh env
	@if [ ! -f .env ]; then \
		$(MAKE) build && \
		docker run --rm --name jrc-env --user "$(JRC_UID):$(JRC_GID)" \
		  --volume "$(CURDIR):/workspace" jrc-dev:local node scripts/init-env.cjs; \
	fi

up: env
	$(MAKE) build
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

test:
	@sh scripts/workspace.sh check
	$(COMPOSE) exec -T jrc-dev bash scripts/smoke.sh

reset:
	$(ADMIN_COMPOSE) down --volumes
	$(MAKE) up

logs:
	$(ADMIN_COMPOSE) logs --tail 100

ps:
	$(ADMIN_COMPOSE) ps
