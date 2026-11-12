"""PostgreSQL-backed dashboard for the target application surface."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    Box,
    Branch,
    Card,
    DomainEvent,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.dependencies import get_pg_current_user, get_pg_session, require_pg_role


router = APIRouter()

ROLE_LABELS = {
    "branch": "Филиал",
    "central_office": "Центральный офис",
    "archive": "Архив",
    "viewer": "Просмотр",
    "admin": "Администратор",
}

EVENT_LABELS = {
    "shipment.handed_over": "Отправление передано курьеру",
    "shipment.received": "Отправление принято",
    "archive_request.created": "Создана заявка в архив",
    "archive_request.not_found": "Карта не найдена в архиве",
    "box.placed_in_archive": "Коробка размещена в архиве",
    "card.placed_in_archive": "Карта размещена в архиве",
    "card.external_custody_started": "Карта передана внешней организации",
    "card.external_custody_returned": "Карта возвращена в ЦО",
}


def _dashboard_context(request: Request, user: User, branch_name: str | None) -> dict:
    role_label = ROLE_LABELS.get(user.role, "Пользователь")
    menu_items = [{"url": "/dashboard", "label": "📊 Панель управления"}]
    if user.role == "admin":
        menu_items.append({"url": "/admin", "label": "⚙️ Администрирование"})
    return {
        "request": request,
        "username": user.username,
        "user_login": user.username,
        "user_full_name": user.full_name or user.username,
        "user_initials": "".join(part[0] for part in (user.full_name or "").split()[:2]).upper()
        or user.username[:1].upper(),
        "user_branch": branch_name or role_label,
        "user_role_pill": role_label,
        "role_label": role_label,
        "menu_items": menu_items,
    }


async def _count(session: AsyncSession, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


@router.get("/dashboard")
async def postgresql_dashboard(
    request: Request,
    user: User = Depends(
        require_pg_role("branch", "central_office", "archive", "admin", "viewer")
    ),
    session: AsyncSession = Depends(get_pg_session),
):
    branch_name = None
    if user.branch_id is not None:
        branch_name = await session.scalar(
            select(Branch.name).where(Branch.id == user.branch_id)
        )

    archive_id = await session.scalar(
        select(Location.id).where(
            Location.kind == "archive", Location.is_active.is_(True)
        )
    )
    scope_location_id = None
    if user.role == "branch":
        scope_location_id = await session.scalar(
            select(Location.id).where(
                Location.kind == "branch", Location.branch_id == user.branch_id,
                Location.is_active.is_(True),
            )
        )
    elif user.role in {"central_office", "archive"}:
        scope_location_id = await session.scalar(
            select(Location.id).where(
                Location.kind == user.role, Location.is_active.is_(True)
            )
        )

    if scope_location_id is None:
        total_cards = await _count(session, Card)
        cards_placed = 0
        if archive_id is not None:
            cards_placed = int(
                await session.scalar(
                    select(func.count()).select_from(Card).where(
                        Card.current_location_id == archive_id
                    )
                ) or 0
            )
        cards_in_branch = int(
            await session.scalar(
                select(func.count())
                .select_from(Card)
                .join(Location, Location.id == Card.current_location_id)
                .where(Location.kind == "branch")
            ) or 0
        )
        cards_in_transit = int(
            await session.scalar(
                select(func.count()).select_from(Card).where(
                    Card.current_shipment_id.is_not(None)
                )
            ) or 0
        )
        total_boxes = await _count(session, Box)
        open_requests_query = select(func.count()).select_from(ArchiveRequest).where(
            ArchiveRequest.status.in_(("new", "in_progress"))
        )
    else:
        cards_at_location = int(
            await session.scalar(
                select(func.count()).select_from(Card).where(
                    Card.current_location_id == scope_location_id
                )
            ) or 0
        )
        cards_in_transit = int(
            await session.scalar(
                select(func.count())
                .select_from(ShipmentCard)
                .join(Shipment, Shipment.id == ShipmentCard.shipment_id)
                .where(
                    Shipment.status == "in_transit",
                    or_(
                        Shipment.from_location_id == scope_location_id,
                        Shipment.to_location_id == scope_location_id,
                    ),
                )
            ) or 0
        )
        total_cards = cards_at_location + cards_in_transit
        cards_placed = cards_at_location if user.role == "archive" else 0
        cards_in_branch = cards_at_location if user.role == "branch" else 0
        boxes_at_location = int(
            await session.scalar(
                select(func.count()).select_from(Box).where(
                    Box.current_location_id == scope_location_id
                )
            ) or 0
        )
        boxes_in_transit = int(
            await session.scalar(
                select(func.count())
                .select_from(ShipmentBox)
                .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
                .where(
                    Shipment.status == "in_transit",
                    or_(
                        Shipment.from_location_id == scope_location_id,
                        Shipment.to_location_id == scope_location_id,
                    ),
                )
            ) or 0
        )
        total_boxes = boxes_at_location + boxes_in_transit
        open_requests_query = select(func.count()).select_from(ArchiveRequest).where(
            ArchiveRequest.status.in_(("new", "in_progress"))
        )
        if user.role == "central_office":
            open_requests_query = open_requests_query.where(
                ArchiveRequest.requested_by.in_(
                    select(User.id).where(User.role == "central_office")
                )
            )
        elif user.role != "archive":
            open_requests_query = open_requests_query.where(False)

    open_requests = int(await session.scalar(open_requests_query) or 0)

    events_query = select(DomainEvent).order_by(DomainEvent.occurred_at.desc())
    if scope_location_id is not None:
        visible_card_ids = select(Card.id).where(Card.current_location_id == scope_location_id)
        visible_shipment_ids = select(Shipment.id).where(
            or_(
                Shipment.from_location_id == scope_location_id,
                Shipment.to_location_id == scope_location_id,
            )
        )
        events_query = events_query.where(
            or_(
                (DomainEvent.aggregate_type == "card") & DomainEvent.aggregate_id.in_(visible_card_ids),
                (DomainEvent.aggregate_type == "shipment") & DomainEvent.aggregate_id.in_(visible_shipment_ids),
            )
        )
    events = list((await session.scalars(events_query.limit(5))).all())
    card_ids = {
        event.aggregate_id for event in events if event.aggregate_type == "card"
    }
    cards_by_id = {}
    if card_ids:
        cards_by_id = {
            card.id: card
            for card in (
                await session.scalars(select(Card).where(Card.id.in_(card_ids)))
            ).all()
        }
    locations = {
        location.id: location.name
        for location in (await session.scalars(select(Location))).all()
    }

    recent_movements = []
    for event in events:
        payload = event.payload or {}
        card = cards_by_id.get(event.aggregate_id)
        destination_id = payload.get("to_location_id")
        recent_movements.append({
            "timestamp": event.occurred_at,
            "card_name": card.full_name if card else event.aggregate_type,
            "action": EVENT_LABELS.get(event.event_type, event.event_type),
            "to_location": locations.get(destination_id) if destination_id else None,
        })

    ctx = _dashboard_context(request, user, branch_name)
    ctx.update({
        "total_cards": total_cards,
        "cards_placed": cards_placed,
        "cards_in_branch": cards_in_branch,
        "cards_in_transit": cards_in_transit,
        "total_boxes": total_boxes,
        "open_requests": open_requests,
        "recent_movements": recent_movements,
    })
    return request.app.state.templates.TemplateResponse(
        request=request, name="dashboard.html", context=ctx
    )
