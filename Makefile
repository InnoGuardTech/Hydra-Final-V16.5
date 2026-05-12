.PHONY: install run test lint docker-build

install:
	poetry install

run:
	poetry run uvicorn main:app --host 0.0.0.0 --port $${PORT:-8080}

test:
	poetry run pytest -q

lint:
	poetry run ruff check .
	poetry run mypy app main.py
	poetry run bandit -q -r app main.py

docker-build:
	docker build -t hydra-final:16.5 .
