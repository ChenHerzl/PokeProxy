SHELL := /bin/bash
.DEFAULT_GOAL := help
.NOTPARALLEL:

.PHONY: help tools doctor up down build verify status logs grafana prometheus proxy test lint

help doctor up down build verify status logs grafana prometheus proxy test lint:
	@bash scripts/local.sh $@

tools:
	@bash scripts/install-local-tools.sh
