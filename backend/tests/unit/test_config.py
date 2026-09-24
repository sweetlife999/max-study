from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from campus.config import (
    ConfigError,
    DatabaseSettings,
    EventKindStep,
    ManualStep,
    Settings,
    UniversityConfig,
    UniversityConfigError,
    load_university_config,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = REPO_ROOT / "config" / "university.example.yaml"


def example_data() -> dict[str, Any]:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def write(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "university.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_example_config_is_valid() -> None:
    config = load_university_config(EXAMPLE)
    assert config.languages == ["ru", "en"]
    assert config.university.timezone == "Europe/Moscow"
    assert 4 <= len(config.onboarding_steps) <= 6
    assert 4 <= len(config.event_kinds) <= 5
    assert config.reminders_before == [timedelta(hours=24), timedelta(hours=1)]
    assert {type(step) for step in config.onboarding_steps} == {ManualStep, EventKindStep}


def test_lookup_helpers() -> None:
    config = load_university_config(EXAMPLE)
    assert config.event_kind("council") is not None
    assert config.event_kind("nope") is None
    step = config.step("meet_curator")
    assert isinstance(step, EventKindStep)
    assert step.event_kind == "curator_meeting"
    assert config.step("nope") is None
    assert config.text(config.university.name, "en") == "University N"
    assert config.default_language == "ru"
    assert config.tz.key == "Europe/Moscow"


def test_text_falls_back_to_default_language() -> None:
    data = example_data()
    data["languages"] = ["ru"]
    for kind in data["event_kinds"]:
        kind["title"] = {"ru": kind["title"]["ru"]}
    for step in data["onboarding_steps"]:
        step["title"] = {"ru": step["title"]["ru"]}
        step["description"] = {"ru": step["description"]["ru"]}
    for reward in data["points_rewards"]:
        reward["title"] = {"ru": reward["title"]["ru"]}
    data["university"]["name"] = {"ru": "Университет N"}
    config = UniversityConfig.model_validate(data)
    assert config.text(config.university.name, "en") == "Университет N"


def mutate(change: Any) -> dict[str, Any]:
    data = example_data()
    change(data)
    return data


INVALID_CASES: dict[str, Any] = {
    "missing_translation": lambda d: d["event_kinds"][0]["title"].pop("en"),
    "empty_translation": lambda d: d["event_kinds"][0]["title"].update(en="  "),
    "unsupported_language": lambda d: d.update(languages=["ru", "de"]),
    "duplicate_language": lambda d: d.update(languages=["ru", "ru"]),
    "no_languages": lambda d: d.update(languages=[]),
    "unknown_kind_in_step": lambda d: d["onboarding_steps"][1].update(event_kind="nope"),
    "duplicate_kind": lambda d: d["event_kinds"].append(dict(d["event_kinds"][0])),
    "duplicate_step": lambda d: d["onboarding_steps"].append(dict(d["onboarding_steps"][0])),
    "manual_with_event_kind": lambda d: d["onboarding_steps"][0].update(event_kind="club"),
    "unknown_step_type": lambda d: d["onboarding_steps"][0].update(type="quiz"),
    "bad_key": lambda d: d["event_kinds"][0].update(key="Bad Key"),
    "negative_points": lambda d: d["event_kinds"][0].update(default_points=-1),
    "bad_timezone": lambda d: d["university"].update(timezone="Mars/Olympus"),
    "unknown_top_level": lambda d: d.update(extra=1),
    "reminder_not_iso": lambda d: d.update(reminders_before=["24h"]),
    "reminder_seconds_int": lambda d: d.update(reminders_before=[3600]),
    "reminder_zero": lambda d: d.update(reminders_before=["PT0S"]),
    "reminder_duplicate": lambda d: d.update(reminders_before=["PT1H", "PT60M"]),
    "negative_threshold": lambda d: d["points_rewards"][0].update(threshold=-5),
    "no_event_kinds": lambda d: d.update(event_kinds=[]),
}


@pytest.mark.parametrize("case", sorted(INVALID_CASES))
def test_invalid_configs_are_rejected(case: str) -> None:
    with pytest.raises(ValidationError):
        UniversityConfig.model_validate(mutate(INVALID_CASES[case]))


def test_load_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UniversityConfigError, match="not found"):
        load_university_config(tmp_path / "missing.yaml")


def test_load_reports_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("university: [unclosed", encoding="utf-8")
    with pytest.raises(UniversityConfigError, match="YAML"):
        load_university_config(path)


def test_load_reports_non_mapping(tmp_path: Path) -> None:
    with pytest.raises(UniversityConfigError, match="mapping"):
        load_university_config(write(tmp_path, ["a", "b"]))


def test_load_wraps_validation_errors(tmp_path: Path) -> None:
    data = mutate(INVALID_CASES["unknown_kind_in_step"])
    with pytest.raises(UniversityConfigError, match="event_kind"):
        load_university_config(write(tmp_path, data))


def test_settings_defaults_and_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/campus")
    monkeypatch.setenv("UNIVERSITY_CONFIG_PATH", str(EXAMPLE))
    monkeypatch.setenv("ADMIN_MAX_USER_IDS", " 101, 202 ,303,")
    monkeypatch.setenv("MAX_BOT_TOKEN", "secret-token")
    settings = Settings.from_env()
    assert settings.database_url == "postgresql+asyncpg://u:p@db:5432/campus"
    assert settings.admin_max_user_ids == frozenset({101, 202, 303})
    assert settings.max_api_base_url == "https://platform-api2.max.ru"
    assert settings.checkin_code_step_seconds == 10
    # §10: the MAX recommendation is about an hour, not a day.
    assert settings.init_data_ttl_seconds == 3600
    assert settings.log_level == "INFO"
    assert settings.require_bot_token() == "secret-token"
    assert "secret-token" not in repr(settings)


def test_settings_empty_admin_list_and_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db/campus")
    monkeypatch.setenv("UNIVERSITY_CONFIG_PATH", str(EXAMPLE))
    monkeypatch.setenv("ADMIN_MAX_USER_IDS", "")
    monkeypatch.delenv("MAX_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MAX_BOT_USERNAME", raising=False)
    settings = Settings.from_env()
    assert settings.admin_max_user_ids == frozenset()
    with pytest.raises(ConfigError, match="MAX_BOT_TOKEN"):
        settings.require_bot_token()
    # Without it no deep link can be built (§5), so the QR worker would fail on every tick
    # forever and the menu would ship a button with nowhere to go.
    with pytest.raises(ConfigError, match="MAX_BOT_USERNAME"):
        settings.require_bot_username()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ADMIN_MAX_USER_IDS", "12,abc"),
        ("ADMIN_MAX_USER_IDS", "-5"),
        ("CHECKIN_CODE_STEP_SECONDS", "0"),
        ("INIT_DATA_TTL_SECONDS", "0"),
        ("LOG_LEVEL", "LOUD"),
        ("DATABASE_URL", "mysql://u:p@db/campus"),
    ],
)
def test_settings_reject_invalid_values(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db/campus")
    monkeypatch.setenv("UNIVERSITY_CONFIG_PATH", str(EXAMPLE))
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings.from_env()


def test_database_settings_only_need_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UNIVERSITY_CONFIG_PATH", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db/campus")
    assert DatabaseSettings.from_env().database_url == "postgresql+asyncpg://u:p@db/campus"
