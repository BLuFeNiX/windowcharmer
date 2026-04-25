.PHONY: lint format typecheck test daemon install-dev

lint:
	ruff check windowcharmer/

format:
	ruff format windowcharmer/ tests/
	ruff check --fix windowcharmer/

typecheck:
	mypy windowcharmer/

test:
	pytest -v

daemon:
	bash start_daemon.sh

install-dev:
	pip install -e ".[dev]"
