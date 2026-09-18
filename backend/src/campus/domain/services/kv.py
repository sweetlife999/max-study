"""The small key/value table (ARCHITECTURE.md §4): today it holds the long-polling marker."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert

from campus.db.models import KeyValue
from campus.domain.services.base import Service

UPDATES_MARKER_KEY = "updates_marker"
_MARKER_FIELD = "marker"


@dataclass(frozen=True, slots=True)
class KeyValueService(Service):
    async def get(self, key: str) -> dict[str, Any] | None:
        row = await self.session.get(KeyValue, key)
        return dict(row.value) if row is not None else None

    async def set(self, key: str, value: Mapping[str, Any]) -> None:
        await self.session.execute(
            pg_insert(KeyValue)
            .values(key=key, value=dict(value))
            .on_conflict_do_update(index_elements=[KeyValue.key], set_={"value": dict(value)})
        )

    async def delete(self, key: str) -> None:
        row = await self.session.get(KeyValue, key)
        if row is not None:
            await self.session.delete(row)
            await self.session.flush()

    async def updates_marker(self) -> int | None:
        """The marker for the next GET /updates; saved only after a batch was handled (§8)."""
        stored = await self.get(UPDATES_MARKER_KEY)
        if stored is None:
            return None
        marker = stored.get(_MARKER_FIELD)
        return marker if isinstance(marker, int) and not isinstance(marker, bool) else None

    async def set_updates_marker(self, marker: int | None) -> None:
        if marker is None:
            await self.delete(UPDATES_MARKER_KEY)
            return
        await self.set(UPDATES_MARKER_KEY, {_MARKER_FIELD: marker})
