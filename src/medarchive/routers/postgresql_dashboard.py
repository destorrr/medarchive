"""PostgreSQL-backed dashboard for the target application surface."""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    ArchiveShelf,
    Box,
    BoxCardMembership,
    BoxCycle,
    BoxPlacement,
    Branch,
    Card,
    CardPlacement,
    DomainEvent,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.dependencies import get_pg_current_user, get_pg_session, require_pg_role
from medarchive.adapters.postgresql.repositories import CardRepository


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
    if user.role in {"archive", "viewer", "admin", "central_office"}:
        menu_items.append({"url": "/search", "label": "🔍 Поиск"})
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


def _card_status(card: Card, location_kind: str | None) -> tuple[str, str]:
    if card.custody_status != "available":
        labels = {
            "at_court": ("В суде", "badge-pending_return"),
            "at_insurance": ("В страховой", "badge-pending_return"),
            "at_external_organization": ("Во внешней организации", "badge-pending_return"),
        }
        return labels.get(card.custody_status, (card.custody_status, "badge-pending_return"))
    if card.current_shipment_id is not None:
        return "В пути", "badge-in_transit"
    if location_kind == "archive":
        return "В архиве", "badge-placed"
    if location_kind == "branch":
        return "В филиале", "badge-in_branch"
    if location_kind == "central_office":
        return "В ЦО", "badge-in_box"
    return card.status, "badge-in_branch"


async def _search_context(
    session: AsyncSession, user: User, query: str | None
) -> dict:
    query = (query or "").strip()
    cards_out: list[dict] = []
    boxes_out: list[dict] = []
    if not query:
        return {"query": query, "cards": cards_out, "boxes": boxes_out}

    location_id = None
    if user.role == "archive":
        location_id = await session.scalar(
            select(Location.id).where(Location.kind == "archive")
        )
    elif user.role == "central_office":
        location_id = await session.scalar(
            select(Location.id).where(Location.kind == "central_office")
        )

    matched_cards = await CardRepository(session).search(query, limit=50)
    if location_id is not None:
        matched_cards = [
            card for card in matched_cards
            if card.current_location_id == location_id
        ]
    card_ids = [card.id for card in matched_cards]
    locations_by_id = {
        item.id: item for item in (await session.scalars(select(Location))).all()
    }

    membership_rows = []
    if card_ids:
        membership_rows = list((
            await session.execute(
                select(BoxCardMembership, Box, BoxPlacement, ArchiveShelf)
                .join(BoxCycle, BoxCycle.id == BoxCardMembership.box_cycle_id)
                .join(Box, Box.id == BoxCycle.box_id)
                .outerjoin(
                    BoxPlacement,
                    (BoxPlacement.box_id == Box.id) & BoxPlacement.removed_at.is_(None),
                )
                .outerjoin(ArchiveShelf, ArchiveShelf.id == BoxPlacement.shelf_id)
                .where(
                    BoxCardMembership.card_id.in_(card_ids),
                    BoxCardMembership.removed_at.is_(None),
                    BoxCycle.ended_at.is_(None),
                )
            )
        )).all()
    memberships_by_card = {}
    for membership, box, placement, shelf in membership_rows:
        memberships_by_card[membership.card_id] = {
            "box_number": box.box_number,
            "place": f"{shelf.section_name} / {shelf.shelf_name}" if shelf else None,
        }

    card_placements = {}
    if card_ids:
        rows = list((
            await session.execute(
                select(CardPlacement, ArchiveShelf)
                .join(ArchiveShelf, ArchiveShelf.id == CardPlacement.shelf_id)
                .where(
                    CardPlacement.card_id.in_(card_ids),
                    CardPlacement.removed_at.is_(None),
                )
            )
        )).all()
        card_placements = {
            placement.card_id: f"{shelf.section_name} / {shelf.shelf_name}"
            for placement, shelf in rows
        }

    for card in matched_cards:
        location = locations_by_id.get(card.current_location_id)
        status_label, status_badge = _card_status(
            card, location.kind if location else None
        )
        membership = memberships_by_card.get(card.id, {})
        cards_out.append({
            "id": str(card.id),
            "full_name": card.full_name,
            "status_label": status_label,
            "status_badge": status_badge,
            "box_number": membership.get("box_number"),
            "place": membership.get("place") or card_placements.get(card.id)
            or (location.name if location else ("В пути" if card.current_shipment_id else None)),
            "stale": [],
        })

    box_statement = (
        select(Box, func.count(BoxCardMembership.id), BoxPlacement, ArchiveShelf)
        .outerjoin(BoxCycle, (BoxCycle.box_id == Box.id) & BoxCycle.ended_at.is_(None))
        .outerjoin(
            BoxCardMembership,
            (BoxCardMembership.box_cycle_id == BoxCycle.id)
            & BoxCardMembership.removed_at.is_(None),
        )
        .outerjoin(
            BoxPlacement,
            (BoxPlacement.box_id == Box.id) & BoxPlacement.removed_at.is_(None),
        )
        .outerjoin(ArchiveShelf, ArchiveShelf.id == BoxPlacement.shelf_id)
        .where(Box.box_number.ilike(f"%{query}%"))
        .group_by(Box.id, BoxPlacement.id, ArchiveShelf.id)
        .order_by(Box.box_number)
        .limit(50)
    )
    for box, card_count, placement, shelf in (await session.execute(box_statement)).all():
        if location_id is not None and box.current_location_id != location_id:
            continue
        location = locations_by_id.get(box.current_location_id)
        boxes_out.append({
            "id": str(box.id),
            "box_number": box.box_number,
            "status_label": "Активна" if box.status == "active" else box.status,
            "status_badge": "badge-ready" if box.status == "active" else "badge-lost",
            "card_count": int(card_count or 0),
            "place": (
                f"{shelf.section_name} / {shelf.shelf_name}" if shelf
                else location.name if location
                else ("В пути" if box.current_shipment_id else None)
            ),
        })
    return {"query": query, "cards": cards_out, "boxes": boxes_out}


@router.get("/search")
async def postgresql_search_page(
    request: Request,
    query: str | None = None,
    user: User = Depends(require_pg_role("archive", "central_office", "viewer", "admin")),
    session: AsyncSession = Depends(get_pg_session),
):
    ctx = _dashboard_context(request, user, None)
    ctx.update(await _search_context(session, user, query))
    return request.app.state.templates.TemplateResponse(
        request=request, name="search.html", context=ctx
    )


@router.get("/search/results")
async def postgresql_search_results(
    request: Request,
    query: str | None = None,
    user: User = Depends(require_pg_role("archive", "central_office", "viewer", "admin")),
    session: AsyncSession = Depends(get_pg_session),
):
    ctx = await _search_context(session, user, query)
    return request.app.state.templates.TemplateResponse(
        request=request, name="search_fragment.html", context=ctx
    )


@router.post("/search")
async def postgresql_search_submit(
    query: str,
    user: User = Depends(require_pg_role("archive", "central_office", "viewer", "admin")),
):
    from urllib.parse import quote
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url=f"/search?query={quote(query)}", status_code=303)
