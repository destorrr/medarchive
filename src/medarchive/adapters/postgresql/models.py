"""SQLAlchemy mappings for the PostgreSQL target schema.

These mappings intentionally live beside (not inside) the legacy SQLite domain
models. UUID identifiers and location/shipment state are part of the target model.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Identity, Index, Integer, String, Text, UniqueConstraint,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base metadata for PostgreSQL mappings."""


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("kind IN ('branch', 'central_office', 'archive')", name="locations_kind_check"),
        CheckConstraint(
            "(kind = 'branch' AND branch_id IS NOT NULL) OR (kind <> 'branch' AND branch_id IS NULL)",
            name="ck_location_branch_binding",
        ),
        Index("uq_locations_branch", "branch_id", unique=True, postgresql_where=text("kind = 'branch'")),
        Index("uq_locations_singleton_kind", "kind", unique=True, postgresql_where=text("kind IN ('central_office', 'archive')")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    kind: Mapped[str] = mapped_column(Text)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('branch', 'central_office', 'archive', 'admin', 'viewer')", name="users_role_check"),
        CheckConstraint(
            "(role = 'branch' AND branch_id IS NOT NULL) OR (role <> 'branch' AND branch_id IS NULL)",
            name="ck_user_branch_binding",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    branch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="RESTRICT"))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Courier(Base):
    __tablename__ = "couriers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    full_name: Mapped[str] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive', 'under_review')", name="cards_status_check"),
        CheckConstraint(
            "custody_status IN ('available', 'at_court', 'at_insurance', 'at_external_organization')",
            name="cards_custody_status_check",
        ),
        CheckConstraint(
            "(custody_status = 'available' AND external_since IS NULL AND external_organization IS NULL) OR "
            "(custody_status <> 'available' AND external_since IS NOT NULL "
            "AND NULLIF(btrim(external_organization), '') IS NOT NULL)",
            name="ck_card_external_custody_fields",
        ),
        CheckConstraint(
            "(current_location_id IS NOT NULL AND current_shipment_id IS NULL) OR "
            "(current_location_id IS NULL AND current_shipment_id IS NOT NULL)",
            name="ck_card_exactly_one_location",
        ),
        ForeignKeyConstraint(
            ["id", "current_shipment_id"], ["shipment_cards.card_id", "shipment_cards.shipment_id"],
            name="fk_card_shipment_manifest", deferrable=True, initially="DEFERRED",
        ),
        Index("ix_cards_current_location", "current_location_id"),
        Index("ix_cards_current_shipment", "current_shipment_id"),
        Index("ix_cards_full_name", text("to_tsvector('simple', full_name)"), postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    card_number: Mapped[str] = mapped_column(Text, unique=True)
    full_name: Mapped[str] = mapped_column(Text)
    date_of_birth: Mapped[date] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(Text)
    origin_branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id", ondelete="RESTRICT"))
    current_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    current_shipment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    custody_status: Mapped[str] = mapped_column(Text, server_default=text("'available'"))
    external_organization: Mapped[str | None] = mapped_column(Text)
    external_comment: Mapped[str | None] = mapped_column(Text)
    external_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class Box(Base):
    __tablename__ = "boxes"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'written_off', 'under_review')", name="boxes_status_check"),
        CheckConstraint(
            "(current_location_id IS NOT NULL AND current_shipment_id IS NULL) OR "
            "(current_location_id IS NULL AND current_shipment_id IS NOT NULL)",
            name="ck_box_exactly_one_location",
        ),
        ForeignKeyConstraint(
            ["id", "current_shipment_id"], ["shipment_boxes.box_id", "shipment_boxes.shipment_id"],
            name="fk_box_shipment_manifest", deferrable=True, initially="DEFERRED",
        ),
        Index("ix_boxes_current_location", "current_location_id"),
        Index(
            "uq_box_one_active_shipment", "current_shipment_id", "id",
            unique=True, postgresql_where=text("current_shipment_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    box_number: Mapped[str] = mapped_column(Text, unique=True)
    current_location_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    current_shipment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BoxCycle(Base):
    __tablename__ = "box_cycles"
    __table_args__ = (
        UniqueConstraint("box_id", "cycle_number"),
        CheckConstraint("cycle_number > 0", name="box_cycles_cycle_number_check"),
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_box_cycle_dates"),
        Index("uq_box_one_open_cycle", "box_id", unique=True, postgresql_where=text("ended_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    box_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("boxes.id", ondelete="RESTRICT"))
    cycle_number: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    ended_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class BoxCardMembership(Base):
    __tablename__ = "box_card_memberships"
    __table_args__ = (
        CheckConstraint("removed_at IS NULL OR removed_at >= added_at", name="ck_box_membership_dates"),
        Index("uq_card_one_active_box", "card_id", unique=True, postgresql_where=text("removed_at IS NULL")),
        Index("ix_box_memberships_cycle", "box_cycle_id", "added_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    box_cycle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("box_cycles.id", ondelete="RESTRICT"))
    card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cards.id", ondelete="RESTRICT"))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'in_transit', 'received', 'exception', 'cancelled')",
            name="shipments_status_check",
        ),
        CheckConstraint("from_location_id <> to_location_id", name="ck_shipment_distinct_locations"),
        CheckConstraint(
            "(status IN ('draft', 'cancelled') AND handed_over_at IS NULL) OR "
            "(status IN ('in_transit', 'received', 'exception') AND handed_over_at IS NOT NULL)",
            name="ck_shipment_handover_time",
        ),
        CheckConstraint(
            "(status = 'received' AND received_by IS NOT NULL AND received_at IS NOT NULL) OR "
            "(status <> 'received' AND received_by IS NULL AND received_at IS NULL)",
            name="ck_shipment_received_fields",
        ),
        CheckConstraint(
            "((status = 'exception' AND exception_by IS NOT NULL AND exception_at IS NOT NULL "
            "AND NULLIF(btrim(exception_note), '') IS NOT NULL) OR "
            "(status = 'received' AND ((exception_by IS NULL AND exception_at IS NULL AND exception_note IS NULL) "
            "OR (exception_by IS NOT NULL AND exception_at IS NOT NULL "
            "AND NULLIF(btrim(exception_note), '') IS NOT NULL))) OR "
            "(status NOT IN ('exception', 'received') AND exception_by IS NULL "
            "AND exception_at IS NULL AND exception_note IS NULL))",
            name="ck_shipment_exception_fields",
        ),
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL) OR "
            "(status <> 'cancelled' AND cancelled_by IS NULL AND cancelled_at IS NULL)",
            name="ck_shipment_cancelled_fields",
        ),
        Index("ix_shipments_route_status", "from_location_id", "to_location_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    shipment_number: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    from_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    to_location_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("locations.id", ondelete="RESTRICT"))
    courier_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("couriers.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'draft'"))
    issued_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handed_over_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exception_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    exception_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exception_note: Mapped[str | None] = mapped_column(Text)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShipmentBox(Base):
    __tablename__ = "shipment_boxes"
    __table_args__ = (
        ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["box_id"], ["boxes.id"], ondelete="RESTRICT"),
        UniqueConstraint("box_id", "shipment_id"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    box_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)


class ShipmentCard(Base):
    __tablename__ = "shipment_cards"
    __table_args__ = (
        ForeignKeyConstraint(["shipment_id"], ["shipments.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["shipment_id", "box_id"], ["shipment_boxes.shipment_id", "shipment_boxes.box_id"],
            name="fk_shipment_card_containing_box", ondelete="RESTRICT",
        ),
        UniqueConstraint("card_id", "shipment_id"),
    )

    shipment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    box_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class ArchiveRequest(Base):
    __tablename__ = "archive_requests"
    __table_args__ = (
        CheckConstraint("status IN ('new', 'in_progress', 'completed', 'not_found', 'cancelled')", name="archive_requests_status_check"),
        CheckConstraint(
            "(status IN ('new', 'not_found', 'cancelled') AND fulfillment_shipment_id IS NULL) OR "
            "(status IN ('in_progress', 'completed') AND fulfillment_shipment_id IS NOT NULL)",
            name="ck_archive_request_fulfillment",
        ),
        Index("uq_card_one_active_archive_request", "card_id", unique=True, postgresql_where=text("status IN ('new', 'in_progress')")),
        Index("ix_archive_requests_status_created", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cards.id", ondelete="RESTRICT"))
    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'new'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    fulfillment_shipment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("shipments.id", ondelete="RESTRICT"))
    result_note: Mapped[str | None] = mapped_column(Text)


class ArchiveShelf(Base):
    __tablename__ = "archive_shelves"
    __table_args__ = (UniqueConstraint("section_name", "shelf_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    section_name: Mapped[str] = mapped_column(Text)
    shelf_name: Mapped[str] = mapped_column(Text)
    qr_code: Mapped[str | None] = mapped_column(Text, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BoxPlacement(Base):
    __tablename__ = "box_placements"
    __table_args__ = (
        CheckConstraint("removed_at IS NULL OR removed_at >= placed_at", name="ck_placement_dates"),
        Index("uq_box_one_active_placement", "box_id", unique=True, postgresql_where=text("removed_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    box_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("boxes.id", ondelete="RESTRICT"))
    shelf_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("archive_shelves.id", ondelete="RESTRICT"))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    placed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class CardPlacement(Base):
    __tablename__ = "card_placements"
    __table_args__ = (
        CheckConstraint("removed_at IS NULL OR removed_at >= placed_at", name="ck_card_placement_dates"),
        Index("uq_card_one_active_placement", "card_id", unique=True, postgresql_where=text("removed_at IS NULL")),
        Index("ix_card_placements_shelf", "shelf_id", "placed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    card_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("cards.id", ondelete="RESTRICT"))
    shelf_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("archive_shelves.id", ondelete="RESTRICT"))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    placed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))


class DomainEvent(Base):
    __tablename__ = "domain_events"
    __table_args__ = (
        Index("ix_domain_events_aggregate", "aggregate_type", "aggregate_id", "id"),
        Index("ix_domain_events_occurred_at", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_type: Mapped[str] = mapped_column(Text)
    aggregate_type: Mapped[str] = mapped_column(Text)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    correction_of_event_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("domain_events.id", ondelete="RESTRICT"))
    request_id: Mapped[str | None] = mapped_column(Text)
