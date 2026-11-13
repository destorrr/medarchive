"""PostgreSQL central-office receiving, packing, dispatch and external custody."""

from __future__ import annotations

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    Box,
    BoxCardMembership,
    BoxCycle,
    Card,
    Courier,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.dependencies import get_pg_session, require_pg_role
from medarchive.services.postgresql_card_custody import PostgreSQLCardCustodyWorkflow
from medarchive.services.postgresql_shipment_workflow import PostgreSQLShipmentWorkflow
from medarchive.services.postgresql_receive_queries import exception_shipments


router = APIRouter()
central_only = require_pg_role("central_office")


async def _central_location(session: AsyncSession) -> Location | None:
    return await session.scalar(
        select(Location).where(
            Location.kind == "central_office", Location.is_active.is_(True)
        )
    )


def _context(request: Request, user: User, **extra) -> dict:
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
            {"url": "/central/receive", "label": "📥 Приёмка"},
            {"url": "/central/packing", "label": "📦 Упаковка"},
            {"url": "/central/transfer", "label": "🚚 Отправка"},
            {"url": "/central/custody", "label": "⚖️ Внешнее хранение"},
        ],
    }
    context.update(extra)
    return context


async def _render(
    request: Request, user: User, session: AsyncSession, template: str, **extra
):
    return request.app.state.templates.TemplateResponse(
        request=request, name=template, context=_context(request, user, **extra)
    )


async def _incoming_shipments(session: AsyncSession, destination_id: uuid.UUID):
    rows = (
        await session.execute(
            select(Shipment, Location, Courier)
            .join(Location, Location.id == Shipment.from_location_id)
            .join(Courier, Courier.id == Shipment.courier_id)
            .where(
                Shipment.to_location_id == destination_id,
                Shipment.status == "in_transit",
            )
            .order_by(Shipment.handed_over_at, Shipment.shipment_number)
        )
    ).all()
    incoming = []
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
        manifest_boxes = []
        for box in boxes:
            manifest_boxes.append({
                "box_number": box.box_number,
                "cards": [
                    {"card_number": card.card_number, "full_name": card.full_name}
                    for row, card in manifest_rows if row.box_id == box.id
                ],
            })
        incoming.append({
            "id": shipment.id,
            "number": shipment.shipment_number,
            "source": source.name,
            "courier": courier.full_name,
            "handed_over_at": shipment.handed_over_at,
            "box_count": len(boxes),
            "card_count": len(manifest_rows),
            "boxes": manifest_boxes,
            "standalone_cards": [
                {"card_number": card.card_number, "full_name": card.full_name}
                for row, card in manifest_rows if row.box_id is None
            ],
        })
    return incoming


@router.get("/central/receive")
async def central_receive_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    location = await _central_location(session)
    incoming = await _incoming_shipments(session, location.id) if location else []
    incidents = await exception_shipments(session, location.id) if location else []
    return await _render(
        request, user, session, "postgresql_central_receive.html",
        incoming=incoming, incidents=incidents, error=error, success=message,
    )


@router.post("/central/receive/confirm")
async def central_receive_confirm(
    request: Request,
    shipment_id: uuid.UUID = Form(...),
    confirmed: bool = Form(False),
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    if not confirmed:
        return await central_receive_page(
            request, error="Подтвердите проверку всего отправления без расхождений.",
            user=user, session=session,
        )
    try:
        shipment = await PostgreSQLShipmentWorkflow(session).receive(
            shipment_id=shipment_id, actor_id=user.id
        )
        number = shipment.shipment_number
        await session.commit()
    except ValueError as exc:
        user_id = user.id
        await session.rollback()
        user = await session.get(User, user_id)
        return await central_receive_page(
            request, error=str(exc), user=user, session=session
        )
    except IntegrityError:
        user_id = user.id
        await session.rollback()
        user = await session.get(User, user_id)
        return await central_receive_page(
            request,
            error="Приёмка заблокирована: проверьте манифест и состав отправления.",
            user=user, session=session,
        )
    message = f"Отправление № {number} принято полностью."
    return RedirectResponse(
        url=f"/central/receive?message={quote(message, safe='')}", status_code=303
    )


@router.get("/central/packing")
async def central_packing_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    location = await _central_location(session)
    boxes, cards, box_items = [], [], []
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
        boxes = list((
            await session.scalars(
                select(Box).where(
                    Box.current_location_id == location.id,
                    Box.status == "active",
                    Box.current_shipment_id.is_(None),
                    ~active_manifest_box,
                ).order_by(Box.created_at.desc())
            )
        ).all())
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
                    Card.current_location_id == location.id,
                    Card.current_shipment_id.is_(None),
                    Card.status == "active",
                    Card.custody_status == "available",
                    ~active_membership,
                ).order_by(Card.card_number)
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
    return await _render(
        request, user, session, "postgresql_central_packing.html",
        boxes=boxes, cards=cards, box_items=box_items, error=error, success=message,
    )


