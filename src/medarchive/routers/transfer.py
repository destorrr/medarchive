"""Роутер передачи курьеру (роль: Филиал)."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_current_user, get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl, BoxRepositoryImpl, CourierRepositoryImpl
)
from medarchive.services.shipment_service import ShipmentService

router = APIRouter()


@router.get("/transfer")
async def transfer_page(
    request: Request,
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
):
    db = await get_db()
    box_repo = BoxRepositoryImpl(db)
    courier_repo = CourierRepositoryImpl(db)

    # Get ready boxes from user's branch
    ready_boxes_rows = await db.fetch_all(
        "SELECT * FROM boxes WHERE status = 'ready' AND origin_branch_id = ?",
        (user.branch_id or 1,),
    )
    couriers = await courier_repo.list_all()

    ctx = {
        "request": request,
        "username": user.username,
        "role_label": "Филиал",
        "menu_items": [],
        "boxes": [dict(r) for r in ready_boxes_rows],
        "couriers": [{"id": c.id, "name": c.name} for c in couriers],
    }
    return request.app.state.templates.TemplateResponse(
        request=request, name="transfer.html", context=ctx
    )


@router.post("/transfer/confirm")
async def transfer_confirm(
    request: Request,
    box_id: int = Form(...),
    courier_id: int = Form(...),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
):
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    courier_repo = CourierRepositoryImpl(db)
    service = ShipmentService(db, box_repo, courier_repo, card_repo)
    await service.transfer_box(box_id=box_id, courier_id=courier_id, issued_by=user.id)
    return RedirectResponse(url="/my-shipments", status_code=303)
