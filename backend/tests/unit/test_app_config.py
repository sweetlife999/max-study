"""GET /api/config (ARCHITECTURE.md §7): the only source of kinds and steps for the mini-app.

The shape is pinned against ``AppConfig`` in ``web/src/api/types.ts``; the forms of the organizer
screens are built from it and §7 forbids hard-coding the vocabulary on the front end.
"""

import pytest

from campus.config import UniversityConfig
from campus.domain.context import DomainConfig
from campus.domain.services.app_config import AppConfigService
from campus.domain.views import AppConfigView


@pytest.fixture
def service(university_config: UniversityConfig) -> AppConfigService:
    return AppConfigService(DomainConfig(university=university_config))


def test_the_view_carries_every_configured_event_kind(
    service: AppConfigService, university_config: UniversityConfig
) -> None:
    view = service.view(lang="ru")

    assert [kind.key for kind in view.event_kinds] == [
        kind.key for kind in university_config.event_kinds
    ]
    assert [kind.default_points for kind in view.event_kinds] == [
        kind.default_points for kind in university_config.event_kinds
    ]


def test_the_view_carries_every_onboarding_step_in_configured_order(
    service: AppConfigService, university_config: UniversityConfig
) -> None:
    view = service.view(lang="ru")

    assert [step.key for step in view.onboarding_steps] == [
        step.key for step in university_config.onboarding_steps
    ]


def test_an_event_kind_step_names_its_kind_and_a_manual_step_does_not(
    service: AppConfigService,
) -> None:
    view = service.view(lang="ru")

    by_type = {step.type for step in view.onboarding_steps}
    assert by_type <= {"event_kind", "manual"}
    for step in view.onboarding_steps:
        assert (step.event_kind is None) == (step.type == "manual")


def test_every_named_event_kind_of_a_step_exists(service: AppConfigService) -> None:
    keys = {kind.key for kind in service.view(lang="ru").event_kinds}

    for step in service.view(lang="ru").onboarding_steps:
        assert step.event_kind is None or step.event_kind in keys


def test_titles_follow_the_requested_language(service: AppConfigService) -> None:
    russian = service.view(lang="ru")
    english = service.view(lang="en")

    assert [kind.title for kind in russian.event_kinds] != [
        kind.title for kind in english.event_kinds
    ]
    assert russian.university.name != english.university.name


def test_an_unsupported_language_falls_back_to_the_default(service: AppConfigService) -> None:
    assert service.view(lang="de") == service.view(lang=None)


def test_the_view_reports_the_languages_and_the_university(
    service: AppConfigService, university_config: UniversityConfig
) -> None:
    view = service.view(lang="ru")

    assert list(view.languages) == list(university_config.languages)
    assert view.university.timezone == university_config.university.timezone


def test_the_view_is_immutable(service: AppConfigService) -> None:
    view = service.view(lang="ru")

    assert isinstance(view, AppConfigView)
    with pytest.raises(AttributeError):
        view.languages = ()  # pyright: ignore[reportAttributeAccessIssue]


def test_the_view_never_leaks_anything_outside_the_contract(service: AppConfigService) -> None:
    """§7 lists four keys; nothing about reminders, rewards or secrets belongs in the answer."""
    assert set(AppConfigView.__dataclass_fields__) == {
        "event_kinds",
        "onboarding_steps",
        "languages",
        "university",
    }
