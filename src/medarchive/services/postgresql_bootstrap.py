"""Idempotent bootstrap for required PostgreSQL singleton locations."""

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Location


SYSTEM_LOCATIONS = {
    "central_office": "Центральный офис",
    "archive": "Архив",
}


async def ensure_system_locations(session: AsyncSession) -> None:
    """Create the central-office and archive locations once, without overwriting them."""
    await session.execute(text("SELECT pg_advisory_xact_lock(741928362)"))
    for kind, name in SYSTEM_LOCATIONS.items():
        location = await session.scalar(
            select(Location).where(Location.kind == kind).with_for_update()
        )
        if location is None:
            session.add(Location(kind=kind, branch_id=None, name=name, is_active=True))
    await session.flush()
