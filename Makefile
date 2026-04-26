# Coding Stack — Makefile
# Single entry point for everything. Run `make` with no args to see commands.

SHELL := /usr/bin/env bash

# Load .env so ADMIN_TOKEN, LITELLM_MASTER_KEY etc. are available as env vars.
# Makefile rules that call ./scripts/admin will have them in the environment.
ifneq (,$(wildcard .env))
  include .env
  export
endif

COMPOSE     := docker compose
ADMIN       := ./scripts/admin
PROFILES    := fast coder reason smart local

# ----------------------------------------------------------------
# Default target — friendly help screen
# ----------------------------------------------------------------
.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo ""
	@echo "  Coding Stack — admin-only model switching"
	@echo ""
	@echo "  Setup"
	@echo "    make init              Copy .env.example to .env (first-time only)"
	@echo "    make check             Validate .env, Docker, and GPU access"
	@echo "    make build             Build the admin-api image"
	@echo ""
	@echo "  Lifecycle"
	@echo "    make up                Start core services (gateway, DBs, admin-api)"
	@echo "    make down              Stop everything"
	@echo "    make restart           Restart core services"
	@echo "    make logs              Tail logs from all services"
	@echo "    make logs-SERVICE      Tail logs for one service (litellm, admin-api, etc.)"
	@echo ""
	@echo "  Profile control"
	@echo "    make status            Show active profile, locks, containers"
	@echo "    make switch P=coder    Activate a profile: fast | coder | reason | smart | off"
	@echo "    make lock P=coder R='demo'   Pin profile, reject switch requests"
	@echo "    make unlock            Remove lock"
	@echo "    make reset             Recover from stuck state"
	@echo ""
	@echo "  Testing"
	@echo "    make test              Send a test chat completion to the active profile"
	@echo "    make ping              Hit all health endpoints"
	@echo ""
	@echo "  Maintenance"
	@echo "    make warmup            Download all model weights (runs overnight)"
	@echo "    make pull              Pull latest Docker images"
	@echo "    make clean             Stop everything + remove volumes (destroys DB)"
	@echo ""
	@echo "  Shortcuts"
	@echo "    make fast              Same as: make switch P=fast"
	@echo "    make coder             Same as: make switch P=coder"
	@echo "    make reason            Same as: make switch P=reason"
	@echo "    make smart             Same as: make switch P=smart"
	@echo "    make local             Same as: make switch P=local  (CPU-only, Mac/no-GPU)"
	@echo "    make off               Same as: make switch P=off"
	@echo ""
	@echo "  Mac / no-GPU testing"
	@echo "    make check-mac         Validate setup without GPU requirement"
	@echo "    make up                Start core services (works on Mac)"
	@echo "    make local             Load Qwen2.5-0.5B (CPU, ~300 MB, no HF token needed)"
	@echo ""

# ----------------------------------------------------------------
# Setup
# ----------------------------------------------------------------
.PHONY: init
init:
	@if [[ -f .env ]]; then \
	  echo "✓ .env already exists (not overwriting)"; \
	else \
	  cp .env.example .env; \
	  echo "✓ created .env — now edit it and set real values"; \
	  echo "  especially: HF_TOKEN, LITELLM_MASTER_KEY, PG_PASSWORD, ADMIN_TOKEN"; \
	fi
	@chmod +x scripts/admin

.PHONY: check
check:
	@echo "→ checking .env..."
	@test -f .env || { echo "✗ .env missing. Run: make init"; exit 1; }
	@grep -q "^HF_TOKEN=hf_" .env || { echo "✗ HF_TOKEN not set in .env"; exit 1; }
	@grep -q "^ADMIN_TOKEN=" .env && ! grep -q "^ADMIN_TOKEN=change-this" .env || \
	  { echo "✗ ADMIN_TOKEN not customized in .env"; exit 1; }
	@echo "✓ .env looks good"
	@echo "→ checking Docker..."
	@docker info >/dev/null 2>&1 || { echo "✗ Docker daemon not reachable"; exit 1; }
	@echo "✓ Docker is running"
	@echo "→ checking NVIDIA GPU..."
	@docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi -L \
	  >/dev/null 2>&1 || { echo "✗ GPU not visible to Docker. Install nvidia-container-toolkit."; exit 1; }
	@echo "✓ GPU accessible from Docker"

.PHONY: check-mac
check-mac:
	@echo "→ checking .env (Mac / no-GPU mode)..."
	@test -f .env || { echo "✗ .env missing. Run: make init"; exit 1; }
	@grep -q "^ADMIN_TOKEN=" .env && ! grep -q "^ADMIN_TOKEN=change-this" .env || \
	  { echo "✗ ADMIN_TOKEN not customized in .env"; exit 1; }
	@grep -q "^LITELLM_MASTER_KEY=" .env && ! grep -q "^LITELLM_MASTER_KEY=sk-change" .env || \
	  { echo "✗ LITELLM_MASTER_KEY not customized in .env"; exit 1; }
	@grep -q "^PG_PASSWORD=" .env && ! grep -q "^PG_PASSWORD=change" .env || \
	  { echo "✗ PG_PASSWORD not customized in .env"; exit 1; }
	@echo "✓ .env looks good"
	@echo "→ checking Docker..."
	@docker info >/dev/null 2>&1 || { echo "✗ Docker daemon not reachable"; exit 1; }
	@echo "✓ Docker is running"
	@echo "✓ GPU check skipped (local profile uses CPU-only inference)"
	@echo ""
	@echo "  Ready. Run: make build && make up && make local"