@router.post("/central/packing/new-box")
async def central_create_box(
    request: Request,
    card_ids: list[uuid.UUID] = Form(default=[]),
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        location = await _central_location(session)
        if location is None:
            raise ValueError("Локация центрального офиса не настроена")
        box = await PostgreSQLShipmentWorkflow(session).create_box(
            location_id=location.id, actor_id=user.id, card_ids=card_ids
        )
        number = box.box_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await central_packing_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await central_packing_page(
            request, error="Не удалось создать коробку. Проверьте выбранные карты.",
            user=user, session=session,
        )
    message = f"Коробка {number} создана."
    return RedirectResponse(
        url=f"/central/packing?message={quote(message, safe='')}", status_code=303
    )


@router.post("/central/packing/add-card")
async def central_add_card(
    request: Request,
    box_id: uuid.UUID = Form(...),
    card_id: uuid.UUID = Form(...),
    user: User = Depends(central_only),
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
        return await central_packing_page(request, error=str(exc), user=user, session=session)
    return RedirectResponse("/central/packing?message=Карта+добавлена+в+коробку", status_code=303)


@router.post("/central/packing/remove-card")
async def central_remove_card(
    request: Request,
    box_id: uuid.UUID = Form(...),
    card_id: uuid.UUID = Form(...),
    user: User = Depends(central_only),
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
        return await central_packing_page(request, error=str(exc), user=user, session=session)
    return RedirectResponse("/central/packing?message=Карта+извлечена+из+коробки", status_code=303)


@router.post("/central/packing/write-off-box")
async def central_write_off_box(
    request: Request, box_id: uuid.UUID = Form(...),
    user: User = Depends(central_only), session: AsyncSession = Depends(get_pg_session),
):
    try:
        box = await PostgreSQLShipmentWorkflow(session).write_off_box(box_id=box_id, actor_id=user.id)
        number = box.box_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await central_packing_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await central_packing_page(request, error="Списание заблокировано ограничениями целостности.", user=user, session=session)
    message = f"Пустая коробка {number} списана."
    return RedirectResponse(url=f"/central/packing?message={quote(message, safe='')}", status_code=303)


@router.get("/central/transfer")
async def central_transfer_page(
    request: Request,
    message: str | None = None,
    error: str | None = None,
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    location = await _central_location(session)
    ready_boxes, destinations, couriers = [], [], []
    if location is not None:
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
            .join(BoxCardMembership, BoxCardMembership.box_cycle_id == BoxCycle.id)
            .where(
                Box.current_location_id == location.id,
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
        active_membership = (
            select(BoxCardMembership.id)
            .where(
                BoxCardMembership.card_id == Card.id,
                BoxCardMembership.removed_at.is_(None),
            )
            .exists()
        )
        active_manifest_card = (
            select(ShipmentCard.card_id)
            .join(Shipment, Shipment.id == ShipmentCard.shipment_id)
            .where(
                ShipmentCard.card_id == Card.id,
                Shipment.status.in_(("draft", "in_transit")),
            )
            .exists()
        )
        cards = list((
            await session.scalars(
                select(Card).where(
                    Card.current_location_id == location.id,
                    Card.current_shipment_id.is_(None),
                    Card.status == "active",
                    Card.custody_status == "available",
                    ~active_membership,
                    ~active_manifest_card,
                ).order_by(Card.card_number)
            )
        ).all())
        destinations = list((
            await session.scalars(
                select(Location).where(
                    Location.kind.in_(("branch", "archive")),
                    Location.is_active.is_(True),
                ).order_by(Location.kind, Location.name)
            )
        ).all())
        couriers = list((
            await session.scalars(
                select(Courier).where(Courier.is_active.is_(True))
                .order_by(Courier.full_name)
            )
        ).all())
    else:
        cards = []
    return await _render(
        request, user, session, "postgresql_central_transfer.html",
        ready_boxes=ready_boxes, cards=cards,
        destinations=destinations, couriers=couriers,
        error=error, success=message,
    )


@router.post("/central/transfer/confirm")
async def central_transfer_confirm(
    request: Request,
    box_id: str = Form(""),
    card_ids: list[uuid.UUID] = Form(default=[]),
    to_location_id: uuid.UUID = Form(...),
    courier_id: uuid.UUID = Form(...),
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        source = await _central_location(session)
        if source is None:
            raise ValueError("Локация центрального офиса не настроена")
        selected_box_id = uuid.UUID(box_id) if box_id.strip() else None
        if selected_box_id is None and not card_ids:
            raise ValueError("Выберите коробку или хотя бы одну отдельную карту")
        workflow = PostgreSQLShipmentWorkflow(session)
        shipment = await workflow.create_draft(
            from_location_id=source.id,
            to_location_id=to_location_id,
            courier_id=courier_id,
            issued_by=user.id,
        )
        if selected_box_id is not None:
            await workflow.add_box(
                shipment_id=shipment.id, box_id=selected_box_id, actor_id=user.id
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
        return await central_transfer_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        await session.refresh(user)
        return await central_transfer_page(
            request, error="Отправка заблокирована ограничениями целостности.",
            user=user, session=session,
        )
    message = f"Отправление № {number} передано курьеру."
    return RedirectResponse(
        url=f"/central/transfer?message={quote(message, safe='')}", status_code=303
    )


@router.get("/central/custody")
async def central_custody_page(
    request: Request,
    error: str | None = None,
    message: str | None = None,
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    location = await _central_location(session)
    cards = []
    if location is not None:
        active_membership = (
            select(BoxCardMembership.id)
            .where(
                BoxCardMembership.card_id == Card.id,
                BoxCardMembership.removed_at.is_(None),
            )
            .exists()
        )
        cards = list((
            await session.scalars(
                select(Card).where(
                    Card.current_location_id == location.id,
                    Card.current_shipment_id.is_(None),
                    Card.status == "active",
                    ~active_membership,
                ).order_by(Card.card_number)
            )
        ).all())
    return await _render(
        request, user, session, "postgresql_central_custody.html",
        cards=cards, error=error, success=message,
    )


@router.post("/central/custody/mark")
async def central_mark_external(
    request: Request,
    card_id: uuid.UUID = Form(...),
    custody_status: str = Form(...),
    organization: str = Form(...),
    comment: str = Form(""),
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLCardCustodyWorkflow(session).mark_external(
            card_id=card_id,
            actor_id=user.id,
            custody_status=custody_status,
            organization=organization,
            comment=comment,
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await central_custody_page(request, error=str(exc), user=user, session=session)
    return RedirectResponse("/central/custody?message=Внешнее+хранение+отмечено", status_code=303)


@router.post("/central/custody/return")
async def central_return_external(
    request: Request,
    card_id: uuid.UUID = Form(...),
    comment: str = Form(""),
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLCardCustodyWorkflow(session).return_to_central_office(
            card_id=card_id, actor_id=user.id, comment=comment
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        await session.refresh(user)
        return await central_custody_page(request, error=str(exc), user=user, session=session)
    return RedirectResponse("/central/custody?message=Карта+возвращена+на+баланс+ЦО", status_code=303)

@router.post("/central/receive/resolve")
async def central_receive_resolve(
    request: Request,
    shipment_id: uuid.UUID = Form(...),
    confirmed: bool = Form(False),
    resolution_note: str = Form(...),
    user: User = Depends(central_only),
    session: AsyncSession = Depends(get_pg_session),
):
    if not confirmed:
        return await central_receive_page(request, error="Подтвердите проверку полного состава отправления.", user=user, session=session)
    if not resolution_note.strip():
        return await central_receive_page(request, error="Укажите результат проверки отправления", user=user, session=session)
    user_id = user.id
    try:
        shipment = await PostgreSQLShipmentWorkflow(session).resolve_exception(
            shipment_id=shipment_id, actor_id=user.id, resolution_note=resolution_note,
        )
        number = shipment.shipment_number
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        user = await session.get(User, user_id)
        return await central_receive_page(request, error=str(exc), user=user, session=session)
    except IntegrityError:
        await session.rollback()
        user = await session.get(User, user_id)
        return await central_receive_page(request, error="Разрешение инцидента заблокировано: проверьте манифест и состав отправления.", user=user, session=session)
    message = f"Инцидент по отправлению № {number} разрешён, отправление принято полностью."
    return RedirectResponse(url=f"/central/receive?message={quote(message, safe='')}", status_code=303)
