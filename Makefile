# Development entry points. `make check` is what CI runs.
.PHONY: help install dev check lint type test cov fetch run clean

help:  ## show this help
	@grep -hE '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install:  ## install the package
	pip install -e .

dev:  ## install with dev and structural extras, plus pre-commit hooks
	pip install -e ".[dev,structural]"
	pre-commit install || echo "pre-commit not installed; skipping hooks"

check: lint type test  ## everything CI runs

lint:  ## ruff
	ruff check src tests

type:  ## mypy
	mypy

test:  ## pytest
	pytest

cov:  ## pytest with coverage
	pytest --cov=salp --cov-report=term-missing

fetch:  ## clone the source/target repositories a run needs (network)
	salp -c configs/default.yaml fetch-repos

run:  ## construct SAPs from data/gacpd into data/out/
	salp -c configs/default.yaml run

clean:  ## remove caches and generated output
	rm -rf data/out .pytest_cache .mypy_cache .ruff_cache dist build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
