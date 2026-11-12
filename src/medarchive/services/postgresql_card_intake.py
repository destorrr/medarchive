"""Validate QR payloads and register cards received from the medical system."""

from __future__ import annotations

import json
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Card, DomainEvent, Location, User


class CardIntakeError(ValueError):
    """A QR payload or card-intake transition is invalid."""


class PostgreSQLCardIntakeService:
    """Validate the agreed QR contract and register a card at its origin branch."""

    QR_VERSION = 1

    def __init__(self, session: AsyncSession):
        self.session = session

    @classmethod
    def parse_payload(cls, payload: str) -> dict:
        try:
            data = json.loads(payload)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CardIntakeError("QR-код не содержит корректный JSON") from exc
        if not isinstance(data, dict):
            raise CardIntakeError("Ожидается JSON-объект медицинской карты")
        if data.get("version") not in (cls.QR_VERSION, str(cls.QR_VERSION)):
            raise CardIntakeError("Неподдерживаемая версия QR-формата")

        card_number = str(data.get("card_number") or "").strip()
        full_name = str(data.get("full_name") or "").strip()
        birth_date_raw = str(data.get("birth_date") or "").strip()
        phone = str(data.get("phone") or "").strip() or None
        if not card_number:
            raise CardIntakeError("В QR-коде отсутствует номер медицинской карты")
        if not full_name:
            raise CardIntakeError("В QR-коде отсутствует ФИО пациента")
        try:
            birth_date = date.fromisoformat(birth_date_raw)
            if birth_date.isoformat() != birth_date_raw:
                raise ValueError("non-canonical date")
        except ValueError as exc:
            raise CardIntakeError(
                "Дата рождения в QR-коде должна быть в формате ГГГГ-ММ-ДД"
            ) from exc
        return {
            "card_number": card_number,
            "full_name": full_name,
            "birth_date": birth_date,
            "phone": phone,
        }

    async def _actor_and_location(self, actor_id: uuid.UUID) -> tuple[User, Location]:
        actor = await self.session.get(User, actor_id)
        if actor is None or not actor.is_active or actor.role != "branch":
            raise CardIntakeError(
                "Регистрировать медицинские карты может только сотрудник филиала"
            )
        if actor.branch_id is None:
            raise CardIntakeError("К учётной записи не привязан филиал")
        location = await self.session.scalar(
            select(Location).where(
                Location.kind == "branch",
                Location.branch_id == actor.branch_id,
                Location.is_active.is_(True),
            )
        )
        if location is None:
            raise CardIntakeError("Для филиала не настроено активное место хранения")
        return actor, location

    async def inspect_from_qr(
        self, *, payload: str, actor_id: uuid.UUID
    ) -> tuple[dict, Card | None]:
        """Validate and preview a QR without creating a new medical-card row."""
        _actor, location = await self._actor_and_location(actor_id)
        data = self.parse_payload(payload)
        existing = await self.session.scalar(
            select(Card).where(Card.card_number == data["card_number"]).with_for_update()
        )
        if existing is not None:
            if (
                existing.full_name != data["full_name"]
                or existing.date_of_birth != data["birth_date"]
            ):
                raise CardIntakeError(
                    "Номер карты уже зарегистрирован с другими персональными данными"
                )
            if (
                existing.status != "active"
                or existing.current_location_id != location.id
                or existing.current_shipment_id is not None
                or existing.custody_status != "available"
            ):
                raise CardIntakeError(
                    "Карта с таким номером уже зарегистрирована и не находится на балансе этого филиала"
                )
        return data, existing

    async def register_from_qr(
        self, *, payload: str, actor_id: uuid.UUID
    ) -> tuple[Card, bool]:
        """Create a previously previewed card only after operator confirmation."""
        actor, location = await self._actor_and_location(actor_id)
        data = self.parse_payload(payload)
        existing = await self.session.scalar(
            select(Card).where(Card.card_number == data["card_number"]).with_for_update()
        )
        if existing is not None:
            if (
                existing.full_name != data["full_name"]
                or existing.date_of_birth != data["birth_date"]
            ):
                raise CardIntakeError(
                    "Номер карты уже зарегистрирован с другими персональными данными"
                )
            if (
                existing.status != "active"
                or existing.current_location_id != location.id
                or existing.current_shipment_id is not None
                or existing.custody_status != "available"
            ):
                raise CardIntakeError(
                    "Карта с таким номером уже зарегистрирована и не находится на балансе этого филиала"
                )
            return existing, False

        card = Card(
            card_number=data["card_number"],
            full_name=data["full_name"],
            date_of_birth=data["birth_date"],
            phone=data["phone"],
            origin_branch_id=actor.branch_id,
            current_location_id=location.id,
            current_shipment_id=None,
            status="active",
            custody_status="available",
            created_by=actor.id,
        )
        self.session.add(card)
        await self.session.flush()
        self.session.add(DomainEvent(
            event_type="card.registered_from_medical_system_qr",
            aggregate_type="card",
            aggregate_id=card.id,
            actor_user_id=actor.id,
            payload={
                "source": "medical_system_qr",
                "location_id": str(location.id),
            },
        ))
        await self.session.flush()
        return card, True
