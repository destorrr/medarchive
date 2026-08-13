"""Роутер упаковки коробок (роль: Филиал).

Активная коробка пользователя — последняя коробка в статусе `packing`,
созданная этим пользователем (хранится в БД, переживает логин/логаут/рестарт).
"""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_current_user, get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.services.box_service import BoxService

router = APIRouter()


async def _get_active_box(db, user: User):
    """Возвращает (box, cards) для активной коробки пользователя или (None, [])."""
    box_repo = BoxRepositoryImpl(db)
    box = await box_repo.get_active_for_user(user.id)
    if box is None:
        return None, []
    cards = await box_repo.get_cards(box.id)
    return box, cards


@router.get("/packing")
async def packing_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, cards = await _get_active_box(db, user)
    ctx = {
        "request": request,
        "username": user.username,
        "role_label": "Филиал",
        "menu_items": [],
        "box": box,
        "cards": cards,
        "cards_count": len(cards),
    }
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/new-box")
async def new_box(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    box = await service.create_box(branch_id=user.branch_id or 1, user_id=user.id)
    ctx = {"request": request, "box": box, "cards": [], "cards_count": 0}
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/scan-card")
async def scan_card(request: Request, full_name: str = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, _ = await _get_active_box(db, user)
    if box is None:
        # No active box: clean fragment with inline error (no page nesting)
        return request.app.state.templates.TemplateResponse(
            request=request, name="packing_fragment.html",
            context={"request": request, "error": "Сначала создайте коробку", "box": None, "cards": [], "cards_count": 0}
        )
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    await service.scan_card(box.id, full_name)
    cards = await service.get_box_contents(box.id)
    ctx = {"request": request, "box": box, "cards": cards, "cards_count": len(cards)}
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/remove-card")
async def remove_card(request: Request, card_id: int = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, _ = await _get_active_box(db, user)
    if box is None:
        return request.app.state.templates.TemplateResponse(
            request=request, name="packing_fragment.html",
            context={"request": request, "error": "Сначала создайте коробку", "box": None, "cards": [], "cards_count": 0}
        )
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    await service.remove_card(box.id, card_id)
    cards = await service.get_box_contents(box.id)
    ctx = {"request": request, "box": box, "cards": cards, "cards_count": len(cards)}
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/close-box")
async def close_box(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, _ = await _get_active_box(db, user)
    if box is None:
        return RedirectResponse(url="/packing", status_code=303)
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    await service.close_box(box.id)
    return RedirectResponse(url=f"/transfer?box_id={box.id}", status_code=303)
