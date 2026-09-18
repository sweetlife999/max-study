# One image, both entrypoints (ARCHITECTURE.md §2): `python -m campus.api` and
# `python -m campus.bot` differ only in the command, never in what is installed.
#
# Build context is the repository root, so that config/ can be copied in as well:
#     docker build -f backend/Dockerfile .

# --- builder: resolve and install dependencies against a locked set ---------------------------
FROM ghcr.io/astral-sh/uv:0.9.9-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies change far less often than the source; installing them first keeps this layer
# cached across ordinary code edits. --no-install-project: the project itself comes next.
# No BuildKit cache mounts: the image must also build with the classic builder.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN uv sync --locked --no-dev --no-editable --no-install-project

COPY backend/src ./src
COPY backend/README.md ./README.md
# --no-editable: the runtime stage copies only .venv, so campus must live inside site-packages
# rather than behind a .pth pointing at a /app/src that is not there.
RUN uv sync --locked --no-dev --no-editable

# --- runtime: no uv, no build tools, no root ---------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# A fixed uid/gid keeps a bind-mounted volume's ownership predictable across hosts.
RUN groupadd --system --gid 10001 campus \
    && useradd --system --uid 10001 --gid campus --home-dir /app --no-create-home campus

WORKDIR /app

COPY --from=builder --chown=root:root /app/.venv /app/.venv
COPY --chown=root:root backend/alembic.ini ./alembic.ini
COPY --chown=root:root backend/alembic ./alembic
# The Russian Trusted Root CA that signs platform-api2.max.ru; campus.max.http adds it to the
# system trust store rather than replacing it (§5 keeps verification on).
COPY --chown=root:root backend/certs ./certs
# The shipped example university config, so the image runs without a mounted file.
COPY --chown=root:root config ./config

ENV UNIVERSITY_CONFIG_PATH=/app/config/university.example.yaml

USER campus

# Overridden by compose for the api, the bot, migrations and the seed.
CMD ["python", "-m", "campus.bot"]
