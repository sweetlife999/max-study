"""The university's vocabulary as the mini-app reads it (ARCHITECTURE.md §7, GET /api/config).

This is the single source of activity kinds and onboarding steps for the organizer forms: §7
forbids hard-coding them on the front end, so anything a form offers has to come from here.

Unlike every other service this one takes no session and no clock: the answer is the YAML of §6
translated into the caller's language, and nothing about it is in the database. Handing it a
session would only suggest otherwise.
"""

from dataclasses import dataclass

from campus.config import EventKindStep
from campus.domain.context import DomainConfig
from campus.domain.views import (
    AppConfigView,
    ConfigEventKindView,
    ConfigStepView,
    UniversityView,
)


@dataclass(frozen=True, slots=True)
class AppConfigService:
    config: DomainConfig

    def view(self, *, lang: str | None = None) -> AppConfigView:
        university = self.config.university
        language = self.config.language_or_default(lang)
        return AppConfigView(
            event_kinds=tuple(
                ConfigEventKindView(
                    key=kind.key,
                    title=university.text(kind.title, language),
                    default_points=kind.default_points,
                )
                for kind in university.event_kinds
            ),
            onboarding_steps=tuple(
                ConfigStepView(
                    key=step.key,
                    type=step.type,
                    title=university.text(step.title, language),
                    event_kind=step.event_kind if isinstance(step, EventKindStep) else None,
                )
                for step in university.onboarding_steps
            ),
            languages=tuple(university.languages),
            university=UniversityView(
                name=university.text(university.university.name, language),
                timezone=university.university.timezone,
            ),
        )
