.PHONY: install run test lint docker-build

install:
	poetry install

run:
	poetry run uvicorn main:app --host 0.0.0.0 --port $${PORT:-8080}

test:
	poetry run pytest -q

lint:
	poetry run ruff check app/core/config.py app/core/config_loader.py app/core/logging_setup.py main.py
	poetry run mypy
	poetry run bandit -q -s B101,B104,B110 app/core/config.py app/core/config_loader.py app/core/logging_setup.py main.py

docker-build:
	docker build -t hydra-final:16.5 .
