# MVP v2

Local Graph RAG for the Vue project. The application uses LadybugDB for retrieval,
Ollama for embeddings and generation, FastAPI for the local API, and Telegram as
the main chat interface. The Python package is named `vue_rag`; `mvp_v2` is the
project directory.

## What runs in production

The normal deployment starts three services:

- **backend** — validates and opens one LadybugDB artifact read-only, retrieves
  evidence, and generates cited answers;
- **telegram** — accepts messages only from configured Telegram user IDs;
- **redis** — stores user profile and Telegram state.

Ollama runs on the host. The containers connect to it through
`host.containers.internal` by default.

## Requirements

Install these host tools before deployment:

- Podman and `podman-compose`;
- `make`, `curl`, and `openssl`;
- Ollama with the configured chat model and the embedding model recorded in the
  database manifest;
- `uv` only for local Python development and tests.

The gateway also requires a canonical database artifact:

```text
runtime/databases/<MVP_V2_DATABASE_NAME>.lbdb/
├── database.lbug
└── _mvp_v2_manifest.json
```

The database name in `.env` does not include the `.lbdb` suffix.

## Quick start with Podman

Run all commands in this section from `mvp_v2/`.

### 1. Configure the environment

```console
cp .env.example .env
```

Set at least these values in `.env`:

```dotenv
MVP_V2_DATABASE_NAME=onboarding-faq-rag-metadata
MVP_V2_CHAT_MODEL=installed-chat-model
MVP_V2_CHAT_MODEL_VERSION=exact-version
TELEGRAM_ALLOWED_USER_IDS=123456789
```

Optional settings include:

- `MVP_V2_OLLAMA_BASE_URL`;
- `MVP_V2_OLLAMA_TIMEOUT_SECONDS`;
- `MVP_V2_MODEL_CONCURRENCY`;
- `MVP_V2_MINIMUM_SIMILARITY_SCORE`;
- `MVP_V2_DATABASES_DIRECTORY` when database artifacts are outside the default
  `runtime/databases/` directory.

Do not commit `.env`. It can contain local configuration and identifiers.

### 2. Create secrets

```console
make secrets
```

This command securely asks for the Telegram bot token and generates the shared
backend token. The ignored files under `secrets/` use mode `0600`. Do not print,
commit, or copy them into container images.

### 3. Deploy

```console
make deploy
make status
make health
```

`make deploy` validates the required configuration, builds the image,
force-recreates backend, Telegram, and Redis, and waits until the backend is
ready. A ready response confirms access to LadybugDB, Ollama, Redis, and the
audit log.

Deployment does not run an authenticated RAG request or the test suite. Use the
diagnostics below when needed.

## Operations

```console
make help              # list all supported targets
make status            # show service state
make health            # query the public readiness endpoint
make diagnose-models   # test embedding and generation models
make logs              # show the last 100 Compose log lines
make export-logs       # export bounded container and JSONL logs
make down               # stop containers and preserve data and volumes
make clean              # remove containers/network; preserve images and data
```

The main diagnostic files are stored in the `gateway-logs` volume. Exported
copies go to `runtime/logs/gateway-export/`. Treat `errors.jsonl` and
`prompt_traces.jsonl` as sensitive because they can contain tracebacks and full
rendered prompts.

To check the backend inside its container:

```console
podman-compose exec backend   curl -fsS http://127.0.0.1:8000/health
```

On macOS, a `proxy already running` error usually indicates a stale Podman
`gvproxy` listener. First stop the failed container or choose another port.
Restart `podman machine` only when the listener remains stale because restarting
it stops all running Podman containers.

## Runtime data: what is actually used

The deployed backend uses only:

- the selected `runtime/databases/*.lbdb` artifact, mounted read-only;
- the Ollama models on the host;
- Redis and the gateway log volume;
- `.env` and the two files under `secrets/`.

It does **not** read `runtime/mvp_data/`, `runtime/dumps/`, notebooks, evaluation
data, or source documents while answering messages.

Other directories support preparation and development:

| Path | Purpose | Needed by deployed gateway |
| --- | --- | --- |
| `runtime/databases/` | Immutable LadybugDB artifacts | Yes, selected artifact only |
| `runtime/mvp_data/` | Mutable source/media workspace for ingestion | No |
| `runtime/dumps/` | Immutable CSV snapshots used to build databases | No |
| `runtime/debug/` | Evaluation and debugging artifacts | No |
| `data/` | Curation and development inputs | No |
| `notebooks/` | Experiments and offline evaluation | No |
| `src/vue_rag/` | Application source code | Included in the image |

Do not copy unused source or CSV trees to a deployment host. A host only needs
the repository files required to build the image, the selected database
artifact, local configuration, and secrets.

## API

The backend exposes:

- `GET /health` — public readiness check;
- `GET /v1/profiles`;
- `POST /v1/messages`;
- `PUT /v1/users/{telegram_user_id}/profile`;
- `POST /v1/feedback`.

All `/v1/*` routes require:

```http
Authorization: Bearer <MVP_V2_BACKEND_TOKEN>
```

Compose reads the token from `secrets/gateway_token`. The Telegram service uses
the same token when calling the backend. The database is opened read-only, and
containers do not mount a home directory, `.venv`, container socket, dumps, or
source data.

## Read-only database inspection

Open an existing canonical LadybugDB artifact with Ladybug Explorer:

```console
make explorer
```

Select a known artifact directly or change the host port:

```console
make explorer EXPLORER_DATABASE=onboarding-faq-rag-metadata.lbdb
make explorer EXPLORER_PORT=8001
```

Explorer binds to `127.0.0.1`, mounts the database read-only, and uses
`MODE=READ_ONLY`.

## Local Python commands

Create or update the external uv environment from `mvp_v2/`:

```console
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv sync --frozen
```

The paths in `.env.example` are relative to the repository root. Run the
following commands from the repository root.

Backend tests:

```console
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  python -m pytest mvp_v2/tests/backend
```

Backend and Telegram can run without containers when local Redis is available
and `MVP_V2_BACKEND_TOKEN` plus `TELEGRAM_BOT_TOKEN` are set:

```console
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  python -m vue_rag.backend

UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  python -m vue_rag.telegram_bot
```

## Optional Streamlit workspace

Streamlit is an operator interface for ingestion, snapshot publication, database
builds, retrieval, and local chat. Unlike the deployed gateway, it can use
`runtime/mvp_data/` and `runtime/dumps/`.

From the repository root:

```console
mkdir -p mvp_v2/runtime/{mvp_data,dumps,databases,ladybug-home}

UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 --env-file mvp_v2/.env \
  streamlit run mvp_v2/streamlit_app.py
```

Model calls require explicit authorization in the UI. The application does not
install Ladybug extensions or select/build a database automatically.

If vector provisioning is needed, run it once with the same Ladybug home:

```console
UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv \
  uv run --project mvp_v2 python -m vue_rag.bootstrap.provision_vector \
  --home mvp_v2/runtime/ladybug-home --yes
```

## Project documentation

- Local setup: [`setup-project.md`](setup-project.md)
- Canonical graph schema: [`docs/schema.md`](docs/schema.md)
- Optional graph fields: [`docs/graph-fields.md`](docs/graph-fields.md)
- Package ownership map: [`docs/module-map.md`](docs/module-map.md)
- OpenSpec workflow: [`docs/openspec-token-efficient-workflow.md`](docs/openspec-token-efficient-workflow.md)

When runtime contracts or DTOs change, restart long-lived Python processes and
notebooks. Validate their construction path in a fresh interpreter before reuse.
