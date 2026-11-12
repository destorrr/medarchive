"""PostgreSQL branch receiving of whole shipments from the Central Office."""

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
    Card,
    Courier,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.dependencies import get_pg_session, require_pg_role
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow


router = APIRouter()
branch_only = require_pg_role("branch")


async def _branch_location(session: AsyncSession, user: User) -> Location | None:
    return await session.scalar(
        select(Location).where(
            Location.kind == "branch",
            Location.branch_id == user.branch_id,
            Location.is_active.is_(True),
        )
    )


@router.get("/branch/receive")
async def branch_receive_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    location = await _branch_location(session, user)
    incoming = []
    if location is not None:
        rows = (
            await session.execute(
                select(Shipment, Location, Courier)
                .join(Location, Location.id == Shipment.from_location_id)
                .join(Courier, Courier.id == Shipment.courier_id)
                .where(
                    Shipment.to_location_id == location.id,
                    Shipment.status == "in_transit",
                )
                .order_by(Shipment.handed_over_at, Shipment.shipment_number)
            )
        ).all()
        for shipment, source, courier in rows:
            manifest_rows = (
                await session.execute(
                    select(ShipmentCard, Card)
                    .join(Card, Card.id == ShipmentCard.card_id)
                    .where(ShipmentCard.shipment_id == shipment.id)
                    .order_by(Card.card_number)
                )
            ).all()
            boxes = list((
                await session.scalars(
                    select(Box)
                    .join(ShipmentBox, ShipmentBox.box_id == Box.id)
                    .where(ShipmentBox.shipment_id == shipment.id)
                    .order_by(Box.box_number)
                )
            ).all())
            incoming.append({
                "id": shipment.id,
                "number": shipment.shipment_number,
                "source": source.name,
                "courier": courier.full_name,
                "handed_over_at": shipment.handed_over_at,
                "box_count": len(boxes),
                "card_count": len(manifest_rows),
                "boxes": [
                    {
                        "box_number": box.box_number,
                        "cards": [
                            {"card_number": card.card_number, "full_name": card.full_name}
                            for row, card in manifest_rows if row.box_id == box.id
                        ],
                    }
                    for box in boxes
                ],
                "standalone_cards": [
                    {"card_number": card.card_number, "full_name": card.full_name}
                    for row, card in manifest_rows if row.box_id is None
                ],
            })
    context = {
        "request": request,
        "username": user.username,
        "user_login": user.username,
        "user_full_name": user.full_name or user.username,
        "user_initials": "".join(part[0] for part in (user.full_name or "").split()[:2]).upper()
        or user.username[:1].upper(),
        "user_branch": location.name if location else "Филиал",
        "user_role_pill": "Филиал",
        "role_label": "Филиал",
        "menu_items": [
            {"url": "/dashboard", "label": "📊 Панель управления"},
            {"url": "/branch/receive", "label": "📥 Приёмка"},
            {"url": "/packing", "label": "📦 Упаковка коробки"},
            {"url": "/transfer", "label": "🚚 Передача курьеру"},
        ],
        "incoming": incoming,
        "error": error,
        "success": message,
    }
    return request.app.state.templates.TemplateResponse(
        request=request, name="postgresql_branch_receive.html", context=context
    )


@router.post("/branch/receive/confirm")
async def branch_receive_confirm(
    request: Request,
    shipment_id: uuid.UUID = Form(...),
    confirmed: bool = Form(False),
    user: User = Depends(branch_only),
    session: AsyncSession = Depends(get_pg_session),
):
    if not confirmed:
        return await branch_receive_page(
            request,
            error="Подтвердите проверку всего отправления без расхождений.",
            user=user, session=session,
        )
    try:
        shipment = await PostgreSQLShipmentWorkflow(session).receive(
            shipment_id=shipment_id, actor_id=user.id
        )
        number = shipment.shipment_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await branch_receive_page(
            request, error=str(exc), user=user, session=session
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await branch_receive_page(
            request,
            error="Приёмка заблокирована: проверьте манифест и фактический состав.",
            user=user, session=session,
        )
    message = f"Отправление № {number} принято полностью."
    return RedirectResponse(
        url=f"/branch/receive?message={quote(message, safe='')}", status_code=303
    )
