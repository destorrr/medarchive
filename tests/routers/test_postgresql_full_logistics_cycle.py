"""End-to-end branch -> archive -> Central Office -> archive HTTP workflow."""

import json
import os
import uuid
from datetime import date
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import (
    ArchiveRequest,
    ArchiveShelf,
    Box,
    BoxPlacement,
    BoxCardMembership,
    BoxCycle,
    Branch,
    Card,
    Courier,
    Location,
    Shipment,
    ShipmentBox,
    ShipmentCard,
    User,
)
from medarchive.config import Config
from medarchive.dependencies import get_auth, get_pg_database, get_pg_session
from medarchive.main import create_app


DATABASE_URL = os.getenv("MEDARCHIVE_DATABASE_URL")


@pytest.mark.asyncio
async def test_card_moves_branch_archive_central_archive_through_http_routes():
    if not DATABASE_URL:
        pytest.skip("MEDARCHIVE_DATABASE_URL is not configured")

    app = create_app(
        Config(
            database_url=DATABASE_URL,
            postgres_enabled=True,
            auth_backend="postgresql",
            secret_key="postgres-full-logistics-cycle-secret",
        )
    )
    database = await get_pg_database()
    connection = await database.engine.connect()
    outer_transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async def session_override():
        yield session

    app.dependency_overrides[get_pg_session] = session_override

    branch = Branch(
        id=uuid.uuid4(),
        code=f"FL-{uuid.uuid4().hex[:10]}",
        name="Full Flow Origin Branch",
        is_active=True,
    )
    branch_location = Location(
        id=uuid.uuid4(), kind="branch", branch_id=branch.id,
        name="Full Flow Branch", is_active=True,
    )
    archive_location = Location(
        id=uuid.uuid4(), kind="archive", branch_id=None,
        name="Full Flow Archive", is_active=True,
    )
    archive_shelf = ArchiveShelf(
        id=uuid.uuid4(), section_name="A", shelf_name=f"FLOW-{uuid.uuid4().hex[:6]}",
        qr_code=f"SHELF-{uuid.uuid4().hex[:10]}", is_active=True,
    )
    central_location = Location(
        id=uuid.uuid4(), kind="central_office", branch_id=None,
        name="Full Flow Central Office", is_active=True,
    )
    branch_actor = User(
        id=uuid.uuid4(), username=f"branch-flow-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash", full_name="Branch Flow Operator",
        role="branch", branch_id=branch.id, is_active=True,
    )
    archive_actor = User(
        id=uuid.uuid4(), username=f"archive-flow-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash", full_name="Archive Flow Operator",
        role="archive", branch_id=None, is_active=True,
    )
    central_actor = User(
        id=uuid.uuid4(), username=f"central-flow-{uuid.uuid4().hex[:10]}",
        password_hash="test-only-hash", full_name="Central Flow Operator",
        role="central_office", branch_id=None, is_active=True,
    )
    courier = Courier(
        id=uuid.uuid4(), full_name="Full Flow Courier", is_active=True
    )
    card_number = f"FLOW-{uuid.uuid4().hex[:10]}"
    qr_payload = json.dumps({
        "version": 1,
        "card_number": card_number,
        "full_name": "Full Flow Test Patient",
        "birth_date": "1981-06-07",
        "phone": "+70000000000",
    }, ensure_ascii=False)
    second_card_number = f"FLOW-SECOND-{uuid.uuid4().hex[:10]}"
    second_qr_payload = json.dumps({
        "version": 1,
        "card_number": second_card_number,
        "full_name": "Second Full Flow Test Patient",
        "birth_date": "1982-08-09",
        "phone": "+70000000001",
    }, ensure_ascii=False)

    try:
        async with session.begin():
            session.add_all([
                branch, branch_location, archive_location, central_location,
                archive_shelf, branch_actor, archive_actor, central_actor, courier,
            ])

        branch_token = get_auth().create_session_token(branch_actor)
        archive_token = get_auth().create_session_token(archive_actor)
        central_token = get_auth().create_session_token(central_actor)
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Verify the branch operator's complete working surface before mutations.
            client.cookies.set("session", branch_token)
            for path in ("/dashboard", "/packing", "/transfer", "/search", "/history"):
                page = await client.get(path)
                assert page.status_code == 200, (path, page.text[:500])

            # The branch creates a transport box and registers the card from its QR.
            client.cookies.set("session", branch_token)
            created_box = await client.post("/packing/new-box")
            assert created_box.status_code == 303
            branch_box_id = uuid.UUID(
                parse_qs(urlparse(created_box.headers["location"]).query)["box_id"][0]
            )
            client.cookies.set("session", branch_token)
            scanned = await client.post(
                "/packing/scan-card",
                data={"box_id": str(branch_box_id), "qr_payload": qr_payload},
            )
            assert scanned.status_code == 200
            assert "Full Flow Test Patient" in scanned.text
            client.cookies.set("session", branch_token)
            confirmed_card = await client.post(
                "/packing/confirm-card",
                data={"box_id": str(branch_box_id), "qr_payload": qr_payload},
            )
            assert confirmed_card.status_code == 303
            card = await session.scalar(
                select(Card).where(Card.card_number == card_number)
            )
            assert card is not None
            card_id = card.id

            client.cookies.set("session", branch_token)
            second_scanned = await client.post(
                "/packing/scan-card",
                data={"box_id": str(branch_box_id), "qr_payload": second_qr_payload},
            )
            assert second_scanned.status_code == 200
            assert "Second Full Flow Test Patient" in second_scanned.text
            client.cookies.set("session", branch_token)
            second_confirmed = await client.post(
                "/packing/confirm-card",
                data={"box_id": str(branch_box_id), "qr_payload": second_qr_payload},
            )
            assert second_confirmed.status_code == 303
            second_card = await session.scalar(
                select(Card).where(Card.card_number == second_card_number)
            )
            assert second_card is not None
            second_card_id = second_card.id

            client.cookies.set("session", branch_token)
            branch_dispatch = await client.post(
                "/transfer/confirm",
                data={
                    "box_id": str(branch_box_id),
                    "to_location_id": str(archive_location.id),
                    "courier_id": str(courier.id),
                },
            )
            assert branch_dispatch.status_code == 303
            branch_shipment = await session.scalar(
                select(Shipment).where(
                    Shipment.from_location_id == branch_location.id,
                    Shipment.to_location_id == archive_location.id,
                )
            )
            assert branch_shipment is not None
            assert branch_shipment.status == "in_transit"

            client.cookies.set("session", archive_token)
            for path in ("/dashboard", "/receive", "/placement", "/requests", "/history"):
                page = await client.get(path)
                assert page.status_code == 200, (path, page.text[:500])
            client.cookies.set("session", archive_token)
            archive_incoming = await client.get("/receive")
            assert archive_incoming.status_code == 200
            assert card_number in archive_incoming.text
            client.cookies.set("session", archive_token)
            archive_receipt = await client.post(
                "/receive/confirm",
                data={"shipment_id": str(branch_shipment.id), "confirmed": "true"},
            )
            assert archive_receipt.status_code == 303
            await session.refresh(card)
            branch_box = await session.get(Box, branch_box_id)
            await session.refresh(second_card)
            assert card.current_location_id == archive_location.id
            assert card.current_shipment_id is None
            assert second_card.current_location_id == archive_location.id
            assert second_card.current_shipment_id is None
            assert branch_box is not None
            assert branch_box.current_location_id == archive_location.id
            branch_box_members = list((await session.scalars(
                select(BoxCardMembership).where(
                    BoxCardMembership.box_cycle_id.in_(
                        select(BoxCycle.id).where(BoxCycle.box_id == branch_box_id)
                    ),
                    BoxCardMembership.removed_at.is_(None),
                )
            )).all())
            assert {membership.card_id for membership in branch_box_members} == {
                card_id, second_card_id,
            }

            # The archive extracts both requested cards before unboxed fulfillment.
            client.cookies.set("session", archive_token)
            extracted = await client.post(
                "/placement/remove-card-from-box",
                data={"box_id": str(branch_box_id), "card_id": str(card_id)},
            )
            assert extracted.status_code == 303
            client.cookies.set("session", archive_token)
            second_extracted = await client.post(
                "/placement/remove-card-from-box",
                data={"box_id": str(branch_box_id), "card_id": str(second_card_id)},
            )
            assert second_extracted.status_code == 303
            for extracted_card_id in (card_id, second_card_id):
                active_membership = await session.scalar(
                    select(BoxCardMembership).where(
                        BoxCardMembership.card_id == extracted_card_id,
                        BoxCardMembership.removed_at.is_(None),
                    )
                )
                assert active_membership is None

            client.cookies.set("session", central_token)
            for path in (
                "/dashboard", "/requests/new", "/central/receive",
                "/central/packing", "/central/transfer", "/central/custody", "/history",
            ):
                page = await client.get(path)
                assert page.status_code == 200, (path, page.text[:500])
            client.cookies.set("session", central_token)
            search = await client.get(
                "/requests/new",
                params={"query": card_number},
            )
            assert search.status_code == 200
            client.cookies.set("session", central_token)
            request_created = await client.post(
                "/requests/create",
                data={"card_ids": [str(card_id), str(second_card_id)]},
            )
            assert request_created.status_code == 303

            client.cookies.set("session", archive_token)
            fulfilled = await client.post(
                "/requests/fulfill",
                data={
                    "card_ids": [str(card_id), str(second_card_id)],
                    "courier_id": str(courier.id),
                },
            )
            assert fulfilled.status_code == 303
            archive_requests = list((await session.scalars(
                select(ArchiveRequest).where(
                    ArchiveRequest.card_id.in_([card_id, second_card_id])
                )
            )).all())
            assert len(archive_requests) == 2
            assert all(request.status == "in_progress" for request in archive_requests)
            assert all(request.fulfilled_by == archive_actor.id for request in archive_requests)
            archive_request = next(request for request in archive_requests if request.card_id == card_id)
            second_archive_request = next(
                request for request in archive_requests if request.card_id == second_card_id
            )
            assert archive_request.fulfillment_shipment_id == second_archive_request.fulfillment_shipment_id
            archive_to_central = await session.get(
                Shipment, archive_request.fulfillment_shipment_id
            )
            assert archive_to_central is not None
            assert archive_to_central.status == "in_transit"
            assert archive_to_central.from_location_id == archive_location.id
            assert archive_to_central.to_location_id == central_location.id
            assert await session.scalar(
                select(ShipmentBox.box_id).where(
                    ShipmentBox.shipment_id == archive_to_central.id
                )
            ) is None
            unboxed_manifest = list((await session.scalars(
                select(ShipmentCard).where(
                    ShipmentCard.shipment_id == archive_to_central.id,
                )
            )).all())
            assert {item.card_id for item in unboxed_manifest} == {card_id, second_card_id}
            assert all(item.box_id is None for item in unboxed_manifest)

            client.cookies.set("session", central_token)
            central_receipt = await client.post(
                "/central/receive/confirm",
                data={
                    "shipment_id": str(archive_to_central.id),
                    "confirmed": "true",
                },
            )
            assert central_receipt.status_code == 303
            await session.refresh(card)
            await session.refresh(second_card)
            for request in archive_requests:
                await session.refresh(request)
            assert card.current_location_id == central_location.id
            assert card.current_shipment_id is None
            assert second_card.current_location_id == central_location.id
            assert second_card.current_shipment_id is None
            assert all(request.status == "completed" for request in archive_requests)

            # Central Office packs both cards into its own box and returns that box to the archive.
            client.cookies.set("session", central_token)
            central_box_response = await client.post(
                "/central/packing/new-box",
                data={"card_ids": [str(card_id), str(second_card_id)]},
            )
            assert central_box_response.status_code == 303
            central_box = await session.scalar(
                select(Box).where(
                    Box.current_location_id == central_location.id,
                    Box.created_by == central_actor.id,
                )
            )
            assert central_box is not None
            central_box_id = central_box.id
            central_box_memberships = list((await session.scalars(
                select(BoxCardMembership).where(
                    BoxCardMembership.card_id.in_([card_id, second_card_id]),
                    BoxCardMembership.removed_at.is_(None),
                )
            )).all())
            assert {membership.card_id for membership in central_box_memberships} == {
                card_id, second_card_id,
            }
            cycle_ids = {membership.box_cycle_id for membership in central_box_memberships}
            assert len(cycle_ids) == 1
            cycle = await session.get(BoxCycle, next(iter(cycle_ids)))
            assert cycle is not None and cycle.box_id == central_box_id and cycle.ended_at is None

            client.cookies.set("session", central_token)
            central_dispatch = await client.post(
                "/central/transfer/confirm",
                data={
                    "box_id": str(central_box_id),
                    "to_location_id": str(archive_location.id),
                    "courier_id": str(courier.id),
                    "card_ids": [],
                },
            )
            assert central_dispatch.status_code == 303
            central_to_archive = await session.scalar(
                select(Shipment).where(
                    Shipment.from_location_id == central_location.id,
                    Shipment.to_location_id == archive_location.id,
                    Shipment.status == "in_transit",
                )
            )
            assert central_to_archive is not None
            assert await session.scalar(
                select(ShipmentBox.box_id).where(
                    ShipmentBox.shipment_id == central_to_archive.id
                )
            ) == central_box_id

            client.cookies.set("session", archive_token)
            archive_final_receipt = await client.post(
                "/receive/confirm",
                data={
                    "shipment_id": str(central_to_archive.id),
                    "confirmed": "true",
                },
            )
            assert archive_final_receipt.status_code == 303

            # Archive stores the received Central Office box on its own shelf.
            # The transport box was created and dispatched by Central Office;
            # archive only performs receipt and placement.
            client.cookies.set("session", archive_token)
            placed = await client.post(
                "/placement/box",
                data={
                    "box_id": str(central_box_id),
                    "shelf_id": str(archive_shelf.id),
                },
            )
            assert placed.status_code == 303
            active_placement = await session.scalar(
                select(BoxPlacement).where(
                    BoxPlacement.box_id == central_box_id,
                    BoxPlacement.removed_at.is_(None),
                )
            )
            assert active_placement is not None
            assert active_placement.shelf_id == archive_shelf.id
            assert active_placement.placed_by == archive_actor.id

        await session.refresh(card)
        await session.refresh(second_card)
        await session.refresh(central_box)
        assert card.current_location_id == archive_location.id
        assert card.current_shipment_id is None
        assert second_card.current_location_id == archive_location.id
        assert second_card.current_shipment_id is None
        assert central_box.current_location_id == archive_location.id
        assert central_box.current_shipment_id is None
        final_memberships = list((await session.scalars(
            select(BoxCardMembership).where(
                BoxCardMembership.card_id.in_([card_id, second_card_id]),
                BoxCardMembership.removed_at.is_(None),
            )
        )).all())
        assert {membership.card_id for membership in final_memberships} == {
            card_id, second_card_id,
        }
        assert {membership.box_cycle_id for membership in final_memberships} == {cycle.id}
        final_manifest_cards = list((await session.scalars(
            select(ShipmentCard).where(ShipmentCard.shipment_id == central_to_archive.id)
        )).all())
        assert {item.card_id for item in final_manifest_cards} == {card_id, second_card_id}
        assert all(item.box_id == central_box_id for item in final_manifest_cards)
        assert branch_box.current_location_id == archive_location.id
        assert branch_box.current_shipment_id is None
        assert not (await session.scalars(
            select(BoxCardMembership).where(
                BoxCardMembership.box_cycle_id.in_(
                    select(BoxCycle.id).where(BoxCycle.box_id == branch_box_id)
                ),
                BoxCardMembership.removed_at.is_(None),
            )
        )).all()
        assert central_box.created_by == central_actor.id
        assert (await session.get(Shipment, branch_shipment.id)).status == "received"
        assert (await session.get(Shipment, archive_to_central.id)).status == "received"
        assert (await session.get(Shipment, central_to_archive.id)).status == "received"
    finally:
        app.dependency_overrides.clear()
        await session.rollback()
        await outer_transaction.rollback()
        await session.close()
        await connection.close()
        await database.close()
