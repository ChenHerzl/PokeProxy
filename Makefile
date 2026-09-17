SHELL := /bin/bash
.DEFAULT_GOAL := help
.NOTPARALLEL:

.PHONY: help tools doctor up down build verify status logs grafana prometheus proxy test lint tunnels tunnels-down

help doctor up down build verify status logs grafana prometheus proxy test lint:
	@bash scripts/local.sh $@

tools:
	@bash scripts/install-local-tools.sh

tunnels:
	@PATH="$(CURDIR)/.local/bin:$$PATH" python3 scripts/tunnels.py up

tunnels-down:
	@python3 scripts/tunnels.py down
