"""PostgreSQL-backed dashboard for the target application surface."""

from __future__ import annotations

import uuid
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
from medarchive.dependencies import get_pg_session, require_pg_role
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
    "box.created": "Создана коробка",
    "box.cycle_started": "Начат новый цикл коробки",
    "box.cycle_closed": "Цикл коробки закрыт",
    "box.placed_in_archive": "Коробка размещена в архиве",
    "box.removed_from_archive_shelf": "Коробка снята с архивной полки",
    "box.written_off": "Коробка списана",
    "card.added_to_box": "Карта добавлена в коробку",
    "card.removed_from_box": "Карта извлечена из коробки",
    "card.placed_in_archive": "Карта размещена в архиве",
    "card.removed_from_archive_shelf": "Карта снята с архивной полки",
    "card.shipment_handed_over": "Карта передана курьеру",
    "card.shipment_received": "Карта принята получателем",
    "box.shipment_handed_over": "Коробка передана курьеру",
    "box.shipment_received": "Коробка принята получателем",
    "shipment.created": "Создано отправление",
    "shipment.exception_registered": "Зарегистрирован инцидент отправления",
    "shipment.exception_resolved": "Инцидент отправления разрешён",
    "card.external_custody_started": "Карта передана внешней организации",
    "card.external_custody_returned": "Карта возвращена в ЦО",
}


def _dashboard_context(request: Request, user: User, branch_name: str | None) -> dict:
    role_label = ROLE_LABELS.get(user.role, "Пользователь")
    menu_items = [{"url": "/dashboard", "label": "📊 Панель управления"}]
    if user.role == "branch":
        menu_items.extend([
            {"url": "/branch/receive", "label": "📥 Приёмка"},
            {"url": "/packing", "label": "📦 Упаковка коробки"},
        ])
        menu_items.append({"url": "/transfer", "label": "🚚 Передача курьеру"})
    if user.role in {"archive", "viewer", "admin", "central_office"}:
        menu_items.extend([
            {"url": "/search", "label": "🔍 Поиск"},
            {"url": "/history", "label": "📜 Журнал движений"},
        ])
    if user.role == "central_office":
        menu_items.extend([
            {"url": "/requests/new", "label": "📋 Запросить карты из архива"},
            {"url": "/central/receive", "label": "📥 Приёмка"},
            {"url": "/central/packing", "label": "📦 Упаковка"},
            {"url": "/central/transfer", "label": "🚚 Отправка"},
            {"url": "/central/custody", "label": "⚖️ Внешнее хранение"},
        ])
    if user.role == "archive":
        menu_items.extend([
            {"url": "/receive", "label": "📥 Приёмка"},
            {"url": "/placement", "label": "🗂 Размещение"},
            {"url": "/requests", "label": "📋 Заявки ЦО"},
        ])
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

    if user.role not in {"branch", "central_office", "archive"}:
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
    elif scope_location_id is None:
        total_cards = 0
        cards_placed = 0
        cards_in_branch = 0
        cards_in_transit = 0
        total_boxes = 0
        open_requests_query = select(func.count()).select_from(ArchiveRequest).where(False)
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
    if user.role in {"branch", "central_office", "archive"}:
        if scope_location_id is None:
            events_query = events_query.where(False)
        else:
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
        if destination_id:
            try:
                destination_id = uuid.UUID(str(destination_id))
            except (TypeError, ValueError):
                destination_id = None
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
            "at_other": ("Во внешней организации", "badge-pending_return"),
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
            select(Location.id).where(
                Location.kind == "archive", Location.is_active.is_(True)
            )
        )
    elif user.role == "central_office":
        location_id = await session.scalar(
            select(Location.id).where(
                Location.kind == "central_office", Location.is_active.is_(True)
            )
        )
    if user.role in {"archive", "central_office"} and location_id is None:
        return {"query": query, "cards": cards_out, "boxes": boxes_out}

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
        membership_rows = (
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
        ).all()
    memberships_by_card = {}
    for membership, box, placement, shelf in membership_rows:
        memberships_by_card[membership.card_id] = {
            "box_number": box.box_number,
            "place": f"{shelf.section_name} / {shelf.shelf_name}" if shelf else None,
        }

    card_placements = {}
    if card_ids:
        rows = (
            await session.execute(
                select(CardPlacement, ArchiveShelf)
                .join(ArchiveShelf, ArchiveShelf.id == CardPlacement.shelf_id)
                .where(
                    CardPlacement.card_id.in_(card_ids),
                    CardPlacement.removed_at.is_(None),
                )
            )
        ).all()
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


