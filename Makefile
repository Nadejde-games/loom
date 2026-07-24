# Loom — common tasks. Run `./setup.sh` first to create .venv and .env.
# `make` on its own lists the targets.
PY := .venv/bin/python

.DEFAULT_GOAL := help
.PHONY: help setup server play workbench test smoke docs _venv

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

DOCS_PORT ?=

docs: _venv  ## Preview the documentation site locally (needs the [docs] extra; picks a free port, or pin one: make docs DOCS_PORT=8080)
	@port="$(DOCS_PORT)"; \
	if [ -z "$$port" ]; then \
		port=$$($(PY) -c "import socket; print(next(p for p in range(8000, 8051) if socket.socket().connect_ex(('127.0.0.1', p))))"); \
	fi; \
	$(PY) -m mkdocs serve -a 127.0.0.1:$$port

_venv:
	@test -x $(PY) || { echo "No virtualenv found — run ./setup.sh first."; exit 1; }
