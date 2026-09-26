SHELL := /bin/bash
.DEFAULT_GOAL := help

PODMAN ?= podman
PODMAN_COMPOSE ?= podman-compose
CURL ?= curl
OPENSSL ?= openssl
API_PORT ?= 8000
API_URL ?= http://127.0.0.1:$(API_PORT)
PROJECT_NAME ?= mvp_v2
BACKEND_CONTAINER ?= $(PROJECT_NAME)_backend_1
TELEGRAM_CONTAINER ?= $(PROJECT_NAME)_telegram_1
REDIS_CONTAINER ?= $(PROJECT_NAME)_redis_1
LOG_EXPORT_DIR ?= runtime/logs/gateway-export
EXPLORER_ENGINE ?= $(PODMAN)
EXPLORER_IMAGE ?= ghcr.io/ladybugdb/explorer:latest
EXPLORER_PORT ?= 8000
EXPLORER_DATABASE_ROOT ?= runtime/databases
EXPLORER_DATABASE ?=
REQUIRED_ENV := MVP_V2_DATABASE_NAME MVP_V2_CHAT_MODEL MVP_V2_CHAT_MODEL_VERSION TELEGRAM_ALLOWED_USER_IDS

.PHONY: help tools env-check secrets secrets-check config build deploy up wait status health explorer diagnose-models logs export-logs down clean

help: ## Show the available gateway commands.
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_-]+:.*## / {printf "%-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

tools: ## Verify host tools without touching a Python virtual environment.
	@command -v $(PODMAN) >/dev/null || { echo "podman is required" >&2; exit 1; }
	@command -v $(PODMAN_COMPOSE) >/dev/null || { echo "podman-compose is required" >&2; exit 1; }
	@command -v $(CURL) >/dev/null || { echo "curl is required" >&2; exit 1; }
	@command -v $(OPENSSL) >/dev/null || { echo "openssl is required" >&2; exit 1; }

env-check: ## Check required .env keys without printing their values.
	@test -f .env || { echo ".env is missing" >&2; exit 1; }
	@missing=0; \
	for key in $(REQUIRED_ENV); do \
		if ! grep -Eq "^$${key}=.+" .env; then echo "$${key} is missing or empty" >&2; missing=1; fi; \
	done; \
	test "$${missing}" -eq 0
	@echo ".env requirements passed"

secrets: tools ## Create ignored mode-0600 Compose secrets; prompts only when absent.
	@if [[ -L secrets || ( -e secrets && ! -d secrets ) ]]; then echo "secrets must be a real directory" >&2; exit 1; fi
	@mkdir -p secrets && chmod 700 secrets
	@if [[ -L secrets/telegram_bot_token ]]; then echo "telegram secret must not be a symlink" >&2; exit 1; fi; \
	if [[ ! -s secrets/telegram_bot_token ]]; then \
		[[ -t 0 ]] || { echo "interactive terminal required to create Telegram secret" >&2; exit 1; }; \
		read -r -s -p "Telegram bot token: " token; printf '\n'; \
		[[ -n "$${token}" ]] || { echo "Telegram token must not be empty" >&2; exit 1; }; \
		(umask 077; printf '%s' "$${token}" > secrets/telegram_bot_token); unset token; \
	fi
	@if [[ -L secrets/gateway_token ]]; then echo "gateway secret must not be a symlink" >&2; exit 1; fi; \
	if [[ ! -s secrets/gateway_token ]]; then \
		(umask 077; $(OPENSSL) rand -base64 48 | tr -d '\n' > secrets/gateway_token); \
	fi
	@chmod 600 secrets/telegram_bot_token secrets/gateway_token
	@$(MAKE) --no-print-directory secrets-check

secrets-check: ## Validate secret files without reading or printing their values.
	@for file in secrets/telegram_bot_token secrets/gateway_token; do \
		[[ -f "$${file}" && ! -L "$${file}" && -s "$${file}" ]] || { echo "$${file} is missing, empty, or unsafe" >&2; exit 1; }; \
	done
	@test "$$(wc -c < secrets/gateway_token | tr -d ' ')" -ge 32 || { echo "gateway token must contain at least 32 characters" >&2; exit 1; }
	@echo "secret boundary passed"

config: tools env-check secrets-check ## Validate the resolved Compose configuration without printing it.
	@$(PODMAN_COMPOSE) config >/dev/null
	@echo "Compose config valid"

build: config ## Build backend and Telegram images.
	@$(PODMAN_COMPOSE) build

deploy: tools env-check secrets ## Build, start, and wait for backend readiness.
	@$(PODMAN_COMPOSE) config >/dev/null
	@$(PODMAN_COMPOSE) up --build --force-recreate -d
	@$(MAKE) --no-print-directory wait
	@echo "Gateway deployment ready"

up: deploy ## Alias for the deployment and readiness workflow.

wait: ## Wait until the public backend health endpoint reports ready.
	@for attempt in $$(seq 1 60); do \
		if $(CURL) --max-time 3 -fsS "$(API_URL)/health" 2>/dev/null | grep -q '"status":"ready"'; then echo "backend ready"; exit 0; fi; \
		sleep 2; \
	done; \
	echo "backend did not become ready" >&2; $(PODMAN_COMPOSE) ps; exit 1

status: ## Show Compose service status.
	@$(PODMAN_COMPOSE) ps

health: tools ## Print the public backend health response.
	@$(CURL) --max-time 5 -fsS "$(API_URL)/health"
	@echo

