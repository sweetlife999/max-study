# campus backend

Python 3.12 package `campus` (see `docs/ARCHITECTURE.md` at the repository root).

```bash
uv sync                      # install runtime + dev dependencies
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest                # starts Postgres via testcontainers unless TEST_DATABASE_URL is set
uv run alembic upgrade head  # needs DATABASE_URL
uv run python -m campus.seed # needs DATABASE_URL and UNIVERSITY_CONFIG_PATH
```
