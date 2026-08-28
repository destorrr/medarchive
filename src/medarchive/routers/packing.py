"""Роутер упаковки коробок (роль: Филиал).

Активная коробка пользователя — выбранная им открытая коробка
(users.active_box_id, хранится в БД, переживает логин/логаут/рестарт).
На странице видны все открытые коробки пользователя: можно переключиться
на любую (продолжить сканирование) или закрыть её.
"""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.repositories import CardRepositoryImpl, BoxRepositoryImpl
from medarchive.services.box_service import BoxService, DuplicateCardError

router = APIRouter()


async def _active_box(db, user: User):
    """Возвращает (box, cards) для активной коробки пользователя или (None, [])."""
    box_repo = BoxRepositoryImpl(db)
    box = await box_repo.get_active_for_user(user.id)
    if box is None:
        return None, []
    cards = await box_repo.get_cards(box.id)
    return box, cards


async def _fragment_ctx(db, user, request, box, cards, error=None) -> dict:
    """Общий контекст фрагмента: содержимое активной коробки + список открытых коробок."""
    box_repo = BoxRepositoryImpl(db)
    open_boxes = await box_repo.list_open_for_user(user.id)
    return {
        "request": request,
        "error": error,
        "box": box,
        "cards": cards,
        "cards_count": len(cards),
        "active_box_id": box.id if box else None,
        "open_boxes": open_boxes,
    }


async def _page_ctx(db, user, request, box, cards) -> dict:
    """Полный контекст страницы /packing (шапка + фрагмент)."""
    ctx = await _fragment_ctx(db, user, request, box, cards)
    ctx.update({
        "username": user.username,
        "role_label": "Филиал",
        "menu_items": [],
    })
    return ctx


@router.get("/packing")
async def packing_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, cards = await _active_box(db, user)
    ctx = await _page_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/new-box")
async def new_box(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    box = await service.create_box(branch_id=user.branch_id or 1, user_id=user.id)
    ctx = await _page_ctx(db, user, request, box, [])
    return request.app.state.templates.TemplateResponse(request=request, name="packing.html", context=ctx)


@router.post("/packing/scan-card")
async def scan_card(request: Request, full_name: str = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, _ = await _active_box(db, user)
    if box is None:
        ctx = await _fragment_ctx(db, user, request, None, [], error="Сначала создайте коробку")
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    try:
        await service.scan_card(box.id, full_name)
    except DuplicateCardError as exc:
        cards = await service.get_box_contents(box.id)
        ctx = await _fragment_ctx(db, user, request, box, cards, error=str(exc))
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    cards = await service.get_box_contents(box.id)
    ctx = await _fragment_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/remove-card")
async def remove_card(request: Request, card_id: int = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    box, _ = await _active_box(db, user)
    if box is None:
        ctx = await _fragment_ctx(db, user, request, None, [], error="Сначала создайте коробку")
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    await service.remove_card(box.id, card_id)
    cards = await service.get_box_contents(box.id)
    ctx = await _fragment_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/open-box")
async def open_box(request: Request, box_id: int = Form(...), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    """Переключиться на другую открытую коробку (продолжить её упаковку)."""
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    try:
        box = await service.select_box(box_id, user.id)
    except ValueError as exc:
        active, _ = await _active_box(db, user)
        cards = await service.get_box_contents(active.id) if active else []
        ctx = await _fragment_ctx(db, user, request, active, cards, error=str(exc))
        return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)
    cards = await service.get_box_contents(box.id)
    ctx = await _fragment_ctx(db, user, request, box, cards)
    return request.app.state.templates.TemplateResponse(request=request, name="packing_fragment.html", context=ctx)


@router.post("/packing/close-box")
async def close_box(request: Request, box_id: int | None = Form(default=None), user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    db = await get_db()
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    service = BoxService(db, card_repo, box_repo)
    if box_id is None:
        box, _ = await _active_box(db, user)
        if box is None:
            return RedirectResponse(url="/packing", status_code=303)
        box_id = box.id
    try:
        await service.close_box(box_id, user_id=user.id)
    except ValueError:
        return RedirectResponse(url="/packing", status_code=303)
    return RedirectResponse(url=f"/transfer?box_id={box_id}", status_code=303)