explorer: ## Select and open an existing runtime database in read-only Ladybug Explorer.
	@engine="$(EXPLORER_ENGINE)"; root="$(EXPLORER_DATABASE_ROOT)"; port="$(EXPLORER_PORT)"; requested="$(EXPLORER_DATABASE)"; \
	command -v "$${engine}" >/dev/null || { echo "$${engine} is required" >&2; exit 1; }; \
	[[ "$${port}" =~ ^[0-9]+$$ ]] && (( port >= 1 && port <= 65535 )) || { echo "EXPLORER_PORT must be between 1 and 65535" >&2; exit 1; }; \
	[[ -d "$${root}" && ! -L "$${root}" ]] || { echo "$${root} must be a real database directory" >&2; exit 1; }; \
	shopt -s nullglob; candidates=(); \
	for artifact in "$${root}"/*.lbdb; do \
		if [[ -d "$${artifact}" && ! -L "$${artifact}" && -f "$${artifact}/database.lbug" && ! -L "$${artifact}/database.lbug" && -f "$${artifact}/_mvp_v2_manifest.json" && ! -L "$${artifact}/_mvp_v2_manifest.json" ]]; then candidates+=("$${artifact}"); fi; \
	done; \
	(( $${#candidates[@]} > 0 )) || { echo "no canonical .lbdb artifacts found in $${root}" >&2; exit 1; }; \
	selected=""; \
	if [[ -n "$${requested}" ]]; then \
		[[ "$${requested}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.lbdb$$ ]] || { echo "EXPLORER_DATABASE must be a safe .lbdb artifact name" >&2; exit 1; }; \
		for artifact in "$${candidates[@]}"; do [[ "$$(basename "$${artifact}")" = "$${requested}" ]] && selected="$${artifact}"; done; \
		[[ -n "$${selected}" ]] || { echo "database artifact not found or invalid: $${requested}" >&2; exit 1; }; \
	else \
		echo "Available LadybugDB artifacts:"; \
		for index in "$${!candidates[@]}"; do printf '  %d) %s\n' "$$((index + 1))" "$$(basename "$${candidates[$${index}]}")"; done; \
		read -r -p "Select database [1-$${#candidates[@]}]: " choice; \
		[[ "$${choice}" =~ ^[0-9]+$$ ]] && (( choice >= 1 && choice <= $${#candidates[@]} )) || { echo "invalid database selection" >&2; exit 1; }; \
		selected="$${candidates[$$((choice - 1))]}"; \
	fi; \
	selected="$$(cd "$${selected}" && pwd -P)"; \
	engine_args=(); [[ "$$(basename "$${engine}")" = podman ]] && engine_args+=(--userns=keep-id); \
	echo "Opening $$(basename "$${selected}") read-only at http://127.0.0.1:$${port} with $${engine}"; \
	exec "$${engine}" run "$${engine_args[@]}" --rm --publish "127.0.0.1:$${port}:8000" --volume "$${selected}:/database:ro" --env LBUG_FILE=database.lbug --env MODE=READ_ONLY "$(EXPLORER_IMAGE)"

diagnose-models: ## Test configured Ollama embedding and generation without exposing payloads.
	@$(PODMAN_COMPOSE) exec backend python -c 'from mvp_v2.adapters.ollama import LocalHttpTransport,OllamaAdapter; from mvp_v2.backend.config import GatewaySettings; from mvp_v2.backend.runtime import load_database_manifest; settings=GatewaySettings.from_environment(); manifest=load_database_manifest(settings.database_path,settings.database_name); adapter=OllamaAdapter(LocalHttpTransport(settings.ollama_base_url,allowed_lan_hosts=settings.ollama_allowed_lan_hosts)); print("embedding model:",manifest.embedding_space.model); vectors=adapter.embed(texts=("gateway diagnostic",),model=manifest.embedding_space.model); actual=len(vectors[0]); print("embedding dimension:",actual,"expected:",manifest.embedding_space.dimension); assert actual == manifest.embedding_space.dimension, "embedding dimension mismatch"; print("chat model:",settings.chat_model); response=adapter.generate(prompt="Reply only with OK.",model=settings.chat_model); assert response.strip(), "empty generation response"; print("embedding and generation diagnostics passed")'

logs: ## Show the last 100 Compose log lines without following.
	@$(PODMAN_COMPOSE) logs --tail=100

export-logs: ## Copy JSONL and bounded container logs to the ignored host log directory.
	@mkdir -p "$(LOG_EXPORT_DIR)"
	@$(PODMAN) cp "$(BACKEND_CONTAINER):/var/lib/mvp-v2/gateway-logs/." "$(LOG_EXPORT_DIR)/"
	@$(PODMAN) logs --tail=500 "$(BACKEND_CONTAINER)" > "$(LOG_EXPORT_DIR)/backend-container.log" 2>&1
	@$(PODMAN) logs --tail=500 "$(TELEGRAM_CONTAINER)" > "$(LOG_EXPORT_DIR)/telegram-container.log" 2>&1
	@chmod 600 "$(LOG_EXPORT_DIR)/backend-container.log" "$(LOG_EXPORT_DIR)/telegram-container.log"
	@echo "gateway JSONL and container logs exported to $(LOG_EXPORT_DIR)"

down: ## Stop and remove Compose containers/network; preserve volumes and secrets.
	@$(PODMAN_COMPOSE) down

clean: ## Remove Compose containers/network; preserve images, volumes, secrets, and data.
	@$(PODMAN_COMPOSE) down
	@echo "Compose containers removed; images, volumes, secrets, and data preserved"
