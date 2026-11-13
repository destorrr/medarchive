"""Printable QR labels for boxes in the PostgreSQL application."""

from __future__ import annotations

import base64
import io
import uuid

import qrcode
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import Box, Location, User
from medarchive.dependencies import get_pg_current_user, get_pg_session

router = APIRouter()


def _box_qr_data(box_id: uuid.UUID) -> str:
    """Stable, non-PII QR payload; reprinting always produces the same value."""
    return f"MEDARCHIVE:BOX:{box_id}"


def _qr_data_uri(payload: str) -> str:
    image = qrcode.make(payload)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@router.get("/boxes/{box_id}/label")
async def box_label_page(
    box_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_pg_current_user),
    session: AsyncSession = Depends(get_pg_session),
):
    row = await session.execute(
        select(Box, Location)
        .join(Location, Location.id == Box.current_location_id)
        .where(Box.id == box_id, Box.status == "active")
    )
    result = row.first()
    if result is None:
        raise HTTPException(status_code=404, detail="Активная коробка не найдена")

    box, location = result
    if user.role == "admin":
        allowed = True
    elif user.role == "branch":
        allowed = location.kind == "branch" and location.branch_id == user.branch_id
    elif user.role == "central_office":
        allowed = location.kind == "central_office"
    elif user.role == "archive":
        allowed = location.kind == "archive"
    else:
        allowed = False

    if not allowed:
        raise HTTPException(status_code=403, detail="Нет доступа к этикетке этой коробки")

    payload = _box_qr_data(box.id)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="postgresql_box_label.html",
        context={
            "request": request,
            "box": box,
            "location": location,
            "qr_data_uri": _qr_data_uri(payload),
            "qr_payload": payload,
        },
    )
