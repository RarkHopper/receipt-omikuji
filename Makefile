.PHONY: setup format lint check test dry-run start

UV_CACHE_DIR := $(CURDIR)/build/uv-cache
export UV_CACHE_DIR
PRINTER_ARGS ?= --vid 0x0416 --pid 0x5011 --interface 0 --endpoint 0x03
ARGS ?=

setup:
	uv sync --locked

format:
	uv run --locked ruff format src spec/impl

lint:
	uv run --locked ruff format --check src spec/impl
	uv run --locked ruff check src spec/impl
	uv run --locked mypy

check: lint test

test:
	uv run --locked -- gauge validate spec
	uv run --locked -- gauge run --install-plugins=false --simple-console spec

dry-run:
	uv run --locked receipt-omikuji dry-run --layout vertical --step --realtime $(ARGS)

start:
	uv run --locked receipt-omikuji print --allow-print $(PRINTER_ARGS) --layout vertical --step $(ARGS)
