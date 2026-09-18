"""SQLAlchemy models, one per table of ARCHITECTURE.md §4.

All timestamps are ``timestamptz`` in UTC; the university time zone (§6) is display-only.
Small closed vocabularies (``lang``, ``method``, ``status``) are text columns guarded by CHECK
constraints rather than PostgreSQL ENUMs, so that adding a value stays an ordinary migration.
"""

from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stable, explicit constraint names: Alembic autogenerate and `alembic check` need them to be
# reproducible, otherwise every run proposes renaming unnamed constraints.
NAMING_CONVENTION: Final[dict[str, str]] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

CheckinMethod = Literal["qr", "code"]
CHECKIN_METHODS: Final[tuple[CheckinMethod, ...]] = ("qr", "code")

OutboxStatus = Literal["pending", "sent", "failed", "cancelled"]
OUTBOX_STATUSES: Final[tuple[OutboxStatus, ...]] = ("pending", "sent", "failed", "cancelled")

OutboxKind = Literal[
    "reminder", "checkin_confirmed", "step_completed", "invite_accepted", "qr_display_start"
]
OUTBOX_KINDS: Final[tuple[OutboxKind, ...]] = (
    "reminder",
    "checkin_confirmed",
    "step_completed",
    "invite_accepted",
    "qr_display_start",
)

# Advisory lock the single bot replica takes at start-up (ARCHITECTURE.md §2).
BOT_ADVISORY_LOCK_ID: Final[int] = 0x43414D50  # "CAMP"

QR_SEED_BYTES: Final[int] = 32
_LANGS: Final[str] = "'ru', 'en'"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{value}'" for value in values)
    return f"{column} in ({joined})"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012 — SQLAlchemy reads this as a plain class attribute
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        bytes: LargeBinary,
    }


def _now() -> Any:
    return text("now()")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    max_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    first_name: Mapped[str] = mapped_column(String(256))
    lang: Mapped[str] = mapped_column(String(8))
    consent_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=_now())

    __table_args__ = (CheckConstraint(f"lang in ({_LANGS})", name="lang"),)


class Organizer(Base):
    __tablename__ = "organizers"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    invited_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=_now())


class OrganizerInvite(Base):
    __tablename__ = "organizer_invites"

    token: Mapped[str] = mapped_column(Text, primary_key=True)
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    expires_at: Mapped[datetime] = mapped_column()
    used_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    used_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=_now())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(64))
    location: Mapped[str] = mapped_column(String(200), default="")
    starts_at: Mapped[datetime] = mapped_column()
    ends_at: Mapped[datetime] = mapped_column()
    points: Mapped[int] = mapped_column(Integer, default=0)
    onboarding_step: Mapped[str | None] = mapped_column(Text, default=None)
    organizer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    # Never leaves the backend: not in API responses, not in logs (ARCHITECTURE.md §5).
    qr_seed: Mapped[bytes] = mapped_column(LargeBinary)
    checkin_open: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=_now())

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="time_range"),
        CheckConstraint("points >= 0", name="points_non_negative"),
        # §4: "qr_seed (bytea 32)". A shorter seed would weaken every code of §5 without a
        # single test failing, so the database refuses it too.
        CheckConstraint(f"octet_length(qr_seed) = {QR_SEED_BYTES}", name="qr_seed_length"),
        Index("ix_events_starts_at", "starts_at"),
        Index("ix_events_organizer_id_starts_at", "organizer_id", "starts_at"),
    )


class Rsvp(Base):
    __tablename__ = "rsvps"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(server_default=_now())


class Checkin(Base):
    __tablename__ = "checkins"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    event_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    method: Mapped[str] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(server_default=_now())

    __table_args__ = (CheckConstraint(_in_list("method", CHECKIN_METHODS), name="method"),)


class ManualStepCompletion(Base):
    __tablename__ = "manual_step_completions"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    step_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=_now())


class CheckinAttempt(Base):
    """Rate-limit ledger (§5): at most 10 attempts per 10 minutes per user."""

    __tablename__ = "checkin_attempts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(server_default=_now())
    success: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_checkin_attempts_user_id_created_at", "user_id", "created_at"),)


class OutboxMessage(Base):
    """Everything the api wants to send goes here; only the bot talks to MAX (§2)."""

    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    run_at: Mapped[datetime] = mapped_column()
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    dedup_key: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=_now())

    __table_args__ = (
        CheckConstraint(_in_list("status", OUTBOX_STATUSES), name="status"),
        CheckConstraint(_in_list("kind", OUTBOX_KINDS), name="kind"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_outbox_status_run_at", "status", "run_at"),
        Index(
            "uq_outbox_dedup_key",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key is not null"),
        ),
    )


class QrDisplay(Base):
    """A rotating QR image the bot keeps editing in an organizer's chat (§8)."""

    __tablename__ = "qr_displays"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("events.id", ondelete="CASCADE"))
    organizer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    max_chat_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    max_user_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    message_id: Mapped[str | None] = mapped_column(Text, default=None)
    active_until: Mapped[datetime] = mapped_column()
    last_rendered_window: Mapped[int | None] = mapped_column(BigInteger, default=None)
    stopped_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=_now())

    __table_args__ = (
        CheckConstraint(
            "max_chat_id is not null or max_user_id is not null", name="destination_present"
        ),
        # "Одна активная qr_display на организатора+событие" (§8).
        Index(
            "uq_qr_displays_organizer_id_event_id_active",
            "organizer_id",
            "event_id",
            unique=True,
            postgresql_where=text("stopped_at is null"),
        ),
        Index("ix_qr_displays_active_until", "active_until"),
    )


class KeyValue(Base):
    """Small process state; holds the ``updates_marker`` of the bot's long polling (§4)."""

    __tablename__ = "kv"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
