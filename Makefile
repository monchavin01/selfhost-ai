# ThaiDub GPU model server — Makefile.
# Production runs the image as a Vast/RunPod template (see README). These targets build the image and run it on a
# local NVIDIA box for testing only.

SHELL := /usr/bin/env bash

ifneq (,$(wildcard .env))
  include .env
  export
endif

IMAGE      ?= ghcr.io/monchavin01/thaidub-gpu-server:dev
HUNYUAN_IMAGE ?= $(IMAGE)-hunyuan
VLLM_BASE  := vllm/vllm-openai:v0.31.0-cu129
CPUS       ?= 8
MEMORY     ?= 24g

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo ""
	@echo "  ThaiDub GPU model server"
	@echo ""
	@echo "    make init       Copy .env.example to .env (first time only)"
	@echo "    make build      Build both tags: light ($(IMAGE)) and hunyuan ($(HUNYUAN_IMAGE))"
	@echo "    make push       Push both tags for the Vast/RunPod template"
	@echo "    make vast-offers   List the cheapest Vast offers that meet the minimum spec"
	@echo "    make vast-up       Rent the cheapest one, wait for READY (prints cold-start time)"
	@echo "    make vast-tunnel   Forward :4000 (ThaiDub) and :8001 (bench) to the rental"
	@echo "    make vast-voices   Upload voices/ to the rental (also done by vast-up)"
	@echo "    make vast-ssh / vast-logs / vast-template / vast-down (asks before destroying)"
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
	docker build -t $(HUNYUAN_IMAGE) --build-arg BASE_IMAGE=$(VLLM_BASE) .

.PHONY: push
push:
	docker push $(IMAGE)
	docker push $(HUNYUAN_IMAGE)

.PHONY: run
run:
	docker run --rm -it --gpus all --cpus $(CPUS) --memory $(MEMORY) --env-file .env \
		-p 127.0.0.1:4000:4000 -v $(CURDIR)/data:/data $(IMAGE)

.PHONY: vast-offers vast-up vast-voices vast-ssh vast-tunnel vast-logs vast-template vast-down
vast-offers vast-up vast-voices vast-ssh vast-tunnel vast-logs vast-template vast-down:
	./scripts/vast.sh $(subst vast-,,$@)

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
