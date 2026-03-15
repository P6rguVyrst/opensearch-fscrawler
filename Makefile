.PHONY: install test test-integration test-all lint format typecheck up down build clean

install:
	uv pip install -e ".[dev]"

test:
	pytest tests/unit -sv --cov=fscrawler --cov-report=term-missing --cov-report=html:htmlcov

test-integration:
	pytest tests/integration -sv -m integration --cov=fscrawler --cov-report=term-missing --cov-report=html:htmlcov

test-all:
	pytest tests/ -sv --cov=fscrawler --cov-report=term-missing --cov-report=html:htmlcov --cov-report=xml:coverage.xml

lint:
	ruff check src tests

format:
	ruff format src tests

typecheck:
	mypy src

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
