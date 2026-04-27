.PHONY: lint format format-check typecheck test daemon install-dev

lint:
	ruff check windowcharmer/ tests/

format:
	ruff format windowcharmer/ tests/
	ruff check --fix windowcharmer/ tests/

format-check:
	ruff format --check windowcharmer/ tests/
	ruff check windowcharmer/ tests/

typecheck:
	mypy windowcharmer/

test:
	pytest -v

daemon:
	bash start_daemon.sh

install-dev:
	pip install -e ".[dev]"
