.PHONY: lint test-unit test-integration test-e2e

lint:
	ruff check src tests

test-unit:
	pytest -m unit

test-integration:
	pytest -m integration

test-e2e:
	pytest -m e2e
