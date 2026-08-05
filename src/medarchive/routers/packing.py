"""Роутер упаковки коробок (роль: Филиал)."""
from fastapi import APIRouter, Request, Form, Depends
from medarchive.dependencies import get_current_user, get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.services.box_service import BoxService

router = APIRouter()

# Track active box per session (simple dict for now)
_active_boxes: dict[int, int] = {}  # user_id -> box_id


@router.get("/packing")
async def packing_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    ctx = {"request": request, "username": user.username, "role_label": "Филиал", "menu_items": []}
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/new-box")
async def new_box(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    box = await service.create_box(branch_id=user.branch_id or 1, user_id=user.id)
    _active_boxes[user.id] = box.id
    ctx = {"request": request, "box": box, "cards": [], "cards_count": 0}
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/scan-card")
async def scan_card(request: Request, full_name: str = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    box_id = _active_boxes.get(user.id)
    if box_id is None:
        return request.app.state.templates.TemplateResponse(
            request=request, name="packing.html",
            context={"request": request, "error": "Сначала создайте коробку", "box": None, "cards": [], "cards_count": 0}
        )
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    await service.scan_card(box_id, full_name)
    cards = await service.get_box_contents(box_id)
    box = await box_repo.get_by_id(box_id)
    ctx = {"request": request, "box": box, "cards": cards, "cards_count": len(cards)}
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/close-box")
async def close_box(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    box_id = _active_boxes.get(user.id)
    if box_id is None:
        # redirect to packing page
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/packing", status_code=303)
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    await service.close_box(box_id)
    _active_boxes.pop(user.id, None)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/transfer?box_id={box_id}", status_code=303)
