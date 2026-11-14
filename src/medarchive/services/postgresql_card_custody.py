"""Central-office workflow for cards temporarily held by external organizations."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    BoxCardMembership,
    Card,
    DomainEvent,
    Location,
    Shipment,
    ShipmentCard,
    User,
)


class CardCustodyWorkflowError(ValueError):
    """An external-custody transition violates a business rule."""


class PostgreSQLCardCustodyWorkflow:
    """Track external custody without changing the card's Central Office balance."""

    EXTERNAL_STATES = {
        "at_court",
        "at_insurance",
        "at_external_organization",
    }

    def __init__(self, session: AsyncSession):
        self.session = session

    async def mark_external(
        self,
        *,
        card_id: uuid.UUID,
        actor_id: uuid.UUID,
        custody_status: str,
        organization: str,
        comment: str | None = None,
    ) -> Card:
        actor = await self._central_office_actor(actor_id)
        if custody_status not in self.EXTERNAL_STATES:
            raise CardCustodyWorkflowError("Неизвестный тип внешней передачи")
        organization = organization.strip()
        if not organization:
            raise CardCustodyWorkflowError("Укажите суд, страховую или другую организацию")

        card = await self._lock_card(card_id)
        await self._assert_available_at_central_office(card)
        now = datetime.now(timezone.utc)
        card.custody_status = custody_status
        card.external_organization = organization
        card.external_comment = (comment or "").strip() or None
        card.external_since = now
        card.updated_at = now
        await self.session.flush()
        await self._event("card.external_custody_started", card, actor, {
            "custody_status": custody_status,
            "organization": organization,
            "comment": card.external_comment,
            "external_since": now.isoformat(),
            "balance_location_id": str(card.current_location_id),
        })
        return card

    async def return_to_central_office(
        self,
        *,
        card_id: uuid.UUID,
        actor_id: uuid.UUID,
        comment: str | None = None,
    ) -> Card:
        actor = await self._central_office_actor(actor_id)
        card = await self._lock_card(card_id)
        await self._assert_available_at_central_office(card, allow_external=True)
        if card.custody_status == "available":
            raise CardCustodyWorkflowError("Карта не отмечена как находящаяся во внешней организации")

        previous = {
            "custody_status": card.custody_status,
            "organization": card.external_organization,
            "comment": card.external_comment,
            "external_since": card.external_since.isoformat() if card.external_since else None,
        }
        now = datetime.now(timezone.utc)
        card.custody_status = "available"
        card.external_organization = None
        card.external_comment = (comment or "").strip() or None
        card.external_since = None
        card.updated_at = now
        await self.session.flush()
        await self._event("card.external_custody_returned", card, actor, {
            "previous_external_custody": previous,
            "return_comment": card.external_comment,
            "returned_at": now.isoformat(),
            "balance_location_id": str(card.current_location_id),
        })
        return card

    async def _central_office_actor(self, actor_id: uuid.UUID) -> User:
        actor = await self.session.get(User, actor_id)
        if actor is None or not actor.is_active or actor.role != "central_office":
            raise CardCustodyWorkflowError("Операция доступна только сотруднику центрального офиса")
        return actor

    async def _lock_card(self, card_id: uuid.UUID) -> Card:
        card = await self.session.scalar(
            select(Card).where(Card.id == card_id).with_for_update()
        )
        if card is None or card.status != "active":
            raise CardCustodyWorkflowError("Карта не найдена или недоступна")
        return card

    async def _assert_available_at_central_office(
        self, card: Card, *, allow_external: bool = False
    ) -> None:
        if card.current_location_id is None or card.current_shipment_id is not None:
            raise CardCustodyWorkflowError("Карта не находится на балансе центрального офиса")
        location = await self.session.get(Location, card.current_location_id)
        if location is None or location.kind != "central_office":
            raise CardCustodyWorkflowError("Операция доступна только для карт на балансе ЦО")
        if not allow_external and card.custody_status != "available":
            raise CardCustodyWorkflowError("Карта уже передана во внешнюю организацию")
        if await self.session.scalar(
            select(BoxCardMembership.id)
            .where(
                BoxCardMembership.card_id == card.id,
                BoxCardMembership.removed_at.is_(None),
            )
            .limit(1)
        ) is not None:
            raise CardCustodyWorkflowError("Сначала извлеките карту из коробки")
        if await self.session.scalar(
            select(ShipmentCard.card_id)
            .join(Shipment, Shipment.id == ShipmentCard.shipment_id)
            .where(
                ShipmentCard.card_id == card.id,
                Shipment.status.in_(("draft", "in_transit")),
            )
            .limit(1)
        ) is not None:
            raise CardCustodyWorkflowError("Карта включена в активное отправление")

    async def _event(
        self, event_type: str, card: Card, actor: User, payload: dict
    ) -> None:
        self.session.add(DomainEvent(
            event_type=event_type,
            aggregate_type="card",
            aggregate_id=card.id,
            actor_user_id=actor.id,
            payload=payload,
        ))
        await self.session.flush()
