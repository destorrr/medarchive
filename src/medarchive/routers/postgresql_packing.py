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
    Shipment,
    ShipmentBox,
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
    pending_qr_payload: str | None = None,
    pending_card_data: dict | None = None,
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
        active_manifest_box = (
            select(ShipmentBox.box_id)
            .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
            .where(
                ShipmentBox.box_id == Box.id,
                Shipment.status.in_(("draft", "in_transit")),
            )
            .exists()
        )
        statement = select(Box).where(
            Box.current_location_id == location.id,
            Box.status == "active",
            ~active_manifest_box,
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
        "menu_items": [
            {"url": "/dashboard", "label": "📊 Панель управления"},
            {"url": "/search", "label": "🔍 Поиск"},
            {"url": "/history", "label": "📜 Журнал движений"},
            {"url": "/packing", "label": "📦 Упаковка коробки"},
            {"url": "/transfer", "label": "🚚 Передача курьеру"},
        ],
        "location": location,
        "box": box,
        "cards": cards,
        "error": error,
        "success": success,
        "pending_qr_payload": pending_qr_payload,
        "pending_card_data": pending_card_data,
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
        await session.refresh(user)
        return await _render_packing(request, user, session, error=str(exc))
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
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
        box = await session.scalar(
            select(Box).where(
                Box.id == box_id,
                Box.current_location_id == (
                    select(Location.id).where(
                        Location.kind == "branch",
                        Location.branch_id == user.branch_id,
                        Location.is_active.is_(True),
                    ).scalar_subquery()
                ),
                Box.status == "active",
            )
        )
        if box is None:
            raise ValueError("Коробка не найдена или недоступна в вашем филиале")
        data, existing = await PostgreSQLCardIntakeService(session).inspect_from_qr(
            payload=qr_payload, actor_id=user.id
        )
        if existing is None:
            return await _render_packing(
                request,
                user,
                session,
                box_id=box_id,
                pending_qr_payload=qr_payload,
                pending_card_data=data,
            )
        await PostgreSQLShipmentWorkflow(session).add_card_to_box(
            box_id=box_id, card_id=existing.id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await _render_packing(
            request, user, session, box_id=box_id, error=str(exc)
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await _render_packing(
            request, user, session, box_id=box_id,
            error="Карта уже обрабатывается или нарушено ограничение БД.",
        )
    message = "Карта добавлена в коробку."
    return RedirectResponse(
        url=f"/packing?box_id={box_id}&message={quote(message, safe='')}", status_code=303
    )


@router.post("/packing/confirm-card")
async def confirm_card_registration(
    request: Request,
    box_id: uuid.UUID = Form(...),
    qr_payload: str = Form(...),
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        card, _created = await PostgreSQLCardIntakeService(session).register_from_qr(
            payload=qr_payload, actor_id=user.id
        )
        await PostgreSQLShipmentWorkflow(session).add_card_to_box(
            box_id=box_id, card_id=card.id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await _render_packing(
            request, user, session, box_id=box_id, error=str(exc)
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await _render_packing(
            request, user, session, box_id=box_id,
            error="Карта уже зарегистрирована или нарушено ограничение БД.",
        )
    message = "Карта зарегистрирована в МедАрхиве и добавлена в коробку."
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
        await session.refresh(user)
        return await _render_packing(
            request, user, session, box_id=box_id, error=str(exc)
        )
    return RedirectResponse(
        url=f"/packing?box_id={box_id}&message={quote('Карта извлечена из коробки', safe='')}",
        status_code=303,
    )


@router.post("/packing/write-off-box")
async def write_off_box(
    request: Request, box_id: uuid.UUID = Form(...),
    user: User = Depends(branch_only), session: AsyncSession = Depends(get_pg_session),
):
    try:
        box = await PostgreSQLShipmentWorkflow(session).write_off_box(box_id=box_id, actor_id=user.id)
        number = box.box_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await _render_packing(request, user, session, box_id=box_id, error=str(exc))
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await _render_packing(request, user, session, box_id=box_id, error="Списание заблокировано ограничениями целостности.")
    message = f"Пустая коробка {number} списана."
    return RedirectResponse(url=f"/packing?message={quote(message, safe='')}", status_code=303)
