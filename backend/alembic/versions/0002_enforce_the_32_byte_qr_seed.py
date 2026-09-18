"""enforce the 32-byte qr_seed

ARCHITECTURE.md §4 says ``qr_seed (bytea 32)``. Nothing in the schema said so, and a short seed
would weaken every code of §5 without a single test failing.

Written by hand: Alembic's autogenerate does not compare CHECK constraints, so ``alembic check``
stays clean whether or not this file exists. What actually enforces it is
``tests/integration/test_schema.py``, which runs these migrations against a real PostgreSQL.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-18 21:00:17.926772
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_events_qr_seed_length"
QR_SEED_BYTES = 32


def upgrade() -> None:
    op.create_check_constraint(
        op.f(CONSTRAINT_NAME),
        "events",
        f"octet_length(qr_seed) = {QR_SEED_BYTES}",
    )


def downgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT_NAME), "events", type_="check")
