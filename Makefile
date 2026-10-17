# ThaiDub GPU model server — Makefile.
# Production runs the image as a Vast/RunPod template (see README). These targets build the image and run it on a
# local NVIDIA box for testing only.

SHELL := /usr/bin/env bash

ifneq (,$(wildcard .env))
  include .env
  export
endif

IMAGE      ?= ghcr.io/monchavin01/thaidub-gpu-server:dev
CPUS       ?= 8
MEMORY     ?= 24g

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo ""
	@echo "  ThaiDub GPU model server"
	@echo ""
	@echo "    make init       Copy .env.example to .env (first time only)"
	@echo "    make build      Build the image ($(IMAGE))"
	@echo "    make push       Push the image for the Vast/RunPod template"
	@echo "    make run        Test-run the image locally (GPU, --cpus $(CPUS), --memory $(MEMORY))"
	@echo "    make status     Health, last GPU lines, last requests, voices hash"
	@echo "    make bench      Throughput, contention, VRAM and contract checks against a live server"
	@echo "    make voices     Rebuild voices/manifest.sha256 after adding a voice"
	@echo "    make test       Unit tests (no GPU needed)"
	@echo ""

.PHONY: init
init:
	@test -f .env && echo ".env already exists" || (cp .env.example .env && echo "Created .env — set LITELLM_MASTER_KEY")

.PHONY: build
build:
	docker build -t $(IMAGE) .

.PHONY: push
push:
	docker push $(IMAGE)

.PHONY: run
run:
	docker run --rm -it --gpus all --cpus $(CPUS) --memory $(MEMORY) --env-file .env \
		-p 127.0.0.1:4000:4000 -v $(CURDIR)/data:/data $(IMAGE)

.PHONY: status
status:
	@./scripts/status.sh

.PHONY: bench
bench:
	python3 scripts/bench.py

.PHONY: voices
voices:
	VOICES_DIR=voices python3 scripts/voices.py build

.PHONY: test
test:
	python3 -m pytest backend/tests -q
