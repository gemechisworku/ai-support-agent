# One image for every Python service (bank API, gateway, scheduler, console).
# The service is chosen by the compose `command`. Model servers (whisper.cpp, Ollama, Kokoro) and the
# agent worker run natively on macOS for Metal GPU access — see docs/architecture.md §6.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PYTHONUNBUFFERED=1
WORKDIR /app

FROM base AS runtime
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --create-home app
USER app
CMD ["python", "-c", "import supportagent; print('supportagent', supportagent.__version__)"]

# Test image: `make docker-test` runs the unit + integration suite inside Docker against compose Postgres.
FROM base AS test
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
ENV PATH="/app/.venv/bin:$PATH"
CMD ["pytest", "-m", "not eval and not live"]
