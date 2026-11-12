"""PostgreSQL-backed branch packing UI."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    Box,
    BoxCardMembership,
    BoxCycle,
    Card,
    Location,
    User,
)
from medarchive.dependencies import get_pg_session, require_pg_role
from medarchive.services.postgresql_card_intake import PostgreSQLCardIntakeService
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow


router = APIRouter()
branch_only = require_pg_role("branch")


async def _packing_context(
    request: Request,
    user: User,
    session: AsyncSession,
    *,
    box_id: uuid.UUID | None = None,
    error: str | None = None,
    success: str | None = None,
) -> dict:
    location = await session.scalar(
        select(Location).where(
            Location.kind == "branch",
            Location.branch_id == user.branch_id,
            Location.is_active.is_(True),
        )
    )
    box = None
    cards: list[Card] = []
    if location is not None:
        statement = select(Box).where(
            Box.current_location_id == location.id,
            Box.status == "active",
        )
        if box_id is not None:
            statement = statement.where(Box.id == box_id)
        else:
            statement = statement.where(Box.created_by == user.id)
        box = await session.scalar(statement.order_by(Box.created_at.desc()).limit(1))
        if box is not None:
            cards = list((
                await session.scalars(
                    select(Card)
                    .join(BoxCardMembership, BoxCardMembership.card_id == Card.id)
                    .join(BoxCycle, BoxCycle.id == BoxCardMembership.box_cycle_id)
                    .where(
                        BoxCycle.box_id == box.id,
                        BoxCycle.ended_at.is_(None),
                        BoxCardMembership.removed_at.is_(None),
                    )
                    .order_by(Card.card_number)
                )
            ).all())

    role_label = "Филиал"
    initials = "".join(part[0] for part in (user.full_name or "").split()[:2]).upper()
    if not initials:
        initials = user.username[:1].upper()
    return {
        "request": request,
        "username": user.username,
        "user_login": user.username,
        "user_full_name": user.full_name or user.username,
        "user_initials": initials,
        "user_branch": location.name if location else role_label,
        "user_role_pill": role_label,
        "role_label": role_label,
        "menu_items": [{"url": "/packing", "label": "📦 Упаковка коробки"}],
        "location": location,
        "box": box,
        "cards": cards,
        "error": error,
        "success": success,
    }


async def _render_packing(
    request: Request,
    user: User,
    session: AsyncSession,
    **kwargs,
):
    context = await _packing_context(request, user, session, **kwargs)
    return request.app.state.templates.TemplateResponse(
        request=request, name="postgresql_packing.html", context=context
    )


@router.get("/packing")
async def packing_page(
    request: Request,
    box_id: uuid.UUID | None = None,
    message: str | None = None,
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    return await _render_packing(
        request, user, session, box_id=box_id, success=message
    )


@router.post("/packing/new-box")
async def create_box(
    request: Request,
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        location = await session.scalar(
            select(Location).where(
                Location.kind == "branch",
                Location.branch_id == user.branch_id,
                Location.is_active.is_(True),
            )
        )
        if location is None:
            raise ValueError("Для филиала не настроено активное место хранения")
        box = await PostgreSQLShipmentWorkflow(session).create_box(
            location_id=location.id, actor_id=user.id
        )
        box_id = box.id
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        return await _render_packing(request, user, session, error=str(exc))
    except IntegrityError:
        await session.rollback()
        return await _render_packing(
            request, user, session,
            error="Не удалось создать коробку из-за конфликта данных. Повторите попытку.",
        )
    return RedirectResponse(url=f"/packing?box_id={box_id}", status_code=303)


@router.post("/packing/scan-card")
async def scan_card(
    request: Request,
    box_id: uuid.UUID = Form(...),
    qr_payload: str = Form(...),
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        card, created = await PostgreSQLCardIntakeService(session).register_from_qr(
            payload=qr_payload, actor_id=user.id
        )
        await PostgreSQLShipmentWorkflow(session).add_card_to_box(
            box_id=box_id, card_id=card.id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        return await _render_packing(
            request, user, session, box_id=box_id, error=str(exc)
        )
    except IntegrityError:
        await session.rollback()
        return await _render_packing(
            request, user, session, box_id=box_id,
            error="Карта с таким номером уже обрабатывается или нарушено ограничение БД.",
        )
    message = "Карта зарегистрирована в МедАрхиве и добавлена в коробку." if created else "Карта добавлена в коробку."
    return RedirectResponse(
        url=f"/packing?box_id={box_id}&message={quote(message, safe='')}", status_code=303
    )


@router.post("/packing/remove-card")
async def remove_card(
    request: Request,
    box_id: uuid.UUID = Form(...),
    card_id: uuid.UUID = Form(...),
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLShipmentWorkflow(session).remove_card_from_box(
            box_id=box_id, card_id=card_id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        return await _render_packing(
            request, user, session, box_id=box_id, error=str(exc)
        )
    return RedirectResponse(
        url=f"/packing?box_id={box_id}&message={quote('Карта извлечена из коробки', safe='')}",
        status_code=303,
    )
