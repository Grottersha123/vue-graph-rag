# Rules
    - git tag every new day when I am working with dir
    - Write python code by SOLID
    - Ask me before do things
    - After changing Pydantic contracts or other runtime types used by notebooks/long-lived processes, test the real runtime construction path in a fresh interpreter and make notebook reload order explicit; static compilation alone is not sufficient
    - Before committing notebook changes, verify imported DTO/service types are not stale and validate newly added fields through the same construction path used by the notebook

# You cannot
    - Change ./vue-graph-rag/.venv it is denied, ask me to add library
    - Change `mvp_v2/.venv`. It is a host-owned environment and is immutable for every agent and subagent.
    - Create, remove, repair, synchronize, chmod, or execute anything from `mvp_v2/.venv`.
    - Fall back to `mvp_v2/.venv` when the external environment is absent or broken. Stop and ask me instead.

# Protected mvp_v2 Python environment
    - Run every mvp_v2 Python command with `UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv`.
    - Never run bare `uv`, `uv run`, `uv sync`, `pytest`, or other Python project commands in `mvp_v2`.
    - Before the first project command, verify in a fresh interpreter that `sys.executable` starts with `/tmp/vue-graph-rag-mvp-v2/.venv/`.
    - Subagents may perform static review only. They must not run project commands. The root agent runs all checks in the external environment.
