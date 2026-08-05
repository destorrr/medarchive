"""Роутер передачи коробок курьеру (роль: Филиал)."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from medarchive.dependencies import get_current_user, get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import (
    BoxRepositoryImpl, CourierRepositoryImpl, CardRepositoryImpl
)
from medarchive.services.shipment_service import ShipmentService


router = APIRouter()


@router.get("/transfer")
async def transfer_page(
    request: Request,
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    box_id: int | None = None,
    db: Database = Depends(get_db),
):
    courier_repo = CourierRepositoryImpl(db)
    service = ShipmentService(db, BoxRepositoryImpl(db), courier_repo, CardRepositoryImpl(db))
    couriers = await service.list_couriers()

    # Get ready boxes for this branch
    rows = await db.fetch_all(
        "SELECT * FROM boxes WHERE status = 'ready' AND origin_branch_id = ?",
        (user.branch_id,),
    )
    ready_boxes = [dict(r) for r in rows]

    from medarchive.routers.dashboard import _menu_context
    ctx = _menu_context(user)
    ctx.update({
        "request": request,
        "username": user.username,
        "couriers": couriers,
        "ready_boxes": ready_boxes,
        "selected_box_id": box_id,
    })
    return request.app.state.templates.TemplateResponse(
        request=request, name="transfer.html", context=ctx
    )


@router.post("/transfer/confirm")
async def confirm_transfer(
    request: Request,
    box_id: int = Form(...),
    courier_id: int = Form(...),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    courier_repo = CourierRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    card_repo = CardRepositoryImpl(db)
    service = ShipmentService(db, box_repo, courier_repo, card_repo)

    try:
        await service.transfer_box(box_id, courier_id, user.id)
    except ValueError as e:
        from medarchive.routers.dashboard import _menu_context
        ctx = _menu_context(user)
        ctx.update({
            "request": request,
            "username": user.username,
            "error": str(e),
            "couriers": await service.list_couriers(),
            "ready_boxes": [],
            "selected_box_id": box_id,
        })
        return request.app.state.templates.TemplateResponse(
            request=request, name="transfer.html", context=ctx, status_code=400
        )

    return RedirectResponse(url="/my-shipments", status_code=303)
