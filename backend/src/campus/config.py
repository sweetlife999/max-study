"""Process configuration: environment (ARCHITECTURE.md §10) and the university YAML (§6).

Both are validated at start-up; an invalid value stops the process instead of being guessed.
"""

import re
from datetime import timedelta
from functools import cached_property
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEFAULT_MAX_API_BASE_URL = "https://platform-api2.max.ru"
Language = Literal["ru", "en"]
SUPPORTED_LANGUAGES: tuple[Language, ...] = ("ru", "en")


class ConfigError(Exception):
    """Configuration is missing or invalid."""


class UniversityConfigError(ConfigError):
    """The university YAML cannot be loaded."""


# --- environment ---------------------------------------------------------------------------------

_ASYNC_DRIVER = "postgresql+asyncpg://"
_PG_SCHEMES = ("postgresql+asyncpg://", "postgresql://", "postgres://")


def _normalize_database_url(value: str) -> str:
    for scheme in _PG_SCHEMES:
        if value.startswith(scheme):
            return _ASYNC_DRIVER + value.removeprefix(scheme)
    msg = "DATABASE_URL must be a PostgreSQL URL (postgresql://...)"
    raise ValueError(msg)


class DatabaseSettings(BaseSettings):
    """The subset needed by migrations: only DATABASE_URL."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_url: str

    @classmethod
    def from_env(cls) -> Self:
        """Read and validate the process environment (fields are filled by pydantic-settings)."""
        return cls()  # pyright: ignore[reportCallIssue]

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        return _normalize_database_url(value.strip())


def _parse_id_list(value: object) -> object:
    if not isinstance(value, str):
        return value
    ids: set[int] = set()
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        if not item.isascii() or not item.isdigit():
            msg = "ADMIN_MAX_USER_IDS must be comma-separated positive integers"
            raise ValueError(msg)
        ids.add(int(item))
    return frozenset(ids)


def _empty_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(DatabaseSettings):
    max_bot_token: Annotated[SecretStr | None, BeforeValidator(_empty_to_none)] = None
    max_bot_username: Annotated[str | None, BeforeValidator(_empty_to_none)] = None
    max_api_base_url: str = DEFAULT_MAX_API_BASE_URL
    university_config_path: Path
    admin_max_user_ids: Annotated[
        frozenset[Annotated[int, Field(gt=0)]], NoDecode, BeforeValidator(_parse_id_list)
    ] = frozenset()
    public_web_url: Annotated[str | None, BeforeValidator(_empty_to_none)] = None
    checkin_code_step_seconds: int = Field(default=10, gt=0, le=3600)
    # §10: MAX recommends about an hour. A day of replay window buys the mini-app nothing
    # and widens the time a leaked initData stays usable.
    init_data_ttl_seconds: int = Field(default=3600, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("max_api_base_url", "public_web_url")
    @classmethod
    def _http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"https?://[^\s/]+(/\S*)?", value):
            msg = "must be an absolute http(s) URL"
            raise ValueError(msg)
        return value.rstrip("/")

    def require_bot_token(self) -> str:
        if self.max_bot_token is None:
            msg = "MAX_BOT_TOKEN is required for this process"
            raise ConfigError(msg)
        return self.max_bot_token.get_secret_value()

    def require_bot_username(self) -> str:
        """Fail at start-up rather than on every QR render.

        Deep links are built from the bot's name (§5), so without it the QR worker raises on
        each tick forever and the menu ships a button with nowhere to go. §6 wants a bad
        configuration to stop the process.
        """
        if self.max_bot_username is None:
            msg = "MAX_BOT_USERNAME is required for this process"
            raise ConfigError(msg)
        return self.max_bot_username


# --- university YAML ---------------------------------------------------------------------------

_KEY_RE = r"^[a-z][a-z0-9_]{0,63}$"
Key = Annotated[str, Field(pattern=_KEY_RE)]
NonEmptyText = Annotated[str, Field(min_length=1, max_length=2000)]
LocalizedText = dict[Language, NonEmptyText]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class University(_Strict):
    name: LocalizedText
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"unknown IANA time zone {value!r}"
            raise ValueError(msg) from exc
        return value


class EventKind(_Strict):
    key: Key
    title: LocalizedText
    default_points: int = Field(ge=0)


class _Step(_Strict):
    key: Key
    title: LocalizedText
    description: LocalizedText


class EventKindStep(_Step):
    type: Literal["event_kind"]
    event_kind: Key


class ManualStep(_Step):
    type: Literal["manual"]


OnboardingStep = Annotated[EventKindStep | ManualStep, Field(discriminator="type")]


class PointsReward(_Strict):
    threshold: int = Field(ge=0)
    title: LocalizedText


def _iso_duration(value: object) -> object:
    # Only ISO 8601 durations ("PT24H") are accepted; pydantic would also take ints or "24:00".
    if not isinstance(value, str) or not value.startswith("P"):
        msg = "reminders_before items must be ISO 8601 durations such as PT24H"
        raise ValueError(msg)
    return value


ReminderOffset = Annotated[timedelta, BeforeValidator(_iso_duration)]


def _unique(values: list[str], what: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            msg = f"duplicate {what} {value!r}"
            raise ValueError(msg)
        seen.add(value)


class UniversityConfig(_Strict):
    university: University
    languages: list[Language] = Field(min_length=1)
    event_kinds: list[EventKind] = Field(min_length=1)
    onboarding_steps: list[OnboardingStep] = Field(default_factory=list[OnboardingStep])
    points_rewards: list[PointsReward] = Field(default_factory=list[PointsReward])
    reminders_before: list[ReminderOffset] = Field(default_factory=list[timedelta])

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        _unique(list(self.languages), "language")
        _unique([kind.key for kind in self.event_kinds], "event kind")
        _unique([step.key for step in self.onboarding_steps], "onboarding step")
        _unique([str(reward.threshold) for reward in self.points_rewards], "reward threshold")
        kinds = {kind.key for kind in self.event_kinds}
        for step in self.onboarding_steps:
            if isinstance(step, EventKindStep) and step.event_kind not in kinds:
                msg = f"onboarding step {step.key!r}: unknown event_kind {step.event_kind!r}"
                raise ValueError(msg)
        seen_offsets: set[timedelta] = set()
        for offset in self.reminders_before:
            if offset <= timedelta(0):
                msg = "reminders_before items must be positive"
                raise ValueError(msg)
            if offset in seen_offsets:
                msg = f"duplicate reminder offset {offset}"
                raise ValueError(msg)
            seen_offsets.add(offset)
        for text in self._all_texts():
            missing = [lang for lang in self.languages if not text.get(lang, "").strip()]
            if missing:
                msg = f"localized text {text!r} lacks languages {missing}"
                raise ValueError(msg)
        return self

    def _all_texts(self) -> list[dict[Language, str]]:
        texts: list[dict[Language, str]] = [self.university.name]
        texts.extend(kind.title for kind in self.event_kinds)
        for step in self.onboarding_steps:
            texts.extend((step.title, step.description))
        texts.extend(reward.title for reward in self.points_rewards)
        return texts

    @property
    def default_language(self) -> Language:
        return self.languages[0]

    @cached_property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.university.timezone)

    def event_kind(self, key: str) -> EventKind | None:
        return next((kind for kind in self.event_kinds if kind.key == key), None)

    def step(self, key: str) -> EventKindStep | ManualStep | None:
        return next((step for step in self.onboarding_steps if step.key == key), None)

    def text(self, text: dict[Language, str], lang: str) -> str:
        """Text in ``lang``, falling back to the default language."""
        for candidate in (lang, self.default_language):
            if candidate in text:
                return text[candidate]  # type: ignore[index]
        return next(iter(text.values()))


def load_university_config(path: Path) -> UniversityConfig:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        msg = f"university config not found: {path}"
        raise UniversityConfigError(msg) from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        msg = f"university config is not valid YAML: {exc}"
        raise UniversityConfigError(msg) from exc
    if not isinstance(data, dict):
        msg = "university config must be a YAML mapping"
        raise UniversityConfigError(msg)
    try:
        return UniversityConfig.model_validate(data)
    except ValidationError as exc:
        msg = f"university config {path} is invalid:\n{exc}"
        raise UniversityConfigError(msg) from exc