.PHONY: build
build:
	$(COMPOSE) build admin-api

# ----------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------
.PHONY: up
up:
	$(COMPOSE) up -d litellm postgres redis langfuse admin-api
	@mkdir -p state
	@test -f state/active-profile || echo "off" > state/active-profile
	@echo ""
	@echo "✓ core services up"
	@echo "    LiteLLM :   http://localhost:4000 (user API)"
	@echo "    Admin API:  http://localhost:8080 (admin only, loopback)"
	@echo "    Langfuse :  http://localhost:3000 (observability)"
	@echo ""
	@echo "  Next: activate a profile with 'make switch P=fast'"

.PHONY: down
down:
	@for p in $(PROFILES); do $(COMPOSE) --profile $$p down; done
	$(COMPOSE) down
	@echo "✓ everything stopped"

.PHONY: restart
restart: down up

.PHONY: logs
logs:
	$(COMPOSE) logs -f --tail=100

logs-%:
	$(COMPOSE) logs -f --tail=200 $*

# ----------------------------------------------------------------
# Profile control (via admin API)
# ----------------------------------------------------------------
.PHONY: status
status:
	@$(ADMIN) status

.PHONY: switch
switch:
	@test -n "$(P)" || { echo "usage: make switch P=<fast|coder|reason|smart|off>"; exit 1; }
	@$(ADMIN) switch $(P)

.PHONY: lock
lock:
	@test -n "$(P)" || { echo "usage: make lock P=<profile> [R='reason']"; exit 1; }
	@$(ADMIN) lock $(P) "$(R)"

.PHONY: unlock
unlock:
	@$(ADMIN) unlock

.PHONY: reset
reset:
	@$(ADMIN) reset

# Profile shortcuts
.PHONY: fast coder reason smart local off
fast:   ; @$(ADMIN) switch fast
coder:  ; @$(ADMIN) switch coder
reason: ; @$(ADMIN) switch reason
smart:  ; @$(ADMIN) switch smart
local:  ; @$(ADMIN) switch local
off:    ; @$(ADMIN) switch off

# ----------------------------------------------------------------
# Testing / smoke tests
# ----------------------------------------------------------------
.PHONY: ping
ping:
	@echo -n "LiteLLM   : "; curl -sS http://localhost:4000/health/readiness -o /dev/null -w "%{http_code}\n" || echo "DOWN"
	@echo -n "Admin API : "; curl -sS http://localhost:8080/status -H "X-Admin-Token: $(ADMIN_TOKEN)" -o /dev/null -w "%{http_code}\n" || echo "DOWN"
	@echo -n "Langfuse  : "; curl -sS http://localhost:3000/api/public/health -o /dev/null -w "%{http_code}\n" || echo "DOWN"

.PHONY: test
test:
	@ACTIVE=$$($(ADMIN) status 2>/dev/null | grep -o '"active_profile": *"[^"]*"' | cut -d'"' -f4); \
	if [[ -z "$$ACTIVE" || "$$ACTIVE" == "off" ]]; then \
	  echo "✗ no profile active. Run: make switch P=fast"; exit 1; \
	fi; \
	echo "→ testing profile '$$ACTIVE'..."; \
	curl -sS http://localhost:4000/v1/chat/completions \
	  -H "Authorization: Bearer $(LITELLM_MASTER_KEY)" \
	  -H "Content-Type: application/json" \
	  -d "{\"model\": \"$$ACTIVE\", \"messages\": [{\"role\":\"user\",\"content\":\"Say hi in one short sentence.\"}]}" \
	  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'])"

# ----------------------------------------------------------------
# Maintenance
# ----------------------------------------------------------------
.PHONY: warmup
warmup:
	@echo "→ downloading all model weights (~100GB, will take hours)"
	@for p in $(PROFILES); do \
	  echo ""; \
	  echo "=== warming up: $$p ==="; \
	  $(ADMIN) switch $$p; \
	  echo "    ($$p loaded; letting it idle before next)"; \
	  sleep 10; \
	done
	@$(ADMIN) switch off
	@echo "✓ all profiles downloaded and cached. Switch is now fast."

.PHONY: pull
pull:
	$(COMPOSE) pull

.PHONY: clean
clean:
	@echo "⚠  this destroys all volumes (DB, model cache, state)"
	@read -p "Type 'yes' to continue: " ans && [[ "$$ans" == "yes" ]]
	@for p in $(PROFILES); do $(COMPOSE) --profile $$p down -v; done
	$(COMPOSE) down -v
	@rm -f state/active-profile state/last-switch-at state/switching state/locked
	@echo "✓ cleaned"
