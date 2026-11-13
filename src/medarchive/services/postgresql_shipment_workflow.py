"""Transactional shipment workflow for the PostgreSQL target model.

The caller owns the transaction (Database.transaction()). Repositories and this
workflow never commit independently.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    Box,
    BoxCardMembership,
    BoxCycle,
    BoxPlacement,
    Card,
    Courier,
    DomainEvent,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)


class ShipmentWorkflowError(ValueError):
    """A requested logistics transition violates a business rule."""


class PostgreSQLShipmentWorkflow:
    """Create, hand over and receive whole shipments atomically."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_box(
        self,
        *,
        location_id: uuid.UUID,
        actor_id: uuid.UUID,
        card_ids: list[uuid.UUID] | None = None,
    ) -> Box:
        """Create a box and its first cycle; archive boxes must be request-backed."""
        actor = await self._active_user(actor_id)
        location = await self._location(location_id)
        await self._authorize_location(actor, location)
        selected_ids = list(card_ids or [])
        if len(selected_ids) != len(set(selected_ids)):
            raise ShipmentWorkflowError("Одна и та же карта указана в коробке несколько раз")
        cards: list[Card] = []
        for card_id in selected_ids:
            card = await self.session.scalar(
                select(Card).where(Card.id == card_id).with_for_update()
            )
            if card is not None and card.custody_status != "available":
                raise ShipmentWorkflowError(
                    "Карта передана во внешнюю организацию и недоступна для внутреннего перемещения"
                )
            if (
                card is None or card.status != "active"
                or card.current_location_id != location.id
                or card.current_shipment_id is not None
            ):
                raise ShipmentWorkflowError("Карта не найдена в указанном месте или недоступна")
            if await self._active_membership(card.id) is not None:
                raise ShipmentWorkflowError("Карта уже находится в другой активной коробке")
            if await self._has_active_manifest_card(card.id):
                raise ShipmentWorkflowError("Карта уже включена в активное отправление")
            cards.append(card)

        box = Box(
            box_number=f"BX-{uuid.uuid4().hex[:16].upper()}",
            current_location_id=location.id,
            status="active",
            created_by=actor.id,
        )
        self.session.add(box)
        await self.session.flush()
        cycle = BoxCycle(box_id=box.id, cycle_number=1, started_by=actor.id)
        self.session.add(cycle)
        await self.session.flush()
        for card in cards:
            self.session.add(BoxCardMembership(
                box_cycle_id=cycle.id, card_id=card.id, added_by=actor.id,
            ))
        await self.session.flush()
        await self._event("box.created", "box", box.id, actor.id, {
            "box_number": box.box_number,
            "location_id": str(location.id),
            "initial_card_ids": [str(card.id) for card in cards],
        })
        for card in cards:
            await self._event("card.added_to_box", "card", card.id, actor.id, {
                "box_id": str(box.id), "location_id": str(location.id),
            })
        return box

    async def write_off_box(self, *, box_id: uuid.UUID, actor_id: uuid.UUID) -> Box:
        """Write off an empty, stationary box while preserving its lifecycle history."""
        actor = await self._active_user(actor_id)
        box = await self.session.scalar(select(Box).where(Box.id == box_id).with_for_update())
        if box is None or box.status != "active":
            raise ShipmentWorkflowError("Активная коробка не найдена или уже списана")
        if box.current_location_id is None or box.current_shipment_id is not None:
            raise ShipmentWorkflowError("Нельзя списать коробку, находящуюся в пути")
        location = await self._location(box.current_location_id)
        await self._authorize_location(actor, location)
        if await self._has_active_manifest_box(box.id):
            raise ShipmentWorkflowError("Нельзя списать коробку, включённую в активное отправление")
        cycle = await self.session.scalar(select(BoxCycle).where(
            BoxCycle.box_id == box.id, BoxCycle.ended_at.is_(None)
        ).with_for_update())
        if cycle is None:
            raise ShipmentWorkflowError("У коробки отсутствует открытый цикл использования")
        membership = await self.session.scalar(select(BoxCardMembership.id).where(
            BoxCardMembership.box_cycle_id == cycle.id,
            BoxCardMembership.removed_at.is_(None),
        ).limit(1))
        if membership is not None:
            raise ShipmentWorkflowError("Списать можно только пустую коробку")
        placement = await self.session.scalar(select(BoxPlacement).where(
            BoxPlacement.box_id == box.id, BoxPlacement.removed_at.is_(None)
        ).with_for_update())
        if placement is not None:
            raise ShipmentWorkflowError("Сначала снимите коробку с архивной полки")
        now = datetime.now(timezone.utc)
        cycle.ended_at = now
        cycle.ended_by = actor.id
        box.status = "written_off"
        box.updated_at = now
        await self.session.flush()
        await self._event("box.written_off", "box", box.id, actor.id, {
            "box_number": box.box_number, "location_id": str(location.id),
            "cycle_id": str(cycle.id), "cycle_number": cycle.cycle_number,
        })
        return box

    async def add_card_to_box(
        self, *, box_id: uuid.UUID, card_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        actor = await self._active_user(actor_id)
        box = await self.session.scalar(
            select(Box).where(Box.id == box_id).with_for_update()
        )
        if box is None or box.status != "active" or box.current_location_id is None:
            raise ShipmentWorkflowError("Коробка не найдена или не находится в постоянной локации")
        location = await self._location(box.current_location_id)
        await self._authorize_location(actor, location)
        if await self._has_active_manifest_box(box.id):
            raise ShipmentWorkflowError("Коробка уже включена в активное отправление")
        card = await self.session.scalar(
            select(Card).where(Card.id == card_id).with_for_update()
        )
        if (
            card is None or card.status != "active" or card.custody_status != "available"
            or card.current_location_id != location.id
            or card.current_shipment_id is not None
        ):
            raise ShipmentWorkflowError("Карта не найдена в месте хранения коробки или недоступна")
        if await self._active_membership(card.id) is not None:
            raise ShipmentWorkflowError("Карта уже находится в активной коробке")
        if await self._has_active_manifest_card(card.id):
            raise ShipmentWorkflowError("Карта уже включена в активное отправление")
        # Archive staff may repack cards for internal storage. The separate
        # archive -> central-office route is kept unboxed by add_box(),
        # hand_over(), and PostgreSQL manifest guards.
        cycle = await self.session.scalar(
            select(BoxCycle)
            .where(BoxCycle.box_id == box.id, BoxCycle.ended_at.is_(None))
            .with_for_update()
        )
        if cycle is None:
            last_number = await self.session.scalar(
                select(func.max(BoxCycle.cycle_number)).where(BoxCycle.box_id == box.id)
            )
            cycle = BoxCycle(
                box_id=box.id, cycle_number=(last_number or 0) + 1,
                started_by=actor.id,
            )
            self.session.add(cycle)
            await self.session.flush()
            await self._event("box.cycle_started", "box", box.id, actor.id, {
                "cycle_id": str(cycle.id), "cycle_number": cycle.cycle_number,
            })
        self.session.add(BoxCardMembership(
            box_cycle_id=cycle.id, card_id=card.id, added_by=actor.id,
        ))
        await self.session.flush()
        await self._event("card.added_to_box", "card", card.id, actor.id, {
            "box_id": str(box.id), "location_id": str(location.id),
        })

    async def remove_card_from_box(
        self, *, box_id: uuid.UUID, card_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        """Remove a card from the current box cycle without deleting history."""
        actor = await self._active_user(actor_id)
        box = await self.session.scalar(
            select(Box).where(Box.id == box_id).with_for_update()
        )
        if box is None or box.status != "active" or box.current_location_id is None:
            raise ShipmentWorkflowError("Коробка не найдена или недоступна")
        location = await self._location(box.current_location_id)
        await self._authorize_location(actor, location)
        if await self._has_active_manifest_box(box.id):
            raise ShipmentWorkflowError("Нельзя менять состав коробки в активном отправлении")
        cycle = await self.session.scalar(
            select(BoxCycle).where(
                BoxCycle.box_id == box.id, BoxCycle.ended_at.is_(None)
            ).with_for_update()
        )
        if cycle is None:
            raise ShipmentWorkflowError("У коробки нет открытого цикла")
        membership = await self.session.scalar(
            select(BoxCardMembership).where(
                BoxCardMembership.box_cycle_id == cycle.id,
                BoxCardMembership.card_id == card_id,
                BoxCardMembership.removed_at.is_(None),
            ).with_for_update()
        )
        if membership is None:
            raise ShipmentWorkflowError("Карта не числится в этой коробке")
        card = await self.session.scalar(
            select(Card).where(Card.id == card_id).with_for_update()
        )
        if card is None or card.current_location_id != location.id or card.current_shipment_id is not None:
            raise ShipmentWorkflowError("Местонахождение карты не совпадает с коробкой")
        now = datetime.now(timezone.utc)
        membership.removed_at = now
        membership.removed_by = actor.id
        await self.session.flush()
        remaining = await self.session.scalar(
            select(BoxCardMembership.id).where(
                BoxCardMembership.box_cycle_id == cycle.id,
                BoxCardMembership.removed_at.is_(None),
            ).limit(1)
        )
        if remaining is None:
            cycle.ended_at = now
            cycle.ended_by = actor.id
            await self.session.flush()
            await self._event("box.cycle_closed", "box", box.id, actor.id, {
                "cycle_id": str(cycle.id), "cycle_number": cycle.cycle_number,
            })
        await self._event("card.removed_from_box", "card", card.id, actor.id, {
            "box_id": str(box.id), "location_id": str(location.id),
        })

    async def create_draft(
        self,
        *,
        from_location_id: uuid.UUID,
        to_location_id: uuid.UUID,
        courier_id: uuid.UUID,
        issued_by: uuid.UUID,
    ) -> Shipment:
        actor = await self._active_user(issued_by)
        source, target = await self._locations(from_location_id, to_location_id)
        if source.id == target.id:
            raise ShipmentWorkflowError("Отправитель и получатель должны различаться")
        allowed_routes = {
            ("branch", "central_office"),
            ("branch", "archive"),
            ("central_office", "branch"),
            ("central_office", "archive"),
            ("archive", "central_office"),
        }
        if (source.kind, target.kind) not in allowed_routes:
            raise ShipmentWorkflowError("Такой маршрут отправления не разрешён")
        await self._authorize_location(actor, source)
        courier = await self.session.scalar(
            select(Courier).where(Courier.id == courier_id).with_for_update()
        )
        if courier is None or not courier.is_active:
            raise ShipmentWorkflowError("Курьер не найден или неактивен")

        shipment = Shipment(
            from_location_id=source.id,
            to_location_id=target.id,
            courier_id=courier.id,
            issued_by=actor.id,
            status="draft",
        )
        self.session.add(shipment)
        await self.session.flush()
        await self._event("shipment.created", "shipment", shipment.id, actor.id, {
            "from_location_id": str(source.id),
            "to_location_id": str(target.id),
            "courier_id": str(courier.id),
        })
        return shipment

    async def add_box(self, *, shipment_id: uuid.UUID, box_id: uuid.UUID, actor_id: uuid.UUID) -> None:
        shipment, actor = await self._draft_and_actor(shipment_id, actor_id)
        source, target = await self._locations(
            shipment.from_location_id, shipment.to_location_id
        )
        await self._authorize_location(actor, source)
        if source.kind == "archive" and target.kind == "central_office":
            raise ShipmentWorkflowError("По заявкам ЦО архив отправляет только отдельные карты, без коробок")
        box = await self.session.scalar(
            select(Box).where(Box.id == box_id).with_for_update()
        )
        if box is None or box.status != "active":
            raise ShipmentWorkflowError("Коробка не найдена или недоступна для отправки")
        if box.current_location_id != shipment.from_location_id or box.current_shipment_id is not None:
            raise ShipmentWorkflowError("Коробка не находится в месте отправления")
        existing = await self.session.scalar(
            select(ShipmentBox)
            .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
            .where(
                ShipmentBox.box_id == box_id,
                Shipment.status.in_(("draft", "in_transit")),
            )
        )
        if existing is not None:
            raise ShipmentWorkflowError("Коробка уже включена в другое активное отправление")
        cycle = await self.session.scalar(
            select(BoxCycle).where(BoxCycle.box_id == box.id, BoxCycle.ended_at.is_(None))
        )
        if cycle is None:
            raise ShipmentWorkflowError("У коробки нет открытого цикла")
        members = list((await self.session.scalars(
            select(BoxCardMembership).where(
                BoxCardMembership.box_cycle_id == cycle.id,
                BoxCardMembership.removed_at.is_(None),
            )
        )).all())
        if not members:
            raise ShipmentWorkflowError("Нельзя добавить пустую коробку в отправление")
        member_ids = [member.card_id for member in members]
        already_added = await self.session.scalar(
            select(ShipmentCard.card_id)
            .join(Shipment, Shipment.id == ShipmentCard.shipment_id)
            .where(
                ShipmentCard.card_id.in_(member_ids),
                Shipment.status.in_(("draft", "in_transit")),
            )
            .limit(1)
        )
        if already_added is not None:
            raise ShipmentWorkflowError("Карта из коробки уже включена в другое активное отправление")
        self.session.add(ShipmentBox(shipment_id=shipment.id, box_id=box.id))
        await self.session.flush()

    async def add_standalone_card(
        self, *, shipment_id: uuid.UUID, card_id: uuid.UUID, actor_id: uuid.UUID
    ) -> None:
        shipment, actor = await self._draft_and_actor(shipment_id, actor_id)
        await self._authorize_location(actor, await self._location(shipment.from_location_id))
        card = await self.session.scalar(
            select(Card).where(Card.id == card_id).with_for_update()
        )
        if card is not None and card.custody_status != "available":
            raise ShipmentWorkflowError(
                "Карта передана во внешнюю организацию и недоступна для внутреннего перемещения"
            )
        if card is None or card.status != "active":
            raise ShipmentWorkflowError("Карта не найдена или недоступна для отправки")
        if card.current_location_id != shipment.from_location_id or card.current_shipment_id is not None:
            raise ShipmentWorkflowError("Карта не находится в месте отправления")
        membership = await self._active_membership(card.id)
        if membership is not None:
            raise ShipmentWorkflowError("Карта находится в коробке; отправьте коробку целиком")
        existing = await self.session.scalar(
            select(ShipmentCard)
            .join(Shipment, Shipment.id == ShipmentCard.shipment_id)
            .where(
                ShipmentCard.card_id == card.id,
                Shipment.status.in_(("draft", "in_transit")),
            )
        )
        if existing is not None:
            raise ShipmentWorkflowError("Карта уже включена в другое активное отправление")
        self.session.add(ShipmentCard(shipment_id=shipment.id, card_id=card.id, box_id=None))
        await self.session.flush()

    async def hand_over(self, *, shipment_id: uuid.UUID, actor_id: uuid.UUID) -> Shipment:
        shipment, actor = await self._draft_and_actor(shipment_id, actor_id)
        source, target = await self._locations(
            shipment.from_location_id, shipment.to_location_id
        )
        await self._authorize_location(actor, source)

        boxes = list((await self.session.scalars(
            select(Box)
            .join(ShipmentBox, ShipmentBox.box_id == Box.id)
            .where(ShipmentBox.shipment_id == shipment.id)
            .order_by(Box.id)
            .with_for_update()
        )).all())
        standalone_rows = list((await self.session.scalars(
            select(ShipmentCard)
            .where(ShipmentCard.shipment_id == shipment.id, ShipmentCard.box_id.is_(None))
        )).all())
        if not boxes and not standalone_rows:
            raise ShipmentWorkflowError("Нельзя отправить пустое отправление")
        target = await self._location(shipment.to_location_id)
        if source.kind == "archive" and target.kind == "central_office" and boxes:
            raise ShipmentWorkflowError("По заявкам ЦО архив отправляет только отдельные карты, без коробок")

        # Lock and validate every active member of every shipped box. Cards inside
        # boxes are represented in the manifest, but are not separate cargo units.
        boxed_cards: list[tuple[Card, uuid.UUID]] = []
        for box in boxes:
            if box.status != "active" or box.current_location_id != source.id or box.current_shipment_id is not None:
                raise ShipmentWorkflowError("Коробка изменила местонахождение или недоступна")
            cycle = await self.session.scalar(
                select(BoxCycle).where(BoxCycle.box_id == box.id, BoxCycle.ended_at.is_(None)).with_for_update()
            )
            if cycle is None:
                raise ShipmentWorkflowError(f"У коробки {box.box_number} нет открытого цикла")
            memberships = list((await self.session.scalars(
                select(BoxCardMembership)
                .where(BoxCardMembership.box_cycle_id == cycle.id, BoxCardMembership.removed_at.is_(None))
                .order_by(BoxCardMembership.card_id)
                .with_for_update()
            )).all())
            if not memberships:
                raise ShipmentWorkflowError(f"Нельзя отправить пустую коробку {box.box_number}")
            for membership in memberships:
                card = await self.session.scalar(
                    select(Card).where(Card.id == membership.card_id).with_for_update()
                )
                if card is None or card.status != "active" or card.custody_status != "available" or card.current_location_id != source.id or card.current_shipment_id is not None:
                    raise ShipmentWorkflowError("Карта в коробке изменила местонахождение или недоступна")
                boxed_cards.append((card, box.id))

        for row in standalone_rows:
            card = await self.session.scalar(
                select(Card).where(Card.id == row.card_id).with_for_update()
            )
            if card is None or card.status != "active" or card.custody_status != "available" or card.current_location_id != source.id or card.current_shipment_id is not None:
                raise ShipmentWorkflowError("Отдельная карта изменила местонахождение или недоступна")
            if await self._active_membership(card.id) is not None:
                raise ShipmentWorkflowError("Нельзя отправить отдельно карту, находящуюся в коробке")

        # Archive issues to the central office are allowed only for cards covered
        # by active central-office requests. The request remains open until receipt.
        if source.kind == "archive" and target.kind == "central_office":
            card_ids = [card.id for card, _ in boxed_cards] + [row.card_id for row in standalone_rows]
            now = datetime.now(timezone.utc)
            for card_id in card_ids:
                request = await self.session.scalar(
                    select(ArchiveRequest)
                    .join(User, User.id == ArchiveRequest.requested_by)
                    .where(
                        ArchiveRequest.card_id == card_id,
                        ArchiveRequest.status == "new",
                        ArchiveRequest.fulfillment_shipment_id.is_(None),
                        User.role == "central_office",
                    )
                    .with_for_update()
                )
                if request is None:
                    raise ShipmentWorkflowError("Для каждой выдаваемой карты нужна свободная активная заявка ЦО")
                request.status = "in_progress"
                request.fulfillment_shipment_id = shipment.id
                request.updated_at = now

        # Ensure each transported card has exactly one manifest row and that its
        # containing box is explicit. A duplicate is rejected by the PK as well.
        for card, box_id in boxed_cards:
            self.session.add(ShipmentCard(shipment_id=shipment.id, card_id=card.id, box_id=box_id))

        # Persist the complete manifest while the shipment is still a draft.
        # The deferred composite FKs permit the card/box transit pointers to be
        # updated in the following flush, within this same outer transaction.
        await self.session.flush()

        now = datetime.now(timezone.utc)
        for box in boxes:
            box.current_location_id = None
            box.current_shipment_id = shipment.id
            box.updated_at = now
        for card, _box_id in boxed_cards:
            card.current_location_id = None
            card.current_shipment_id = shipment.id
            card.updated_at = now
        for row in standalone_rows:
            card = await self.session.get(Card, row.card_id)
            card.current_location_id = None
            card.current_shipment_id = shipment.id
            card.updated_at = now

        shipment.status = "in_transit"
        shipment.handed_over_at = now
        await self.session.flush()
        await self._event("shipment.handed_over", "shipment", shipment.id, actor.id, {
            "boxes": [str(box.id) for box in boxes],
            "boxed_cards": [str(card.id) for card, _ in boxed_cards],
            "standalone_cards": [str(row.card_id) for row in standalone_rows],
        })
        for box in boxes:
            await self._event("box.shipment_handed_over", "box", box.id, actor.id, {
                "shipment_id": str(shipment.id),
                "from_location_id": str(source.id),
                "to_location_id": str(target.id),
            })
        for card, box_id in boxed_cards:
            await self._event("card.shipment_handed_over", "card", card.id, actor.id, {
                "shipment_id": str(shipment.id), "box_id": str(box_id),
                "from_location_id": str(source.id), "to_location_id": str(target.id),
            })
        for row in standalone_rows:
            await self._event("card.shipment_handed_over", "card", row.card_id, actor.id, {
                "shipment_id": str(shipment.id),
                "from_location_id": str(source.id), "to_location_id": str(target.id),
            })
        return shipment

    async def register_exception(
        self, *, shipment_id: uuid.UUID, actor_id: uuid.UUID, note: str
    ) -> Shipment:
        """Mark an in-transit shipment as exceptional without moving its cargo."""
        actor = await self._active_user(actor_id)
        normalized_note = (note or "").strip()
        if not normalized_note:
            raise ShipmentWorkflowError("Укажите причину инцидента")
        if len(normalized_note) > 2000:
            raise ShipmentWorkflowError("Причина инцидента не должна превышать 2000 символов")

        shipment = await self.session.scalar(
            select(Shipment).where(Shipment.id == shipment_id).with_for_update()
        )
        if shipment is None or shipment.status != "in_transit":
            raise ShipmentWorkflowError("Инцидент можно зарегистрировать только для отправления в пути")

        source, target = await self._locations(
            shipment.from_location_id, shipment.to_location_id
        )

        def may_report(location: Location) -> bool:
            expected_role = {
                "branch": "branch",
                "central_office": "central_office",
                "archive": "archive",
            }[location.kind]
            return (
                actor.role == expected_role
                and (location.kind != "branch" or actor.branch_id == location.branch_id)
            )

        if not (may_report(source) or may_report(target)):
            raise ShipmentWorkflowError(
                "Зарегистрировать инцидент может сотрудник места отправления или получения"
            )

        now = datetime.now(timezone.utc)
        shipment.status = "exception"
        shipment.exception_by = actor.id
        shipment.exception_at = now
        shipment.exception_note = normalized_note
        await self.session.flush()
        await self._event("shipment.exception_registered", "shipment", shipment.id, actor.id, {
            "from_location_id": str(source.id),
            "to_location_id": str(target.id),
            "note": normalized_note,
        })
        return shipment

    async def receive(self, *, shipment_id: uuid.UUID, actor_id: uuid.UUID) -> Shipment:
        """Receive an ordinary in-transit shipment after full manifest validation."""
        return await self._receive(
            shipment_id=shipment_id, actor_id=actor_id, expected_status="in_transit",
        )

    async def resolve_exception(
        self, *, shipment_id: uuid.UUID, actor_id: uuid.UUID, resolution_note: str
    ) -> Shipment:
        """Resolve an incident only by receiving the complete, verified shipment."""
        normalized_note = (resolution_note or "").strip()
        if not normalized_note:
            raise ShipmentWorkflowError("Укажите результат проверки отправления")
        if len(normalized_note) > 2000:
            raise ShipmentWorkflowError("Результат проверки не должен превышать 2000 символов")
        return await self._receive(
            shipment_id=shipment_id, actor_id=actor_id, expected_status="exception",
            exception_resolution_note=normalized_note,
        )

    async def _receive(
        self, *, shipment_id: uuid.UUID, actor_id: uuid.UUID,
        expected_status: str, exception_resolution_note: str | None = None,
    ) -> Shipment:
        actor = await self._active_user(actor_id)
        shipment = await self.session.scalar(
            select(Shipment).where(Shipment.id == shipment_id).with_for_update()
        )
        if shipment is None or shipment.status != expected_status:
            if expected_status == "exception":
                raise ShipmentWorkflowError("Инцидент не найден или уже разрешён")
            raise ShipmentWorkflowError("Отправление не найдено или уже не находится в пути")
        source, target = await self._locations(
            shipment.from_location_id, shipment.to_location_id
        )
        await self._authorize_location(actor, target)

        boxes = list((await self.session.scalars(
            select(Box)
            .join(ShipmentBox, ShipmentBox.box_id == Box.id)
            .where(ShipmentBox.shipment_id == shipment.id)
            .order_by(Box.id)
            .with_for_update()
        )).all())
        rows = list((await self.session.scalars(
            select(ShipmentCard)
            .where(ShipmentCard.shipment_id == shipment.id)
            .order_by(ShipmentCard.card_id)
        )).all())
        if not boxes and not rows:
            raise ShipmentWorkflowError("Нельзя принять пустое отправление")

        box_ids = {box.id for box in boxes}
        expected_boxed: dict[uuid.UUID, uuid.UUID] = {}
        for box in boxes:
            if box.current_shipment_id != shipment.id or box.current_location_id is not None:
                raise ShipmentWorkflowError("Состояние коробки не соответствует отправлению")
            cycle = await self.session.scalar(
                select(BoxCycle).where(BoxCycle.box_id == box.id, BoxCycle.ended_at.is_(None)).with_for_update()
            )
            if cycle is None:
                raise ShipmentWorkflowError("У отправляемой коробки отсутствует открытый цикл")
            members = list((await self.session.scalars(
                select(BoxCardMembership).where(
                    BoxCardMembership.box_cycle_id == cycle.id,
                    BoxCardMembership.removed_at.is_(None),
                )
            )).all())
            for membership in members:
                expected_boxed[membership.card_id] = box.id

        manifest = {row.card_id: row.box_id for row in rows}
        if manifest != expected_boxed | {card_id: box_id for card_id, box_id in manifest.items() if box_id is None}:
            raise ShipmentWorkflowError("Состав отправления не совпадает с фактическим составом коробок")
        if any(box_id is not None and box_id not in box_ids for box_id in manifest.values()):
            raise ShipmentWorkflowError("Манифест содержит неизвестную коробку")

        cards: list[Card] = []
        for row in rows:
            card = await self.session.scalar(
                select(Card).where(Card.id == row.card_id).with_for_update()
            )
            if card is None or card.current_shipment_id != shipment.id or card.current_location_id is not None:
                raise ShipmentWorkflowError("Состояние карты не соответствует отправлению")
            if row.box_id != expected_boxed.get(card.id) and row.box_id is not None:
                raise ShipmentWorkflowError("Карта указана не в той коробке")
            if row.box_id is None:
                if card.id in expected_boxed:
                    raise ShipmentWorkflowError("Карта из коробки ошибочно указана отдельно")
                if await self._active_membership(card.id) is not None:
                    raise ShipmentWorkflowError("Отдельная карта числится в активной коробке")
            cards.append(card)

        # Every active card inside every shipped box must be represented in the
        # manifest. Every standalone manifest row must not be in an active box.
        if set(expected_boxed) - set(manifest):
            raise ShipmentWorkflowError("В манифесте отсутствуют карты из коробки")

        now = datetime.now(timezone.utc)
        for box in boxes:
            box.current_shipment_id = None
            box.current_location_id = target.id
            box.updated_at = now
        for card in cards:
            card.current_shipment_id = None
            card.current_location_id = target.id
            card.updated_at = now

        shipment.status = "received"
        shipment.received_by = actor.id
        shipment.received_at = now

        if target.kind == "central_office" and source.kind == "archive":
            for card in cards:
                request = await self.session.scalar(
                    select(ArchiveRequest).where(
                        ArchiveRequest.card_id == card.id,
                        ArchiveRequest.status == "in_progress",
                        ArchiveRequest.fulfillment_shipment_id == shipment.id,
                    ).with_for_update()
                )
                if request is None:
                    raise ShipmentWorkflowError("Для принятой архивной карты не найдена связанная заявка ЦО")
                request.status = "completed"
                request.completed_at = now
                request.updated_at = now
                request.fulfilled_by = actor.id

        await self.session.flush()
        if exception_resolution_note is None:
            event_type = "shipment.received"
            event_payload = {
                "to_location_id": str(target.id),
                "boxes": [str(box.id) for box in boxes],
                "cards": [str(card.id) for card in cards],
            }
        else:
            event_type = "shipment.exception_resolved"
            event_payload = {
                "resolution": "complete_manifest_verified",
                "resolution_note": exception_resolution_note,
                "original_exception_by": str(shipment.exception_by),
                "original_exception_at": shipment.exception_at.isoformat(),
                "original_exception_note": shipment.exception_note,
                "to_location_id": str(target.id),
                "boxes": [str(box.id) for box in boxes],
                "cards": [str(card.id) for card in cards],
            }
        await self._event(event_type, "shipment", shipment.id, actor.id, event_payload)
        for box in boxes:
            await self._event("box.shipment_received", "box", box.id, actor.id, {
                "shipment_id": str(shipment.id), "to_location_id": str(target.id),
            })
        for card in cards:
            await self._event("card.shipment_received", "card", card.id, actor.id, {
                "shipment_id": str(shipment.id), "to_location_id": str(target.id),
            })
        return shipment

    async def _draft_and_actor(self, shipment_id: uuid.UUID, actor_id: uuid.UUID) -> tuple[Shipment, User]:
        actor = await self._active_user(actor_id)
        shipment = await self.session.scalar(
            select(Shipment).where(Shipment.id == shipment_id).with_for_update()
        )
        if shipment is None or shipment.status != "draft":
            raise ShipmentWorkflowError("Черновик отправления не найден или уже передан курьеру")
        return shipment, actor

    async def _active_user(self, user_id: uuid.UUID) -> User:
        user = await self.session.get(User, user_id)
        if user is None or not user.is_active:
            raise ShipmentWorkflowError("Пользователь не найден или неактивен")
        return user

    async def _locations(
        self, *location_ids: uuid.UUID
    ) -> tuple[Location, ...]:
        """Lock multiple locations in a stable order to avoid cross-route deadlocks."""
        unique_ids = sorted(set(location_ids), key=str)
        locked: dict[uuid.UUID, Location] = {}
        for location_id in unique_ids:
            locked[location_id] = await self._location(location_id)
        return tuple(locked[location_id] for location_id in location_ids)

    async def _location(self, location_id: uuid.UUID) -> Location:
        # A location row is the serialization point shared with admin branch
        # deactivation. Lock it before any inventory or shipment mutation.
        location = await self.session.scalar(
            select(Location).where(Location.id == location_id).with_for_update()
        )
        if location is None or not location.is_active:
            raise ShipmentWorkflowError("Место хранения не найдено или неактивно")
        return location

    async def _authorize_location(self, user: User, location: Location) -> None:
        expected_role = {
            "branch": "branch",
            "central_office": "central_office",
            "archive": "archive",
        }[location.kind]
        if user.role != expected_role:
            raise ShipmentWorkflowError("Недостаточно прав для операции в этом месте")
        if location.kind == "branch" and user.branch_id != location.branch_id:
            raise ShipmentWorkflowError("Нет доступа к указанному филиалу")

    async def _has_active_manifest_box(self, box_id: uuid.UUID) -> bool:
        return await self.session.scalar(
            select(ShipmentBox.box_id)
            .join(Shipment, Shipment.id == ShipmentBox.shipment_id)
            .where(
                ShipmentBox.box_id == box_id,
                Shipment.status.in_(("draft", "in_transit")),
            )
            .limit(1)
        ) is not None

    async def _has_active_manifest_card(self, card_id: uuid.UUID) -> bool:
        result = await self.session.scalar(
            select(ShipmentCard.card_id)
            .join(Shipment, Shipment.id == ShipmentCard.shipment_id)
            .where(
                ShipmentCard.card_id == card_id,
                Shipment.status.in_(("draft", "in_transit")),
            )
            .limit(1)
        )
        return result is not None

    async def _active_membership(self, card_id: uuid.UUID) -> BoxCardMembership | None:
        return await self.session.scalar(
            select(BoxCardMembership)
            .join(BoxCycle, BoxCycle.id == BoxCardMembership.box_cycle_id)
            .where(
                BoxCardMembership.card_id == card_id,
                BoxCardMembership.removed_at.is_(None),
                BoxCycle.ended_at.is_(None),
            )
            .with_for_update()
        )

    async def _event(
        self, event_type: str, aggregate_type: str, aggregate_id: uuid.UUID,
        actor_id: uuid.UUID, payload: dict,
    ) -> None:
        self.session.add(DomainEvent(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            actor_user_id=actor_id,
            payload=payload,
        ))
        await self.session.flush()
