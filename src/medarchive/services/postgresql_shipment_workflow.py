"""Transactional shipment workflow for the PostgreSQL target model.

The caller owns the transaction (Database.transaction()). Repositories and this
workflow never commit independently.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    Box,
    BoxCardMembership,
    BoxCycle,
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

    async def create_draft(
        self,
        *,
        from_location_id: uuid.UUID,
        to_location_id: uuid.UUID,
        courier_id: uuid.UUID,
        issued_by: uuid.UUID,
    ) -> Shipment:
        actor = await self._active_user(issued_by)
        source = await self._location(from_location_id)
        target = await self._location(to_location_id)
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
        courier = await self.session.get(Courier, courier_id)
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
        await self._authorize_location(actor, await self._location(shipment.from_location_id))
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
            select(ShipmentCard.card_id).where(
                ShipmentCard.shipment_id == shipment.id,
                ShipmentCard.card_id.in_(member_ids),
            ).limit(1)
        )
        if already_added is not None:
            raise ShipmentWorkflowError("Карта из коробки уже добавлена в отправление отдельно")
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
        source = await self._location(shipment.from_location_id)
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
                if card is None or card.status != "active" or card.current_location_id != source.id or card.current_shipment_id is not None:
                    raise ShipmentWorkflowError("Карта в коробке изменила местонахождение или недоступна")
                boxed_cards.append((card, box.id))

        for row in standalone_rows:
            card = await self.session.scalar(
                select(Card).where(Card.id == row.card_id).with_for_update()
            )
            if card is None or card.status != "active" or card.current_location_id != source.id or card.current_shipment_id is not None:
                raise ShipmentWorkflowError("Отдельная карта изменила местонахождение или недоступна")
            if await self._active_membership(card.id) is not None:
                raise ShipmentWorkflowError("Нельзя отправить отдельно карту, находящуюся в коробке")

        # Archive issues to the central office are allowed only for cards covered
        # by active central-office requests. The request remains open until receipt.
        target = await self._location(shipment.to_location_id)
        if source.kind == "archive" and target.kind == "central_office":
            card_ids = [card.id for card, _ in boxed_cards] + [row.card_id for row in standalone_rows]
            for card_id in card_ids:
                request = await self.session.scalar(
                    select(ArchiveRequest)
                    .join(User, User.id == ArchiveRequest.requested_by)
                    .where(
                        ArchiveRequest.card_id == card_id,
                        ArchiveRequest.status.in_(("new", "in_progress")),
                        User.role == "central_office",
                    )
                    .with_for_update()
                )
                if request is None:
                    raise ShipmentWorkflowError("Для каждой выдаваемой карты нужна активная заявка ЦО")

        # Ensure each transported card has exactly one manifest row and that its
        # containing box is explicit. A duplicate is rejected by the PK as well.
        for card, box_id in boxed_cards:
            self.session.add(ShipmentCard(shipment_id=shipment.id, card_id=card.id, box_id=box_id))

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

    async def receive(self, *, shipment_id: uuid.UUID, actor_id: uuid.UUID) -> Shipment:
        actor = await self._active_user(actor_id)
        shipment = await self.session.scalar(
            select(Shipment).where(Shipment.id == shipment_id).with_for_update()
        )
        if shipment is None or shipment.status != "in_transit":
            raise ShipmentWorkflowError("Отправление не найдено или уже не находится в пути")
        target = await self._location(shipment.to_location_id)
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

        if target.kind == "central_office" and (await self._location(shipment.from_location_id)).kind == "archive":
            for card in cards:
                request = await self.session.scalar(
                    select(ArchiveRequest).where(
                        ArchiveRequest.card_id == card.id,
                        ArchiveRequest.status.in_(("new", "in_progress")),
                    ).with_for_update()
                )
                if request is not None:
                    request.status = "completed"
                    request.completed_at = now
                    request.updated_at = now
                    request.fulfilled_by = actor.id

        await self.session.flush()
        await self._event("shipment.received", "shipment", shipment.id, actor.id, {
            "to_location_id": str(target.id),
            "boxes": [str(box.id) for box in boxes],
            "cards": [str(card.id) for card in cards],
        })
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

    async def _location(self, location_id: uuid.UUID) -> Location:
        location = await self.session.get(Location, location_id)
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
