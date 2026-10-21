# Testy nočního spouštěče ops/tick.sh (blok F0-B05).
# Použití (z kořene pracovní kopie):  make -f ops/Makefile test-tick
# Testy běží jen v Dockeru: malý oficiální obraz s Pythonem, pracovní kopie připojená jen pro čtení,
# bez sítě. Skutečné claude ani codex se nespouštějí (testy používají falešné skripty).

ROOT  := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))..)
IMAGE ?= python:3.13-slim

.PHONY: test-tick
test-tick:
	docker run --rm --name jrc-tick-test-$$$$ --network none \
	  -u "$$(id -u):$$(id -g)" -e HOME=/tmp -e TZ=Europe/Prague -e TICK_TEST_IN_DOCKER=1 \
	  -v "$(ROOT)":/src:ro -w /src $(IMAGE) bash ops/tests/run.sh
