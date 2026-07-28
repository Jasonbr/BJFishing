.PHONY: install test test-fast type-check lint security verify run clean

install:
	pip install -r requirements.txt

test:
	python -m pytest --cov=. --cov-report=term-missing --cov-fail-under=80

test-fast:
	python -m pytest -x -q

type-check:
	python -m mypy .

lint:
	python -m ruff check .

security:
	pip-audit -r requirements.txt

verify: lint type-check test
	@echo "All checks passed."

run:
	python -m services.server

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -f .coverage data/feedback.db data/feedback_export.json 2>/dev/null || true
