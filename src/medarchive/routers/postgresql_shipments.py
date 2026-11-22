"""Read-only PostgreSQL shipment register and detail pages."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from medarchive.adapters.postgresql.models import (
    Box, Card, Courier, DomainEvent, Location, Shipment, ShipmentBox,
    ShipmentCard, User,
)
from medarchive.dependencies import get_pg_session, require_pg_role

router = APIRouter()
shipment_reader = require_pg_role("branch", "central_office", "archive")

EVENT_LABELS = {
    "shipment.created": "Отправление создано",
    "shipment.handed_over": "Передано курьеру",
    "shipment.exception_registered": "Зарегистрировано расхождение",
    "shipment.received": "Отправление принято",
    "shipment.exception_resolved": "Инцидент разрешён",
}

STATUS_LABELS = {
    "draft": "Черновик", "in_transit": "В пути", "received": "Принято",
    "exception": "Инцидент", "cancelled": "Отменено",
}


async def _user_location(session: AsyncSession, user: User):
    if user.role == "branch":
        return await session.scalar(select(Location).where(
            Location.kind == "branch", Location.branch_id == user.branch_id,
            Location.is_active.is_(True),
        ))
    return await session.scalar(select(Location).where(
        Location.kind == user.role, Location.is_active.is_(True),
    ))


def _context(request: Request, user: User, location: Location, **extra):
    if user.role == "branch":
        role_label, menu = "Филиал", [
            ("/dashboard", "Панель управления"), ("/search", "Поиск"),
            ("/history", "Журнал движений"), ("/shipments", "Отправления"),
            ("/branch/receive", "Приёмка"), ("/packing", "Упаковка коробки"),
            ("/transfer", "Передача курьеру"),
        ]
    elif user.role == "central_office":
        role_label, menu = "Центральный офис", [
            ("/dashboard", "Панель управления"), ("/search", "Поиск"),
            ("/history", "Журнал движений"), ("/shipments", "Отправления"),
            ("/requests/new", "Запросить карты из архива"),
            ("/central/receive", "Приёмка"), ("/central/packing", "Упаковка"),
            ("/central/transfer", "Отправка"), ("/central/custody", "Внешнее хранение"),
        ]
    else:
        role_label, menu = "Архив", [
            ("/dashboard", "Панель управления"), ("/search", "Поиск"),
            ("/history", "Журнал движений"), ("/shipments", "Отправления"),
            ("/receive", "Приёмка"), ("/placement", "Размещение"),
            ("/requests", "Заявки ЦО"),
        ]
    initials = "".join(part[0] for part in (user.full_name or "").split()[:2]).upper()
    return {
        "request": request, "username": user.username, "user_login": user.username,
        "user_full_name": user.full_name or user.username,
        "user_initials": initials or user.username[:1].upper(),
        "user_branch": location.name, "user_role_pill": role_label,
        "role_label": role_label,
        "menu_items": [{"url": url, "label": label} for url, label in menu],
        **extra,
    }


def _visibility(location: Location):
    return or_(
        Shipment.from_location_id == location.id,
        Shipment.to_location_id == location.id,
    )


async def _manifest(session: AsyncSession, shipment: Shipment):
    box_rows = (await session.scalars(
        select(Box).join(ShipmentBox, ShipmentBox.box_id == Box.id)
        .where(ShipmentBox.shipment_id == shipment.id).order_by(Box.box_number)
    )).all()
    card_rows = (await session.execute(
        select(ShipmentCard, Card).join(Card, Card.id == ShipmentCard.card_id)
        .where(ShipmentCard.shipment_id == shipment.id).order_by(Card.card_number)
    )).all()
    boxes = [{
        "box_number": box.box_number,
        "cards": [{
            "card_number": card.card_number, "full_name": card.full_name,
        } for row, card in card_rows if row.box_id == box.id],
    } for box in box_rows]
    standalone = [{
        "card_number": card.card_number, "full_name": card.full_name,
    } for row, card in card_rows if row.box_id is None]
    return boxes, standalone, len(box_rows), len(card_rows)


@router.get("/shipments")
async def shipment_list(
    request: Request,
    status: str | None = None,
    user: User = Depends(shipment_reader),
    session: AsyncSession = Depends(get_pg_session),
):
    location = await _user_location(session, user)
    if location is None:
        raise HTTPException(status_code=403, detail="Для пользователя не настроена активная локация")
    source = aliased(Location)
    destination = aliased(Location)
    box_count_query = select(func.count()).select_from(ShipmentBox).where(
        ShipmentBox.shipment_id == Shipment.id
    ).scalar_subquery()
    card_count_query = select(func.count()).select_from(ShipmentCard).where(
        ShipmentCard.shipment_id == Shipment.id
    ).scalar_subquery()
    query = (
        select(Shipment, source, destination, Courier, box_count_query, card_count_query)
        .join(source, source.id == Shipment.from_location_id)
        .join(destination, destination.id == Shipment.to_location_id)
        .join(Courier, Courier.id == Shipment.courier_id)
        .where(_visibility(location))
    )
    if status in STATUS_LABELS:
        query = query.where(Shipment.status == status)
    rows = (await session.execute(
        query.order_by(Shipment.created_at.desc(), Shipment.shipment_number.desc())
        .limit(200)
    )).all()
    items = []
    for shipment, origin, target, courier, box_count, card_count in rows:
        items.append({
            "id": shipment.id, "number": shipment.shipment_number,
            "source": origin.name, "destination": target.name,
            "courier": courier.full_name, "status": shipment.status,
            "status_label": STATUS_LABELS[shipment.status],
            "created_at": shipment.created_at, "handed_over_at": shipment.handed_over_at,
            "received_at": shipment.received_at, "box_count": box_count or 0,
            "card_count": card_count or 0,
        })
    return request.app.state.templates.TemplateResponse(
        request=request, name="postgresql_shipments.html",
        context=_context(request, user, location, shipments=items, selected_status=status),
    )


@router.get("/shipments/{shipment_id}")
async def shipment_detail(
    shipment_id: uuid.UUID,
    request: Request,
    user: User = Depends(shipment_reader),
    session: AsyncSession = Depends(get_pg_session),
):
    location = await _user_location(session, user)
    if location is None:
        raise HTTPException(status_code=403, detail="Для пользователя не настроена активная локация")
    source, destination = aliased(Location), aliased(Location)
    row = await session.execute(
        select(Shipment, source, destination, Courier)
        .join(source, source.id == Shipment.from_location_id)
        .join(destination, destination.id == Shipment.to_location_id)
        .join(Courier, Courier.id == Shipment.courier_id)
        .where(Shipment.id == shipment_id, _visibility(location))
    )
    result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail="Отправление не найдено")
    shipment, origin, target, courier = result
    boxes, standalone, box_count, card_count = await _manifest(session, shipment)
    timeline_rows = (await session.execute(
        select(DomainEvent, User)
        .outerjoin(User, User.id == DomainEvent.actor_user_id)
        .where(
            DomainEvent.aggregate_type == "shipment",
            DomainEvent.aggregate_id == shipment.id,
        )
        .order_by(DomainEvent.occurred_at, DomainEvent.id)
    )).all()
    timeline = [{
        "event_type": event.event_type, "event_label": EVENT_LABELS.get(event.event_type, event.event_type),
        "occurred_at": event.occurred_at,
        "actor": actor.full_name if actor else "Система", "payload": event.payload or {},
    } for event, actor in timeline_rows]
    detail = {
        "id": shipment.id, "number": shipment.shipment_number,
        "source": origin.name, "destination": target.name,
        "courier": courier.full_name, "courier_phone": courier.phone,
        "status": shipment.status, "status_label": STATUS_LABELS[shipment.status],
        "created_at": shipment.created_at, "handed_over_at": shipment.handed_over_at,
        "received_at": shipment.received_at, "exception_at": shipment.exception_at,
        "exception_note": shipment.exception_note,
        "box_count": box_count, "card_count": card_count,
    }
    return request.app.state.templates.TemplateResponse(
        request=request, name="postgresql_shipment_detail.html",
        context=_context(
            request, user, location, shipment=detail, boxes=boxes,
            standalone_cards=standalone, timeline=timeline,
        ),
    )
