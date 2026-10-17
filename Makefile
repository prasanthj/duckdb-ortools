.PHONY: all release test lint clean
all release:
	./build.sh
test:
	uv run pytest -q
lint:
	uv run ruff check tests scripts examples
clean:
	uv run cmake --build build --target clean
