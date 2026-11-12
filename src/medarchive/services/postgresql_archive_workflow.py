"""Transactional archive requests and shelf placement workflows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveRequest, ArchiveShelf, Box, BoxPlacement, Card, CardPlacement,
    BoxCardMembership, BoxCycle, DomainEvent, Location, Shipment, ShipmentCard, User,
)


class ArchiveWorkflowError(ValueError):
    """An archive operation violates a business rule."""


class PostgreSQLArchiveWorkflow:
    """Create central-office requests and manage archive shelf placements."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_requests(
        self, *, card_ids: list[uuid.UUID], actor_id: uuid.UUID
    ) -> list[ArchiveRequest]:
        actor = await self._user(actor_id, "central_office")
        if not card_ids:
            raise ArchiveWorkflowError("Укажите хотя бы одну медицинскую карту")
        if len(card_ids) != len(set(card_ids)):
            raise ArchiveWorkflowError("Одна карта указана в заявке несколько раз")

        archive = await self.session.scalar(
            select(Location).where(Location.kind == "archive", Location.is_active.is_(True))
        )
        if archive is None:
            raise ArchiveWorkflowError("Архивная локация не настроена")

        cards = []
        for card_id in sorted(card_ids, key=str):
            card = await self.session.scalar(
                select(Card).where(Card.id == card_id).with_for_update()
            )
            if (
                card is None or card.status != "active"
                or card.current_location_id != archive.id
                or card.current_shipment_id is not None
            ):
                raise ArchiveWorkflowError("Заявку можно создать только на карту, находящуюся в архиве")
            active = await self.session.scalar(
                select(ArchiveRequest.id).where(
                    ArchiveRequest.card_id == card.id,
                    ArchiveRequest.status.in_(("new", "in_progress")),
                ).limit(1)
            )
            if active is not None:
                raise ArchiveWorkflowError("На эту карту уже есть активная заявка")
            active_manifest = await self.session.scalar(
                select(ShipmentCard.card_id)
                .join(Shipment, Shipment.id == ShipmentCard.shipment_id)
                .where(
                    ShipmentCard.card_id == card.id,
                    Shipment.status.in_(("draft", "in_transit")),
                )
                .limit(1)
            )
            if active_manifest is not None:
                raise ArchiveWorkflowError("Карта уже включена в активное отправление")
            cards.append(card)

        requests = [
            ArchiveRequest(card_id=card.id, requested_by=actor.id, status="new")
            for card in cards
        ]
        self.session.add_all(requests)
        await self.session.flush()
        for request in requests:
            await self._event("archive_request.created", "archive_request", request.id, actor.id, {
                "card_id": str(request.card_id),
            })
        return requests

    async def mark_not_found(
        self, *, request_id: uuid.UUID, actor_id: uuid.UUID, note: str
    ) -> ArchiveRequest:
        actor = await self._user(actor_id, "archive")
        note = note.strip()
        if not note:
            raise ArchiveWorkflowError("Укажите пояснение, почему карта не найдена")
        request = await self.session.scalar(
            select(ArchiveRequest).where(
                ArchiveRequest.id == request_id
            ).with_for_update()
        )
        if request is None or request.status != "new":
            raise ArchiveWorkflowError("Активная заявка не найдена или уже обрабатывается")
        card = await self.session.scalar(
            select(Card).where(Card.id == request.card_id).with_for_update()
        )
        archive = await self.session.scalar(
            select(Location).where(Location.kind == "archive")
        )
        if card is None or archive is None or card.current_location_id != archive.id:
            raise ArchiveWorkflowError("Карта уже не находится в архиве")
        now = datetime.now(timezone.utc)
        request.status = "not_found"
        request.completed_at = now
        request.updated_at = now
        request.fulfilled_by = actor.id
        request.result_note = note
        await self.session.flush()
        await self._event("archive_request.not_found", "archive_request", request.id, actor.id, {
            "card_id": str(card.id), "note": note,
        })
        return request

    async def place_box(
        self, *, box_id: uuid.UUID, shelf_id: uuid.UUID, actor_id: uuid.UUID
    ) -> BoxPlacement:
        actor = await self._user(actor_id, "archive")
        box = await self.session.scalar(
            select(Box).where(Box.id == box_id).with_for_update()
        )
        archive = await self.session.scalar(
            select(Location).where(Location.kind == "archive", Location.is_active.is_(True))
        )
        shelf = await self.session.scalar(
            select(ArchiveShelf).where(
                ArchiveShelf.id == shelf_id, ArchiveShelf.is_active.is_(True)
            ).with_for_update()
        )
        if (
            box is None or box.status != "active" or archive is None
            or box.current_location_id != archive.id or box.current_shipment_id is not None
        ):
            raise ArchiveWorkflowError("Размещать можно только принятую активную коробку из архива")
        if shelf is None:
            raise ArchiveWorkflowError("Архивная полка не найдена или неактивна")
        existing = await self.session.scalar(
            select(BoxPlacement.id).where(
                BoxPlacement.box_id == box.id, BoxPlacement.removed_at.is_(None)
            ).limit(1)
        )
        if existing is not None:
            raise ArchiveWorkflowError("Коробка уже размещена на архивной полке")
        placement = BoxPlacement(box_id=box.id, shelf_id=shelf.id, placed_by=actor.id)
        self.session.add(placement)
        await self.session.flush()
        await self._event("box.placed_in_archive", "box", box.id, actor.id, {
            "shelf_id": str(shelf.id), "placement_id": str(placement.id),
        })
        return placement

    async def place_card(
        self, *, card_id: uuid.UUID, shelf_id: uuid.UUID, actor_id: uuid.UUID
    ) -> CardPlacement:
        actor = await self._user(actor_id, "archive")
        archive = await self.session.scalar(
            select(Location).where(Location.kind == "archive", Location.is_active.is_(True))
        )
        card = await self.session.scalar(
            select(Card).where(Card.id == card_id).with_for_update()
        )
        shelf = await self.session.scalar(
            select(ArchiveShelf).where(
                ArchiveShelf.id == shelf_id, ArchiveShelf.is_active.is_(True)
            ).with_for_update()
        )
        if (
            card is None or card.status != "active" or archive is None
            or card.current_location_id != archive.id or card.current_shipment_id is not None
        ):
            raise ArchiveWorkflowError("Размещать отдельно можно только принятую активную карту из архива")
        membership = await self.session.scalar(
            select(BoxCardMembership.id)
            .join(BoxCycle, BoxCycle.id == BoxCardMembership.box_cycle_id)
            .where(
                BoxCardMembership.card_id == card.id,
                BoxCardMembership.removed_at.is_(None),
                BoxCycle.ended_at.is_(None),
            ).limit(1)
        )
        if membership is not None:
            raise ArchiveWorkflowError("Карта находится в коробке; сначала извлеките её из коробки")
        if shelf is None:
            raise ArchiveWorkflowError("Архивная полка не найдена или неактивна")
        existing = await self.session.scalar(
            select(CardPlacement.id).where(
                CardPlacement.card_id == card.id, CardPlacement.removed_at.is_(None)
            ).limit(1)
        )
        if existing is not None:
            raise ArchiveWorkflowError("Карта уже размещена на архивной полке")
        placement = CardPlacement(card_id=card.id, shelf_id=shelf.id, placed_by=actor.id)
        self.session.add(placement)
        await self.session.flush()
        await self._event("card.placed_in_archive", "card", card.id, actor.id, {
            "shelf_id": str(shelf.id), "placement_id": str(placement.id),
        })
        return placement

    async def remove_card_from_shelf(
        self, *, card_id: uuid.UUID, actor_id: uuid.UUID
    ) -> CardPlacement:
        actor = await self._user(actor_id, "archive")
        placement = await self.session.scalar(
            select(CardPlacement).where(
                CardPlacement.card_id == card_id, CardPlacement.removed_at.is_(None)
            ).with_for_update()
        )
        if placement is None:
            raise ArchiveWorkflowError("Активное размещение карты не найдено")
        card = await self.session.scalar(
            select(Card).where(Card.id == card_id).with_for_update()
        )
        archive = await self.session.scalar(
            select(Location).where(Location.kind == "archive")
        )
        if card is None or archive is None or card.current_location_id != archive.id:
            raise ArchiveWorkflowError("Карта больше не находится в архиве")
        now = datetime.now(timezone.utc)
        placement.removed_at = now
        placement.removed_by = actor.id
        await self.session.flush()
        await self._event("card.removed_from_archive_shelf", "card", card.id, actor.id, {
            "shelf_id": str(placement.shelf_id), "placement_id": str(placement.id),
        })
        return placement

    async def remove_box_from_shelf(
        self, *, box_id: uuid.UUID, actor_id: uuid.UUID
    ) -> BoxPlacement:
        actor = await self._user(actor_id, "archive")
        placement = await self.session.scalar(
            select(BoxPlacement).where(
                BoxPlacement.box_id == box_id, BoxPlacement.removed_at.is_(None)
            ).with_for_update()
        )
        if placement is None:
            raise ArchiveWorkflowError("Активное размещение коробки не найдено")
        box = await self.session.scalar(
            select(Box).where(Box.id == box_id).with_for_update()
        )
        archive = await self.session.scalar(
            select(Location).where(Location.kind == "archive")
        )
        if box is None or archive is None or box.current_location_id != archive.id:
            raise ArchiveWorkflowError("Коробка больше не находится в архиве")
        now = datetime.now(timezone.utc)
        placement.removed_at = now
        placement.removed_by = actor.id
        await self.session.flush()
        await self._event("box.removed_from_archive_shelf", "box", box.id, actor.id, {
            "shelf_id": str(placement.shelf_id), "placement_id": str(placement.id),
        })
        return placement

    async def _user(self, user_id: uuid.UUID, role: str) -> User:
        user = await self.session.get(User, user_id)
        if user is None or not user.is_active or user.role != role:
            raise ArchiveWorkflowError("Недостаточно прав для архивной операции")
        return user

    async def _event(
        self, event_type: str, aggregate_type: str, aggregate_id: uuid.UUID,
        actor_id: uuid.UUID, payload: dict,
    ) -> None:
        self.session.add(DomainEvent(
            event_type=event_type, aggregate_type=aggregate_type,
            aggregate_id=aggregate_id, actor_user_id=actor_id, payload=payload,
        ))
        await self.session.flush()
