SHELL := /bin/sh
.DEFAULT_GOAL := up
# Pevný projekt a soubor zabrání zásahu do jiných Compose projektů.
override COMPOSE := docker compose --project-name jrc --file docker-compose.yml --env-file .env
export JRC_HOST_UID := $(shell id -u)
export JRC_HOST_GID := $(shell id -g)
# Buildx zapisuje provozní údaje pouze do worktree, i v omezené relaci agenta.
export BUILDX_CONFIG := $(CURDIR)/.cache/docker-buildx
CMD ?= bash
export CMD

.PHONY: build env up down shell test reset logs ps
build:
	docker build --tag jrc-dev:local --file docker/Dockerfile docker

env:
	@if [ ! -f .env ]; then \
		$(MAKE) build && \
		docker run --rm --name jrc-env --user "$(JRC_HOST_UID):$(JRC_HOST_GID)" \
		  --volume "$(CURDIR):/workspace" jrc-dev:local node scripts/init-env.cjs; \
	fi

up: env
	$(MAKE) build
	$(COMPOSE) up --detach --build --wait --wait-timeout 120

down:
	$(COMPOSE) down

shell:
	@if [ -t 0 ]; then \
		$(COMPOSE) exec jrc-dev sh -c "$$CMD"; \
	else \
		$(COMPOSE) exec -T jrc-dev sh -c "$$CMD"; \
	fi

test:
	$(COMPOSE) exec -T jrc-dev bash scripts/smoke.sh

reset: env
	$(COMPOSE) down --volumes
	$(MAKE) up

logs:
	$(COMPOSE) logs --tail 100

ps:
	$(COMPOSE) ps
