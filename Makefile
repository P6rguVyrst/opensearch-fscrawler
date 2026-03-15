.PHONY: develop install hooks security update-security-baseline test test-integration test-all lint format typecheck up down build clean

PYTHON  = .venv/bin/python
PYTEST  = .venv/bin/pytest
RUFF    = .venv/bin/ruff
MYPY    = .venv/bin/mypy
BANDIT  = .venv/bin/bandit

develop: install hooks
	@echo ""
	@echo "Done. Activate your shell environment with:"
	@echo "  source .venv/bin/activate"
	@echo ""
	@echo "Then start services with: make up"

install:
	uv venv
	uv sync --all-extras

hooks:
	git config core.hooksPath .githooks
	@echo "Git hooks installed. Pre-commit security scan is now active."

security:
	$(PYTHON) scripts/security_scan.py

update-security-baseline:
	$(PYTHON) scripts/update_security_baseline.py

test:
	$(PYTEST) tests/unit -sv --cov=fscrawler --cov-report=term-missing --cov-report=html:htmlcov

test-integration:
	$(PYTEST) tests/integration -sv -m integration --cov=fscrawler --cov-report=term-missing --cov-report=html:htmlcov

test-all:
	$(PYTEST) tests/ -sv --cov=fscrawler --cov-report=term-missing --cov-report=html:htmlcov --cov-report=xml:coverage.xml

lint:
	$(RUFF) check src tests

format:
	$(RUFF) format src tests

typecheck:
	$(MYPY) src

up:
	docker compose up -d

down:
	docker compose down

build:
	docker buildx build --platform linux/amd64,linux/arm64 -t fscrawler:latest .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	rm -rf dist/ build/ htmlcov/ .mypy_cache/ 2>/dev/null || true
