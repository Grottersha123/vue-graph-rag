

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
REQUIRED_ENV := MVP_V2_DATABASE_NAME MVP_V2_CHAT_MODEL MVP_V2_CHAT_MODEL_VERSION TELEGRAM_ALLOWED_USER_IDS

.PHONY: help tools env-check secrets secrets-check config build deploy up wait status health smoke smoke-message diagnose-models security verify logs export-logs down clean

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

deploy: tools env-check secrets ## Build, start, wait, and run all runtime/security checks.
	@$(PODMAN_COMPOSE) config >/dev/null
	@$(PODMAN_COMPOSE) up --build --force-recreate -d
	@$(MAKE) --no-print-directory wait
	@$(MAKE) --no-print-directory verify
	@echo "Gateway deployment verified"

up: deploy ## Alias for the complete verified deployment.

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

smoke: ## Check health, Redis, service DNS, authentication, users, and mounts.
	@test "$$($(PODMAN_COMPOSE) exec redis redis-cli ping | tr -d '\r')" = "PONG" || { echo "Redis PING failed" >&2; exit 1; }
	@$(CURL) --max-time 5 -fsS "$(API_URL)/health" | grep -q '"status":"ready"' || { echo "backend health failed" >&2; exit 1; }
	@$(PODMAN_COMPOSE) exec telegram python -c 'import socket; socket.create_connection(("backend", 8000), 3).close(); socket.create_connection(("redis", 6379), 3).close()'
	@code="$$($(CURL) --max-time 10 -sS -o /dev/null -w '%{http_code}' "$(API_URL)/v1/profiles")"; [[ "$${code}" = 401 ]] || { echo "unauthenticated API check returned HTTP $${code}" >&2; exit 1; }
	@auth_file="$$(mktemp)"; trap 'rm -f "$${auth_file}"' EXIT; chmod 600 "$${auth_file}"; \
	printf 'header = "Authorization: Bearer %s"\n' "$$(cat secrets/gateway_token)" > "$${auth_file}"; \
	code="$$($(CURL) --max-time 10 -sS --config "$${auth_file}" -o /dev/null -w '%{http_code}' "$(API_URL)/v1/profiles")"; \
	[[ "$${code}" = 200 ]] || { echo "authenticated profile check returned HTTP $${code}" >&2; exit 1; }
	@$(PODMAN_COMPOSE) exec backend sh -c 'test "$$(id -u)" = 10001 && test ! -w /app/runtime/databases && test ! -e /app/runtime/dumps && test -w /var/lib/mvp-v2/gateway-logs'
	@$(PODMAN_COMPOSE) exec telegram sh -c 'test "$$(id -u)" = 10001 && test -w /var/lib/mvp-v2/gateway-logs'
	@echo "runtime smoke checks passed"

smoke-message: ## Run one authenticated end-to-end RAG request using the first allowed Telegram ID.
	@user_id="$$(grep -E '^TELEGRAM_ALLOWED_USER_IDS=.+' .env | head -n 1 | cut -d= -f2- | cut -d, -f1 | tr -d '[:space:]')"; \
	[[ "$${user_id}" =~ ^[1-9][0-9]*$$ ]] || { echo "cannot derive a valid smoke Telegram user ID" >&2; exit 1; }; \
	auth_file="$$(mktemp)"; body_file="$$(mktemp)"; response_file="$$(mktemp)"; trap 'rm -f "$${auth_file}" "$${body_file}" "$${response_file}"' EXIT; \
	chmod 600 "$${auth_file}" "$${body_file}" "$${response_file}"; \
	printf 'header = "Authorization: Bearer %s"\n' "$$(cat secrets/gateway_token)" > "$${auth_file}"; \
	request_id="make-smoke-$$(date +%s)-$${RANDOM}"; \
	printf '{"request_id":"%s","telegram_user_id":%s,"chat_id":%s,"source_message_id":1,"conversation_id":"%s","question":"Как пройти онбординг?"}' "$${request_id}" "$${user_id}" "$${user_id}" "$${user_id}" > "$${body_file}"; \
	code="$$($(CURL) --max-time 180 -sS --config "$${auth_file}" -H 'Content-Type: application/json' --data-binary @"$${body_file}" -o "$${response_file}" -w '%{http_code}' "$(API_URL)/v1/messages")"; \
	[[ "$${code}" = 200 ]] || { echo "end-to-end message check returned HTTP $${code}; request_id=$${request_id}" >&2; exit 1; }; \
	echo "end-to-end message check passed: $${request_id}"

