.PHONY: lint test-unit test-integration test-e2e evaluate

lint:
	ruff check src tests

test-unit:
	python -m pytest -m unit

test-integration:
	python -m pytest -m integration

test-e2e:
	python -m pytest -m e2e

test:
	python -m pytest -q

evaluate:
	PYTHONPATH=. python scripts/evaluate_retrieval.py --dataset eval/datasets/sample.jsonl
