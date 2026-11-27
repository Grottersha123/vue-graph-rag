# Rules
    - git tag every new day when I am working with dir
    - Write python code by SOLID
    - Ask me before do things
    - After changing Pydantic contracts or other runtime types used by notebooks/long-lived processes, test the real runtime construction path in a fresh interpreter and make notebook reload order explicit; static compilation alone is not sufficient
    - Before committing notebook changes, verify imported DTO/service types are not stale and validate newly added fields through the same construction path used by the notebook
    - we are working with [mvp_v2] it is a main project in repository

# OpenSpec workflow
    - For OpenSpec work in `mvp_v2`, use [`mvp_v2/docs/openspec-token-efficient-workflow.md`](mvp_v2/docs/openspec-token-efficient-workflow.md) as the operating guide. Read only the section needed for the current planning, apply, validation, or archive phase instead of loading the full project history.
    - Create and switch to a dedicated git branch before creating change artifacts or modifying code.
    - When implementation is complete, ask whether to commit the scoped changes and create a pull request. If approved, use a short commit message and a concise pull request title and description.
    - Read [`mvp_v2/docs/schema.md`](mvp_v2/docs/schema.md) before changing graph, storage, dump, database, or retrieval contracts.

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

# Creating the external mvp_v2 environment
    - The external environment lives at `/tmp/vue-graph-rag-mvp-v2/.venv`. It is not host-owned and the root agent creates it when it is missing.
    - Its absence is not a blocker: create it, then continue the task.
    - Create or repair it only with this command, run from `mvp_v2`:
      `UV_PROJECT_ENVIRONMENT=/tmp/vue-graph-rag-mvp-v2/.venv uv sync --frozen`
    - Keep `--frozen`. It installs from the existing `uv.lock` and does not modify `uv.lock` or `pyproject.toml`.
    - Resulting interpreter: `/tmp/vue-graph-rag-mvp-v2/.venv/bin/python`. Verify `sys.executable` matches before running project commands.
    - Do not run `uv add` or install project libraries ad hoc. If a library is missing, report it and ask me to add the dependency to `pyproject.toml`.
    - Recreating the environment is the only supported repair. Report the failing package and the full error instead of patching installed files.
