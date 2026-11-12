"""PostgreSQL branch-to-destination shipment handover UI."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    Box,
    BoxCardMembership,
    BoxCycle,
    Courier,
    Location,
    Shipment,
    ShipmentBox,
    User,
)
from medarchive.dependencies import get_pg_session, require_pg_role
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow


router = APIRouter()
branch_only = require_pg_role("branch")


async def _transfer_context(
    request: Request,
    user: User,
    session: AsyncSession,
    *,
    error: str | None = None,
    success: str | None = None,
) -> dict:
    source = await session.scalar(
        select(Location).where(
            Location.kind == "branch",
            Location.branch_id == user.branch_id,
            Location.is_active.is_(True),
        )
    )
    ready_boxes = []
    destinations = []
    couriers = []
    if source is not None:
        active_shipment_box = (
            select(ShipmentBox.box_id)
            .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
            .where(
                ShipmentBox.box_id == Box.id,
                Shipment.status.in_(("draft", "in_transit")),
            )
            .exists()
        )
        rows = await session.execute(
            select(Box, func.count(BoxCardMembership.id))
            .join(BoxCycle, BoxCycle.box_id == Box.id)
            .join(
                BoxCardMembership,
                BoxCardMembership.box_cycle_id == BoxCycle.id,
            )
            .where(
                Box.current_location_id == source.id,
                Box.status == "active",
                BoxCycle.ended_at.is_(None),
                BoxCardMembership.removed_at.is_(None),
                ~active_shipment_box,
            )
            .group_by(Box.id)
            .order_by(Box.created_at.desc())
        )
        ready_boxes = [
            {"id": box.id, "box_number": box.box_number, "card_count": int(count)}
            for box, count in rows.all()
        ]
        destinations = list((
            await session.scalars(
                select(Location)
                .where(
                    Location.kind.in_(("central_office", "archive")),
                    Location.is_active.is_(True),
                )
                .order_by(Location.kind)
            )
        ).all())
        couriers = list((
            await session.scalars(
                select(Courier)
                .where(Courier.is_active.is_(True))
                .order_by(Courier.full_name)
            )
        ).all())

    initials = "".join(part[0] for part in (user.full_name or "").split()[:2]).upper()
    if not initials:
        initials = user.username[:1].upper()
    return {
        "request": request,
        "username": user.username,
        "user_login": user.username,
        "user_full_name": user.full_name or user.username,
        "user_initials": initials,
        "user_branch": source.name if source else "Филиал",
        "user_role_pill": "Филиал",
        "role_label": "Филиал",
        "menu_items": [
            {"url": "/dashboard", "label": "📊 Панель управления"},
            {"url": "/packing", "label": "📦 Упаковка коробки"},
            {"url": "/transfer", "label": "🚚 Передача курьеру"},
        ],
        "ready_boxes": ready_boxes,
        "destinations": destinations,
        "couriers": couriers,
        "error": error,
        "success": success,
    }


@router.get("/transfer")
async def transfer_page(
    request: Request,
    message: str | None = None,
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    context = await _transfer_context(
        request, user, session, success=message
    )
    return request.app.state.templates.TemplateResponse(
        request=request, name="postgresql_transfer.html", context=context
    )


@router.post("/transfer/confirm")
async def confirm_transfer(
    request: Request,
    box_id: uuid.UUID = Form(...),
    to_location_id: uuid.UUID = Form(...),
    courier_id: uuid.UUID = Form(...),
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        source = await session.scalar(
            select(Location).where(
                Location.kind == "branch",
                Location.branch_id == user.branch_id,
                Location.is_active.is_(True),
            )
        )
        if source is None:
            raise ValueError("Для филиала не настроено активное место хранения")
        workflow = PostgreSQLShipmentWorkflow(session)
        shipment = await workflow.create_draft(
            from_location_id=source.id,
            to_location_id=to_location_id,
            courier_id=courier_id,
            issued_by=user.id,
        )
        await workflow.add_box(
            shipment_id=shipment.id, box_id=box_id, actor_id=user.id
        )
        await workflow.hand_over(
            shipment_id=shipment.id, actor_id=user.id
        )
        shipment_number = shipment.shipment_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        context = await _transfer_context(request, user, session, error=str(exc))
        return request.app.state.templates.TemplateResponse(
            request=request, name="postgresql_transfer.html",
            context=context, status_code=422,
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        context = await _transfer_context(
            request, user, session,
            error="Не удалось передать коробку из-за конфликта данных. Обновите страницу и повторите.",
        )
        return request.app.state.templates.TemplateResponse(
            request=request, name="postgresql_transfer.html",
            context=context, status_code=409,
        )
    message = f"Отправление № {shipment_number} передано курьеру."
    return RedirectResponse(
        url=f"/transfer?message={quote(message, safe='')}", status_code=303
    )
