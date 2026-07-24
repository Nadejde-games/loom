# Loom — common tasks. Run `./setup.sh` first to create .venv and .env.
# `make` on its own lists the targets.
PY := .venv/bin/python

.DEFAULT_GOAL := help
.PHONY: help setup server play workbench test smoke _venv

help:  ## List these targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-11s\033[0m %s\n", $$1, $$2}'

setup:  ## Run the interactive setup wizard
	./setup.sh

server: _venv  ## Start the game server (backend read from .env: LOOM_PROVIDER)
	$(PY) game/main.py

play: _venv  ## Connect a terminal client and play
	$(PY) client/terminal.py

workbench: _venv  ## Open the authoring workbench (needs the [authoring] extra)
	$(PY) -m authoring

test: _venv  ## Run the offline test suite (no network, no GPU)
	$(PY) -m unittest discover -s tests

smoke: _venv  ## End-to-end smoke check (the server must already be running)
	$(PY) scripts/smoke.py

_venv:
	@test -x $(PY) || { echo "No virtualenv found — run ./setup.sh first."; exit 1; }