diagnose-models: ## Test configured Ollama embedding and generation without exposing payloads.
	@$(PODMAN_COMPOSE) exec backend python -c 'from mvp_v2.adapters.ollama import LocalHttpTransport,OllamaAdapter; from mvp_v2.backend.config import GatewaySettings; from mvp_v2.backend.runtime import load_database_manifest; settings=GatewaySettings.from_environment(); manifest=load_database_manifest(settings.database_path,settings.database_name); adapter=OllamaAdapter(LocalHttpTransport(settings.ollama_base_url,allowed_lan_hosts=settings.ollama_allowed_lan_hosts)); print("embedding model:",manifest.embedding_space.model); vectors=adapter.embed(texts=("gateway diagnostic",),model=manifest.embedding_space.model); actual=len(vectors[0]); print("embedding dimension:",actual,"expected:",manifest.embedding_space.dimension); assert actual == manifest.embedding_space.dimension, "embedding dimension mismatch"; print("chat model:",settings.chat_model); response=adapter.generate(prompt="Reply only with OK.",model=settings.chat_model); assert response.strip(), "empty generation response"; print("embedding and generation diagnostics passed")'

security: ## Verify mounts, published ports, and absence of secret values in logs.
	@for container in $(BACKEND_CONTAINER) $(TELEGRAM_CONTAINER) $(REDIS_CONTAINER); do $(PODMAN) container exists "$${container}" || { echo "$${container} is missing" >&2; exit 1; }; done
	@backend_mounts="$$($(PODMAN) inspect $(BACKEND_CONTAINER) --format '{{range .Mounts}}{{printf "%s|%t\n" .Destination .RW}}{{end}}')"; \
	printf '%s\n' "$${backend_mounts}" | grep -Fx '/app/runtime/databases|false' >/dev/null || { echo "database mount is not read-only" >&2; exit 1; }; \
	if printf '%s\n' "$${backend_mounts}" | grep -Eq 'runtime/dumps|docker\.sock|podman\.sock|/\.ssh|/\.gnupg|^/home\||^/root\|'; then echo "forbidden backend mount found" >&2; exit 1; fi
	@for container in $(TELEGRAM_CONTAINER) $(REDIS_CONTAINER); do \
		mounts="$$($(PODMAN) inspect "$${container}" --format '{{range .Mounts}}{{printf "%s\n" .Destination}}{{end}}')"; \
		if printf '%s\n' "$${mounts}" | grep -Eq 'runtime/databases|runtime/dumps|docker\.sock|podman\.sock|/\.ssh|/\.gnupg|^/home$$|^/root$$'; then echo "forbidden mount found in $${container}" >&2; exit 1; fi; \
	done
	@ports="$$($(PODMAN) port $(BACKEND_CONTAINER))"; printf '%s\n' "$${ports}" | grep -Eq '127\.0\.0\.1:' || { echo "backend is not loopback-only" >&2; exit 1; }
	@test -z "$$($(PODMAN) port $(TELEGRAM_CONTAINER) 2>/dev/null || true)" || { echo "Telegram publishes a host port" >&2; exit 1; }
	@test -z "$$($(PODMAN) port $(REDIS_CONTAINER) 2>/dev/null || true)" || { echo "Redis publishes a host port" >&2; exit 1; }
	@log_file="$$(mktemp)"; trap 'rm -f "$${log_file}"' EXIT; $(PODMAN_COMPOSE) logs --no-color > "$${log_file}"; \
	if grep -F -f secrets/telegram_bot_token "$${log_file}" >/dev/null; then echo "Telegram token found in logs" >&2; exit 1; fi; \
	if grep -F -f secrets/gateway_token "$${log_file}" >/dev/null; then echo "gateway token found in logs" >&2; exit 1; fi
	@echo "container security checks passed"

verify: smoke security smoke-message ## Run the complete runtime verification suite.
	@echo "all gateway checks passed"

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
