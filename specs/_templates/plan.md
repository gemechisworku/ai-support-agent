# NN · Module name — Plan

> Implements [spec.md](spec.md). Tasks in [tasks.md](tasks.md).

## Approach
The design in a few paragraphs. Reference [architecture.md](../../docs/architecture.md) sections
rather than restating them.

## File layout
```
src/supportagent/<pkg>/
  __init__.py
  …
tests/<pkg>/
  …
```

## Data model / contracts
Tables, Pydantic models, tool schemas and message shapes this module owns.

## Key decisions
| Decision | Choice | Why |
|---|---|---|

## Dependencies
New runtime packages (added with `uv add`) and services (added to `docker-compose.yml`).

## Test strategy
- **unit:** … (no network; fakes for …)
- **integration:** … (Postgres from `docker compose up -d postgres`)
- **eval / live:** … (only if applicable)

## Risks
