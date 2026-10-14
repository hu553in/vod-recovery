.DEFAULT_GOAL := check

SHELL := /bin/bash -euo pipefail

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
	uv run --locked ruff check
	uv run --locked ruff format --check

.PHONY: lint-fix
lint-fix:
	$(PRETTIER) -w .
	$(TAPLO) fmt
	uv run --locked ruff check --fix
	uv run --locked ruff format

.PHONY: check-hooks
check-hooks:
	uv run --locked prek validate-config prek.toml

.PHONY: check-types
check-types:
	uv run --locked ty check .

.PHONY: check-deps
check-deps:
	uv run --locked deptry .

.PHONY: check-vulns
check-vulns:
	uv run --locked pysentry-rs .

.PHONY: check-unused
check-unused:
	uv run --locked vulture

.PHONY: check-security
check-security:
	git ls-files --cached --others --exclude-standard -z -- '*.py' | xargs -0 uv run --locked bandit -c pyproject.toml

.PHONY: check-build
check-build:
	output_dir="$$(mktemp -d)"; \
	trap 'rm -rf "$$output_dir"' EXIT; \
	uv build --out-dir "$$output_dir"

.PHONY: check-renovate
check-renovate:
	bunx --package renovate renovate-config-validator --strict --no-global renovate.json

.PHONY: test
test:
	uv run --locked pytest

.PHONY: check-workflows
check-workflows:
	$(ACTIONLINT)

.PHONY: check
check: lint check-hooks check-types check-deps check-vulns check-unused check-security check-build check-renovate test check-workflows

.PHONY: check-fix
check-fix: lint-fix
	$(MAKE) check