@router.get("/history")
async def postgresql_history_page(
    request: Request,
    page: int = 1,
    user: User = Depends(require_pg_role("archive", "central_office", "viewer", "admin")),
    session: AsyncSession = Depends(get_pg_session),
):
    page = max(1, page)
    page_size = 50
    total = int(await session.scalar(select(func.count(DomainEvent.id))) or 0)
    events = list((await session.scalars(
        select(DomainEvent)
        .order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )).all())

    card_ids = {event.aggregate_id for event in events if event.aggregate_type == "card"}
    box_ids = {event.aggregate_id for event in events if event.aggregate_type == "box"}
    shipment_ids = {event.aggregate_id for event in events if event.aggregate_type == "shipment"}
    request_ids = {event.aggregate_id for event in events if event.aggregate_type == "archive_request"}
    actor_ids = {event.actor_user_id for event in events if event.actor_user_id is not None}
    location_ids, shelf_ids = set(), set()
    for event in events:
        payload = event.payload or {}
        for key in ("from_location_id", "to_location_id", "location_id"):
            value = payload.get(key)
            if value:
                try:
                    location_ids.add(uuid.UUID(str(value)))
                except (TypeError, ValueError):
                    pass
        value = payload.get("shelf_id")
        if value:
            try:
                shelf_ids.add(uuid.UUID(str(value)))
            except (TypeError, ValueError):
                pass

    boxes = {item.id: item for item in (await session.scalars(
        select(Box).where(Box.id.in_(box_ids))
    )).all()} if box_ids else {}
    shipments = {item.id: item for item in (await session.scalars(
        select(Shipment).where(Shipment.id.in_(shipment_ids))
    )).all()} if shipment_ids else {}
    archive_requests = {item.id: item for item in (await session.scalars(
        select(ArchiveRequest).where(ArchiveRequest.id.in_(request_ids))
    )).all()} if request_ids else {}
    card_ids.update(item.card_id for item in archive_requests.values())
    cards = {item.id: item for item in (await session.scalars(
        select(Card).where(Card.id.in_(card_ids))
    )).all()} if card_ids else {}
    actors = {item.id: item for item in (await session.scalars(
        select(User).where(User.id.in_(actor_ids))
    )).all()} if actor_ids else {}
    locations = {item.id: item.name for item in (await session.scalars(
        select(Location).where(Location.id.in_(location_ids))
    )).all()} if location_ids else {}
    shelves = {item.id: f"{item.section_name} / {item.shelf_name}" for item in (
        await session.scalars(select(ArchiveShelf).where(ArchiveShelf.id.in_(shelf_ids)))
    ).all()} if shelf_ids else {}

    history_items = []
    for event in events:
        payload = event.payload or {}
        if event.aggregate_type == "card":
            card = cards.get(event.aggregate_id)
            subject = f"{card.card_number} · {card.full_name}" if card else "Медицинская карта"
        elif event.aggregate_type == "box":
            box = boxes.get(event.aggregate_id)
            subject = box.box_number if box else payload.get("box_number", "Коробка")
        elif event.aggregate_type == "shipment":
            shipment = shipments.get(event.aggregate_id)
            subject = f"Отправление № {shipment.shipment_number}" if shipment else "Отправление"
        elif event.aggregate_type == "archive_request":
            archive_request = archive_requests.get(event.aggregate_id)
            card = cards.get(archive_request.card_id) if archive_request else None
            subject = f"Заявка: {card.card_number}" if card else "Заявка в архив"
        else:
            subject = event.aggregate_type
        from_id = payload.get("from_location_id")
        to_id = payload.get("to_location_id") or payload.get("location_id")
        try:
            from_id = uuid.UUID(str(from_id)) if from_id else None
        except (TypeError, ValueError):
            from_id = None
        try:
            to_id = uuid.UUID(str(to_id)) if to_id else None
        except (TypeError, ValueError):
            to_id = None
        destination = locations.get(to_id) if to_id else None
        if payload.get("shelf_id"):
            try:
                destination = shelves.get(uuid.UUID(str(payload["shelf_id"])), destination)
            except (TypeError, ValueError):
                pass
        if payload.get("organization"):
            destination = payload["organization"]
        history_items.append({
            "timestamp": event.occurred_at,
            "subject": subject,
            "action": EVENT_LABELS.get(event.event_type, event.event_type),
            "from_location": locations.get(from_id) if from_id else None,
            "to_location": destination,
            "actor": actors[event.actor_user_id].full_name if event.actor_user_id in actors else "—",
        })

    ctx = _dashboard_context(request, user, None)
    ctx.update({
        "history_items": history_items,
        "page": page,
        "has_previous": page > 1,
        "has_next": page * page_size < total,
        "total_events": total,
    })
    return request.app.state.templates.TemplateResponse(
        request=request, name="postgresql_history.html", context=ctx
    )
