"""Validated request bodies and HTTP envelopes. Domain views are response schemas."""

from typing import Annotated, Literal, Self

from fastapi import Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from campus.domain.errors import API_ERROR_CODES
from campus.domain.views import EventView

EventId = Annotated[int, Path(gt=0, le=2**63 - 1)]

PositiveId = Annotated[int, Field(gt=0, le=2**63 - 1, strict=True)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
Description = Annotated[str, Field(max_length=4000)]
Location = Annotated[str, Field(max_length=200)]
Points = Annotated[int, Field(ge=0, le=2**31 - 1, strict=True)]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateMe(RequestBody):
    lang: Literal["ru", "en"]


class CheckinRequest(RequestBody):
    event_id: PositiveId
    code: Annotated[str, Field(max_length=64)]
    method: Literal["qr", "code"]


class CreateEvent(RequestBody):
    title: Title
    description: Description = ""
    kind: Annotated[str, Field(min_length=1, max_length=64)]
    location: Location = ""
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    points: Points | None = None
    onboarding_step: Annotated[str, Field(max_length=64)] | None = None


class UpdateEvent(RequestBody):
    title: Title | None = None
    description: Description | None = None
    kind: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    location: Location | None = None
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    points: Points | None = None
    onboarding_step: Annotated[str, Field(max_length=64)] | None = None
    checkin_open: Annotated[bool, Field(strict=True)] | None = None

    @model_validator(mode="after")
    def reject_null_fields(self) -> Self:
        for field in self.model_fields_set - {"onboarding_step"}:
            if getattr(self, field) is None:
                raise ValueError("only onboarding_step may be null")
        return self


class EventList(BaseModel):
    items: list[EventView]


ERROR_ENUM: dict[str, JsonValue] = {"enum": list[JsonValue](sorted(API_ERROR_CODES))}


class ErrorDetail(BaseModel):
    code: str = Field(json_schema_extra=ERROR_ENUM)
    message: str


class ErrorBody(BaseModel):
    error: ErrorDetail


class Health(BaseModel):
    status: Literal["ok", "unavailable"]
    db: Literal["ok", "unavailable"]
