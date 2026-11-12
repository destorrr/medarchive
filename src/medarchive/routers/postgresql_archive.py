"""PostgreSQL archive receiving, storage and request fulfilment pages."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    ArchiveShelf,
    Box,
    BoxCardMembership,
    BoxCycle,
    BoxPlacement,
    Card,
    CardPlacement,
    Courier,
    DomainEvent,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.dependencies import get_pg_session, require_pg_role
from medarchive.services.postgresql_archive_workflow import PostgreSQLArchiveWorkflow
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow


router = APIRouter()
archive_only = require_pg_role("archive")
central_only = require_pg_role("central_office")


async def _archive_location(session: AsyncSession) -> Location | None:
    return await session.scalar(
        select(Location).where(
            Location.kind == "archive", Location.is_active.is_(True)
        )
    )


def _archive_context(request: Request, user: User, **extra) -> dict:
    initials = "".join(part[0] for part in (user.full_name or "").split()[:2]).upper()
    if not initials:
        initials = user.username[:1].upper()
    context = {
        "request": request,
        "username": user.username,
        "user_login": user.username,
        "user_full_name": user.full_name or user.username,
        "user_initials": initials,
        "user_branch": "Архив",
        "user_role_pill": "Архив",
        "role_label": "Архив",
        "menu_items": [
            {"url": "/dashboard", "label": "📊 Панель управления"},
            {"url": "/search", "label": "🔍 Поиск"},
            {"url": "/receive", "label": "📥 Приёмка"},
            {"url": "/placement", "label": "🗂 Размещение"},
            {"url": "/requests", "label": "📋 Заявки ЦО"},
        ],
    }
    context.update(extra)
    return context


async def _render_page(
    request: Request, user: User, session: AsyncSession, template: str, **extra
):
    context = _archive_context(request, user, **extra)
    return request.app.state.templates.TemplateResponse(
        request=request, name=template, context=context
    )


@router.get("/receive")
async def receive_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    archive = await _archive_location(session)
    incoming = []
    if archive is not None:
        rows = (
            await session.execute(
                select(Shipment, Location, Courier)
                .join(Location, Location.id == Shipment.from_location_id)
                .join(Courier, Courier.id == Shipment.courier_id)
                .where(
                    Shipment.to_location_id == archive.id,
                    Shipment.status == "in_transit",
                )
                .order_by(Shipment.handed_over_at, Shipment.shipment_number)
            )
        ).all()
        for shipment, source, courier in rows:
            box_count = int(
                await session.scalar(
                    select(func.count()).select_from(ShipmentBox).where(
                        ShipmentBox.shipment_id == shipment.id
                    )
                ) or 0
            )
            manifest_rows = (
                await session.execute(
                    select(ShipmentCard, Card)
                    .join(Card, Card.id == ShipmentCard.card_id)
                    .where(ShipmentCard.shipment_id == shipment.id)
                    .order_by(Card.card_number)
                )
            ).all()
            card_count = len(manifest_rows)
            boxes = list((
                await session.scalars(
                    select(Box)
                    .join(ShipmentBox, ShipmentBox.box_id == Box.id)
                    .where(ShipmentBox.shipment_id == shipment.id)
                    .order_by(Box.box_number)
                )
            ).all())
            manifest_boxes = []
            for box in boxes:
                members = [
                    {"card_number": card.card_number, "full_name": card.full_name}
                    for row, card in manifest_rows if row.box_id == box.id
                ]
                manifest_boxes.append({
                    "box_number": box.box_number,
                    "cards": members,
                })
            standalone_cards = [
                {"card_number": card.card_number, "full_name": card.full_name}
                for row, card in manifest_rows if row.box_id is None
            ]
            incoming.append({
                "id": shipment.id,
                "number": shipment.shipment_number,
                "source": source.name,
                "courier": courier.full_name,
                "handed_over_at": shipment.handed_over_at,
                "box_count": box_count,
                "card_count": card_count,
                "boxes": manifest_boxes,
                "standalone_cards": standalone_cards,
            })
    return await _render_page(
        request, user, session, "postgresql_receive.html",
        incoming=incoming, error=error, success=message,
    )


@router.post("/receive/confirm")
async def receive_confirm(
    request: Request,
    shipment_id: uuid.UUID = Form(...),
    confirmed: bool = Form(False),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    if not confirmed:
        return await receive_page(
            request, error="Приёмка возможна только после проверки всего отправления целиком.",
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
        return await receive_page(
            request, error=str(exc), user=user, session=session
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await receive_page(
            request,
            error="Приёмка заблокирована ограничениями целостности. Проверьте состав отправления.",
            user=user, session=session,
        )
    message = f"Отправление № {number} принято полностью."
    return RedirectResponse(
        url=f"/receive?message={quote(message, safe='')}", status_code=303
    )


@router.get("/placement")
async def placement_page(
    request: Request,
    error: str | None = None,
    message: str | None = None,
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    archive = await _archive_location(session)
    boxes, cards, shelves, box_items = [], [], [], []
    if archive is not None:
        active_box_placement = select(BoxPlacement.id).where(
            BoxPlacement.box_id == Box.id, BoxPlacement.removed_at.is_(None)
        ).exists()
        box_rows = (
            await session.execute(
                select(Box).where(
                    Box.current_location_id == archive.id,
                    Box.status == "active",
                    ~active_box_placement,
                    ~active_manifest_box,
                ).order_by(Box.box_number)
            )
        ).scalars().all()
        boxes = list(box_rows)

        active_card_placement = select(CardPlacement.id).where(
            CardPlacement.card_id == Card.id, CardPlacement.removed_at.is_(None)
        ).exists()
        active_membership = (
            select(BoxCardMembership.id)
            .join(BoxCycle, BoxCycle.id == BoxCardMembership.box_cycle_id)
            .where(
                BoxCardMembership.card_id == Card.id,
                BoxCardMembership.removed_at.is_(None),
                BoxCycle.ended_at.is_(None),
            )
            .exists()
        )
        cards = list((
            await session.scalars(
                select(Card).where(
                    Card.current_location_id == archive.id,
                    Card.status == "active",
                    ~active_card_placement,
                    ~active_membership,
                    ~active_manifest_card,
                ).order_by(Card.card_number)
            )
        ).all())
        shelves = list((
            await session.scalars(
                select(ArchiveShelf).where(ArchiveShelf.is_active.is_(True))
                .order_by(ArchiveShelf.section_name, ArchiveShelf.shelf_name)
            )
        ).all())
        for box in boxes:
            box_cards = list((
                await session.scalars(
                    select(Card)
                    .join(BoxCardMembership, BoxCardMembership.card_id == Card.id)
                    .join(BoxCycle, BoxCycle.id == BoxCardMembership.box_cycle_id)
                    .where(
                        BoxCycle.box_id == box.id,
                        BoxCycle.ended_at.is_(None),
                        BoxCardMembership.removed_at.is_(None),
                    ).order_by(Card.card_number)
                )
            ).all())
            box_items.append({"box": box, "cards": box_cards})

    placed_boxes = []
    placed_cards = []
    if archive is not None:
        placed_boxes = (
            await session.execute(
                select(Box, BoxPlacement, ArchiveShelf)
                .join(BoxPlacement, BoxPlacement.box_id == Box.id)
                .join(ArchiveShelf, ArchiveShelf.id == BoxPlacement.shelf_id)
                .where(
                    Box.current_location_id == archive.id,
                    BoxPlacement.removed_at.is_(None),
                ).order_by(Box.box_number)
            )
        ).all()
        placed_cards = (
            await session.execute(
                select(Card, CardPlacement, ArchiveShelf)
                .join(CardPlacement, CardPlacement.card_id == Card.id)
                .join(ArchiveShelf, ArchiveShelf.id == CardPlacement.shelf_id)
                .where(
                    Card.current_location_id == archive.id,
                    CardPlacement.removed_at.is_(None),
                ).order_by(Card.card_number)
            )
        ).all()
    return await _render_page(
        request, user, session, "postgresql_placement.html",
        boxes=boxes, cards=cards, shelves=shelves, box_items=box_items,
        placed_boxes=placed_boxes, placed_cards=placed_cards,
        error=error, success=message,
    )


@router.post("/placement/add-card")
async def archive_add_card_to_internal_box(
    request: Request,
    box_id: uuid.UUID = Form(...),
    card_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLShipmentWorkflow(session).add_card_to_box(
            box_id=box_id, card_id=card_id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(
            request, error="Нельзя изменить состав размещённой или зарезервированной коробки.",
            user=user, session=session,
        )
    return RedirectResponse("/placement?message=Карта+добавлена+во+внутреннюю+коробку", status_code=303)


@router.post("/placement/remove-card-from-box")
async def archive_remove_card_from_internal_box(
    request: Request,
    box_id: uuid.UUID = Form(...),
    card_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
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
        return await placement_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(
            request, error="Сначала снимите коробку с архивной полки.",
            user=user, session=session,
        )
    return RedirectResponse("/placement?message=Карта+извлечена+из+внутренней+коробки", status_code=303)


@router.post("/placement/new-internal-box")
async def create_internal_box(
    request: Request,
    card_ids: list[uuid.UUID] = Form(default=[]),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        archive = await _archive_location(session)
        if archive is None:
            raise ValueError("Архивная локация не настроена")
        box = await PostgreSQLShipmentWorkflow(session).create_box(
            location_id=archive.id, actor_id=user.id, card_ids=card_ids
        )
        number = box.box_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(
            request, error="Не удалось создать внутреннюю коробку. Проверьте выбранные карты.",
            user=user, session=session,
        )
    message = f"Внутренняя коробка {number} создана."
    return RedirectResponse(
        url=f"/placement?message={quote(message, safe='')}", status_code=303
    )


@router.post("/placement/box")
async def place_box(
    request: Request,
    box_id: uuid.UUID = Form(...),
    shelf_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLArchiveWorkflow(session).place_box(
            box_id=box_id, shelf_id=shelf_id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(
            request, error="Коробка уже размещена или полка занята.", user=user, session=session
        )
    return RedirectResponse("/placement?message=Коробка+размещена", status_code=303)


@router.post("/placement/card")
async def place_card(
    request: Request,
    card_id: uuid.UUID = Form(...),
    shelf_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLArchiveWorkflow(session).place_card(
            card_id=card_id, shelf_id=shelf_id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(
            request, error="Карта уже размещена или полка занята.", user=user, session=session
        )
    return RedirectResponse("/placement?message=Карта+размещена", status_code=303)


@router.post("/placement/remove-box")
async def remove_box_from_shelf(
    request: Request,
    box_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLArchiveWorkflow(session).remove_box_from_shelf(
            box_id=box_id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(request, error=str(exc), user=user, session=session)
    return RedirectResponse("/placement?message=Коробка+снята+с+полки", status_code=303)


@router.post("/placement/remove-card")
async def remove_card_from_shelf(
    request: Request,
    card_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLArchiveWorkflow(session).remove_card_from_shelf(
            card_id=card_id, actor_id=user.id
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await placement_page(request, error=str(exc), user=user, session=session)
    return RedirectResponse("/placement?message=Карта+снята+с+полки", status_code=303)


@router.get("/requests")
async def archive_requests_page(
    request: Request,
    error: str | None = None,
    message: str | None = None,
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    rows = (
        await session.execute(
            select(ArchiveRequest, Card, User)
            .join(Card, Card.id == ArchiveRequest.card_id)
            .join(User, User.id == ArchiveRequest.requested_by)
            .where(ArchiveRequest.status.in_(("new", "in_progress")))
            .order_by(ArchiveRequest.created_at, ArchiveRequest.id)
        )
    ).all()
    requests = []
    for archive_request, card, requester in rows:
        membership_row = await session.execute(
            select(BoxCardMembership, Box, BoxPlacement)
            .join(BoxCycle, BoxCycle.id == BoxCardMembership.box_cycle_id)
            .join(Box, Box.id == BoxCycle.box_id)
            .outerjoin(
                BoxPlacement,
                (BoxPlacement.box_id == Box.id) & BoxPlacement.removed_at.is_(None),
            )
            .where(
                BoxCardMembership.card_id == card.id,
                BoxCardMembership.removed_at.is_(None),
                BoxCycle.ended_at.is_(None),
            ).limit(1)
        )
        membership = membership_row.first()
        placement = await session.scalar(
            select(CardPlacement).where(
                CardPlacement.card_id == card.id,
                CardPlacement.removed_at.is_(None),
            )
        )
        requests.append({
            "request": archive_request,
            "card": card,
            "requester": requester,
            "box": membership[1] if membership else None,
            "box_placement": membership[2] if membership else None,
            "card_placement": placement,
        })
    couriers = list((
        await session.scalars(
            select(Courier).where(Courier.is_active.is_(True)).order_by(Courier.full_name)
        )
    ).all())
    return await _render_page(
        request, user, session, "postgresql_requests.html",
        requests=requests, couriers=couriers, error=error, success=message,
    )


@router.post("/requests/not-found")
async def archive_request_not_found(
    request: Request,
    request_id: uuid.UUID = Form(...),
    note: str = Form(...),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLArchiveWorkflow(session).mark_not_found(
            request_id=request_id, actor_id=user.id, note=note
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await archive_requests_page(
            request, error=str(exc), user=user, session=session
        )
    return RedirectResponse("/requests?message=Заявка+закрыта%3A+карта+не+найдена", status_code=303)


@router.post("/requests/unpack-card")
async def unpack_requested_card(
    request: Request,
    box_id: uuid.UUID = Form(...),
    card_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
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
        return await archive_requests_page(
            request, error=str(exc), user=user, session=session
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await archive_requests_page(
            request,
            error="Сначала снимите коробку с архивной полки.",
            user=user, session=session,
        )
    return RedirectResponse("/requests?message=Карта+извлечена+из+коробки", status_code=303)


@router.post("/requests/fulfill")
async def fulfill_archive_requests(
    request: Request,
    card_ids: list[uuid.UUID] = Form(...),
    courier_id: uuid.UUID = Form(...),
    user: User = Depends(archive_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        archive = await _archive_location(session)
        central_office = await session.scalar(
            select(Location).where(
                Location.kind == "central_office", Location.is_active.is_(True)
            )
        )
        if archive is None or central_office is None:
            raise ValueError("Не настроены локации архива или центрального офиса")
        workflow = PostgreSQLShipmentWorkflow(session)
        shipment = await workflow.create_draft(
            from_location_id=archive.id,
            to_location_id=central_office.id,
            courier_id=courier_id,
            issued_by=user.id,
        )
        for card_id in card_ids:
            await workflow.add_standalone_card(
                shipment_id=shipment.id, card_id=card_id, actor_id=user.id
            )
        await workflow.hand_over(shipment_id=shipment.id, actor_id=user.id)
        number = shipment.shipment_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await archive_requests_page(
            request, error=str(exc), user=user, session=session
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await archive_requests_page(
            request,
            error="Отправка заблокирована: проверьте полки, коробки и состав заявок.",
            user=user, session=session,
        )
    message = f"Отправление № {number} с отдельными картами передано курьеру."
    return RedirectResponse(
        url=f"/requests?message={quote(message, safe='')}", status_code=303
    )


def _central_requests_context(request: Request, user: User, **extra) -> dict:
    initials = "".join(part[0] for part in (user.full_name or "").split()[:2]).upper()
    if not initials:
        initials = user.username[:1].upper()
    context = {
        "request": request,
        "username": user.username,
        "user_login": user.username,
        "user_full_name": user.full_name or user.username,
        "user_initials": initials,
        "user_branch": "Центральный офис",
        "user_role_pill": "Центральный офис",
        "role_label": "Центральный офис",
        "menu_items": [
            {"url": "/dashboard", "label": "📊 Панель управления"},
            {"url": "/search", "label": "🔍 Поиск"},
            {"url": "/requests/new", "label": "📋 Запросить карты из архива"},
        ],
    }
    context.update(extra)
    return context


@router.get("/requests/new")
async def central_archive_request_page(
    request: Request,
    query: str | None = None,
    error: str | None = None,
    message: str | None = None,
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    archive = await _archive_location(session)
    cards = []
    query = (query or "").strip()
    if archive is not None and query:
        active_request = (
            select(ArchiveRequest.id)
            .where(
                ArchiveRequest.card_id == Card.id,
                ArchiveRequest.status.in_(("new", "in_progress")),
            )
            .exists()
        )
        cards = list((
            await session.scalars(
                select(Card).where(
                    Card.current_location_id == archive.id,
                    Card.current_shipment_id.is_(None),
                    Card.status == "active",
                    or_(
                        Card.card_number.ilike(f"%{query}%"),
                        Card.full_name.ilike(f"%{query}%"),
                    ),
                    ~active_request,
                ).order_by(Card.card_number).limit(100)
            )
        ).all())
    context = _central_requests_context(
        request, user, query=query, cards=cards, error=error, success=message
    )
    return request.app.state.templates.TemplateResponse(
        request=request, name="postgresql_central_requests.html", context=context
    )


@router.post("/requests/create")
async def central_create_archive_requests(
    request: Request,
    card_ids: list[uuid.UUID] = Form(...),
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        created = await PostgreSQLArchiveWorkflow(session).create_requests(
            card_ids=card_ids, actor_id=user.id
        )
        count = len(created)
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await central_archive_request_page(
            request, error=str(exc), user=user, session=session
        )
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await central_archive_request_page(
            request,
            error="Не удалось создать заявки: одна из карт уже запрошена или изменила местонахождение.",
            user=user, session=session,
        )
    message = f"Создано заявок: {count}."
    return RedirectResponse(
        url=f"/requests/new?message={quote(message, safe='')}", status_code=303
    )
