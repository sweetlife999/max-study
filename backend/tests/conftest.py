"""Fixtures shared by unit and integration tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from campus.config import UniversityConfig, load_university_config
from campus.domain.clock import FixedClock

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "university.example.yaml"


@pytest.fixture(scope="session")
def example_config_path() -> Path:
    return EXAMPLE_CONFIG_PATH


@pytest.fixture(scope="session")
def university_config() -> UniversityConfig:
    """The shipped example config; every test speaks the same vocabulary of kinds and steps."""
    return load_university_config(EXAMPLE_CONFIG_PATH)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
