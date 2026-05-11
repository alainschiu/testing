.PHONY: dev test migrate scout doctor lint fmt install clean

install:
	uv sync

dev:
	uv run uvicorn scout.web.app:app --reload --host 127.0.0.1 --port 8000

test:
	uv run pytest -v

migrate:
	uv run scout migrate

scout:
	uv run scout run

doctor:
	uv run scout doctor

lint:
	uv run ruff check src tests

fmt:
	uv run ruff format src tests

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__ *.egg-info build dist
