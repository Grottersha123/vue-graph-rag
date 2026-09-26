# Local project setup

Run commands from `mvp_v2/`. Do not use or modify the repository-owned `.venv`.
Host-side Python commands must use:

```console
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv uv run --project . <command>
```

## 1. Configure environment

Create the ignored local environment file once:

```console
cp .env.example .env
```

Set at least:

```dotenv
MVP_V2_DATABASE_NAME=database-name-without-.lbdb
MVP_V2_CHAT_MODEL=installed-chat-model
MVP_V2_CHAT_MODEL_VERSION=exact-version
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Do not commit `.env` or print its values in logs.

## 2. Create local secrets

```console
make secrets
```

The command securely prompts for the Telegram bot token when absent, generates the
shared gateway token, and checks mode `0600`. Files under `secrets/` are ignored and
must not be copied into images, documentation, or shell commands.

## 3. Deploy with Podman

```console
make deploy
make status
make health
```

`make deploy` validates configuration, builds and force-recreates the services, and
waits for backend readiness. It does not run an embedded smoke, authenticated RAG,
or container-security test suite.

Use explicit diagnostics when needed:

```console
make diagnose-models
make logs
make export-logs
```

## 4. Inspect a database read-only

```console
make explorer EXPLORER_PORT=8001
```

Select an existing canonical artifact from `runtime/databases/`. The Explorer uses
Podman, binds only to loopback, mounts the artifact read-only, and enables
`MODE=READ_ONLY`.

See [`README.md`](README.md) for complete runtime behavior and
[`docs/module-map.md`](docs/module-map.md) for source ownership.
