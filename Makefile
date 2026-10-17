# ThaiDub GPU model server — Makefile (runs on the Mac).
# The rented box needs no image build: the Vast template's On-start clones this repo at a tag and runs
# scripts/bootstrap.sh (see README "Rent and run").

SHELL := /usr/bin/env bash

ifneq (,$(wildcard .env))
  include .env
  export
endif

# Lock files are resolved for the rented box (Linux x86_64), not the Mac.
LOCK := uv pip compile --python-platform x86_64-manylinux_2_28 --generate-hashes --no-header -q

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo ""
	@echo "  ThaiDub GPU model server"
	@echo ""
	@echo "    make init          Copy .env.example to .env (first time only)"
	@echo "    make vast-offers   Cheapest offers matching the template's machine filter"
	@echo "    make vast-up       Rent one from the thaidub-gpu template, upload voices, wait for READY"
	@echo "    make vast-voices   Upload voices/ to the rental (also done by vast-up)"
	@echo "    make vast-logs     Follow /workspace/bootstrap.log"
	@echo "    make vast-ssh      Shell on the rental"
	@echo "    make vast-tunnel   Fallback: forward :4000/:8001 over SSH if Tailscale is down"
	@echo "    make vast-down     Destroy the rental and its disk (asks first)"
	@echo "    make bench         Throughput, contention, VRAM and contract checks against a live server"
	@echo "    make voices        Rebuild voices/manifest.sha256 after adding a voice"
	@echo "    make lock          Re-resolve locks/*.txt from locks/*.in"
	@echo "    make test          Unit tests (no GPU needed)"
	@echo ""

.PHONY: init
init:
	@test -f .env && echo ".env already exists" || (cp .env.example .env && echo "Created .env — fill it in")

.PHONY: vast-offers vast-up vast-voices vast-ssh vast-tunnel vast-logs vast-down
vast-offers vast-up vast-voices vast-ssh vast-tunnel vast-logs vast-down:
	./scripts/vast.sh $(subst vast-,,$@)

.PHONY: bench
bench:
	python3 scripts/bench.py $(LITELLM_URL)/v1 --backend-url $(subst :4000,:8001,$(LITELLM_URL))

.PHONY: voices
voices:
	VOICES_DIR=voices python3 scripts/voices.py build

.PHONY: lock
lock:
	$(LOCK) --python-version 3.11 --extra-index-url https://download.pytorch.org/whl/cu128 \
		--index-strategy unsafe-best-match locks/backend.in -o locks/backend.txt
	$(LOCK) --python-version 3.12 locks/litellm.in -o locks/litellm.txt
	$(LOCK) --python-version 3.12 locks/tools.in -o locks/tools.txt
	$(LOCK) --python-version 3.12 locks/vllm.in -o locks/vllm.txt

.PHONY: test
test:
	python3 -m pytest backend/tests -q
