.DEFAULT_GOAL := check

SHELL := /bin/bash
.ONESHELL:
.SHELLFLAGS := -euo pipefail -c

PRETTIER := bunx prettier -u
ACTIONLINT := bunx github-actionlint
TAPLO := bunx @taplo/cli

.PHONY: install-deps
install-deps:
	uv sync --all-groups --locked

.PHONY: lint
lint:
	$(PRETTIER) -c .
	$(TAPLO) fmt --check
	uv run ruff check
	uv run ruff format --check

.PHONY: lint-fix
lint-fix:
	$(PRETTIER) -w .
	$(TAPLO) fmt
	uv run ruff check --fix
	uv run ruff format

.PHONY: check-types
check-types:
	uv run ty check .

.PHONY: check-deps
check-deps:
	uv run deptry .

.PHONY: check-vulns
check-vulns:
	uv run pysentry-rs .

.PHONY: check-unused
check-unused:
	uv run vulture

.PHONY: check-security
check-security:
	git ls-files --cached --others --exclude-standard -z -- '*.py' | xargs -0 uv run bandit -c pyproject.toml

.PHONY: check-build
check-build:
	output_dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$output_dir"' EXIT; \
	uv build --out-dir "$$output_dir"

.PHONY: test
test:
	uv run pytest

.PHONY: check-renovate
check-renovate:
	bunx --package renovate renovate-config-validator --strict --no-global renovate.json

.PHONY: check-hooks
check-hooks:
	uv run prek validate-config prek.toml

.PHONY: check-workflows
check-workflows:
	$(ACTIONLINT)

.PHONY: check
check: lint check-hooks check-types check-deps check-vulns check-unused check-security check-build check-renovate test check-workflows

.PHONY: check-fix
check-fix: lint-fix
	$(MAKE) check
