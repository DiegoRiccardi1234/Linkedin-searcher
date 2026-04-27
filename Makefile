.PHONY: install test coverage e2e lint fmt run docker docker-down clean

install:
	pip install -r requirements.txt -r requirements-dev.txt
	pre-commit install

test:
	pytest

coverage:
	pytest --cov=app --cov-report=term-missing --cov-report=html --cov-report=xml
	python scripts/coverage_badge.py

e2e:
	npm install
	npx playwright install chromium
	npx playwright test

lint:
	ruff check app/
	ruff format --check app/
	mypy app/

fmt:
	ruff check --fix app/
	ruff format app/

run:
	python run_webapp.py

docker:
	docker compose up -d --build

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
