"""Роутер дашборда и общих страниц."""
from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_current_user, get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole, BoxStatus
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl,
    BoxRepositoryImpl,
    CourierRepositoryImpl,
)
from medarchive.services.request_service import RequestService
from medarchive.services.return_service import ReturnService
from medarchive.services.shipment_service import ShipmentService

router = APIRouter()

ROLE_LABELS = {
    UserRole.branch: "Филиал",
    UserRole.archive: "Архив",
    UserRole.viewer: "Просмотр",
    UserRole.admin: "Администратор",
}

MENU_BY_ROLE = {
    UserRole.branch: [
        {"url": "/packing", "label": "📦 Упаковка коробки"},
        {"url": "/transfer", "label": "🚚 Передача курьеру"},
        {"url": "/my-shipments", "label": "📋 Мои отправки"},
        {"url": "/requests/new", "label": "📝 Заявки на возврат"},
        {"url": "/receive-return", "label": "📥 Приём возврата"},
    ],
    UserRole.archive: [
        {"url": "/receive", "label": "📥 Приёмка"},
        {"url": "/placement", "label": "📍 Размещение"},
        {"url": "/requests", "label": "📋 Очередь заявок"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/relocate", "label": "🔄 Перестановка"},
    ],
    UserRole.viewer: [
        {"url": "/dashboard", "label": "📊 Дашборд"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/history", "label": "📜 Журнал движений"},
    ],
    UserRole.admin: [
        {"url": "/dashboard", "label": "📊 Дашборд"},
        {"url": "/packing", "label": "📦 Упаковка"},
        {"url": "/receive", "label": "📥 Приёмка"},
        {"url": "/requests", "label": "📋 Заявки"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/admin", "label": "⚙️ Справочники"},
    ],
}


def _menu_context(user: User) -> dict:
    return {
        "role_label": ROLE_LABELS.get(user.role, "Неизвестно"),
        "menu_items": MENU_BY_ROLE.get(user.role, []),
    }


@router.get("/dashboard")
async def dashboard(request: Request):
    try:
        user = await get_current_user(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    db = await get_db()
    ctx = _menu_context(user)
    ctx["username"] = user.username
    ctx["request"] = request

    # Статистика
    total_cards = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards")
    cards_placed = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards WHERE status='placed'")
    cards_in_branch = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards WHERE status='in_branch'")
    cards_in_transit = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards WHERE status='in_transit'")
    total_boxes = await db.fetch_one("SELECT COUNT(*) as cnt FROM boxes")
    open_requests = await db.fetch_one("SELECT COUNT(*) as cnt FROM requests WHERE status IN ('new','in_progress')")

    from medarchive.services.movement_service import MovementService
    ms = MovementService(db)
    recent = await ms.get_recent_movements(5)

    ctx.update({
        "total_cards": total_cards["cnt"],
        "cards_placed": cards_placed["cnt"],
        "cards_in_branch": cards_in_branch["cnt"],
        "cards_in_transit": cards_in_transit["cnt"],
        "total_boxes": total_boxes["cnt"],
        "open_requests": open_requests["cnt"],
        "recent_movements": recent,
    })
    return request.app.state.templates.TemplateResponse(
        request=request, name="dashboard.html", context=ctx
    )


@router.get("/search")
async def search_page(request: Request, user: User = Depends(require_role(UserRole.archive, UserRole.viewer, UserRole.admin))):
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "results": False, "cards_found": [], "boxes_found": [], "query": ""})
    return request.app.state.templates.TemplateResponse(request=request, name="search.html", context=ctx)


@router.post("/search")
async def search_execute(request: Request, query: str = Form(...),
                         user: User = Depends(require_role(UserRole.archive, UserRole.viewer, UserRole.admin)),
                         db: Database = Depends(get_db)):
    from medarchive.services.search_service import SearchService
    svc = SearchService(db)
    cards = await svc.search_cards(query)
    boxes = await svc.search_boxes(query)
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "results": True, "cards_found": cards, "boxes_found": boxes, "query": query})
    return request.app.state.templates.TemplateResponse(request=request, name="search.html", context=ctx)


@router.get("/my-shipments")
async def my_shipments(
    request: Request,
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
):
    db = await get_db()
    rows = await db.fetch_all(
        """SELECT s.*, b.box_number, c.name as courier_name
           FROM shipments s
           JOIN boxes b ON s.box_id = b.id
           JOIN couriers c ON s.courier_id = c.id
           WHERE s.from_branch_id = ?
           ORDER BY s.issued_at DESC""",
        (user.branch_id or 1,),
    )
    shipments = [dict(r) for r in rows]
    ctx = _menu_context(user)
    ctx["username"] = user.username
    ctx["request"] = request
    ctx["shipments"] = shipments
    return request.app.state.templates.TemplateResponse(
        request=request, name="my_shipments.html", context=ctx
    )


@router.get("/requests/new")
async def request_new_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "results": None, "search_name": ""})
    return request.app.state.templates.TemplateResponse(request=request, name="request_new.html", context=ctx)


@router.post("/requests/new")
async def request_new_search_or_create(
    request: Request,
    search_name: str | None = Form(None),
    card_id: int | None = Form(None),
    target_branch_id: int | None = Form(None),
    target_department: int = Form(0),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)

    # Phase 1: search
    if search_name:
        cards = await card_repo.find_by_name(search_name)
        # Also get available branches as targets
        branches = await db.fetch_all("SELECT * FROM branches WHERE id != ?", (user.branch_id or 1,))
        ctx = _menu_context(user)
        ctx.update({"request": request, "username": user.username, "results": [{"id": c.id, "full_name": c.full_name, "status": c.status.value} for c in cards], "branches": [dict(b) for b in branches], "search_name": search_name})
        return request.app.state.templates.TemplateResponse(request=request, name="request_new.html", context=ctx)

    # Phase 2: create
    if card_id:
        await req_service.create_request(card_id=card_id, requested_by=user.id, target_branch_id=target_branch_id, target_department=bool(target_department))
        return RedirectResponse(url="/requests/my", status_code=303)

    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "results": None, "search_name": ""})
    return request.app.state.templates.TemplateResponse(request=request, name="request_new.html", context=ctx)


@router.get("/requests/my")
async def requests_my_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin)), db: Database = Depends(get_db)):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)
    my_requests = await req_service.get_my_requests(user.id)
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "my_requests": my_requests})
    return request.app.state.templates.TemplateResponse(request=request, name="requests_my.html", context=ctx)


@router.get("/requests")
async def request_queue_page(request: Request, user: User = Depends(require_role(UserRole.archive, UserRole.admin)), db: Database = Depends(get_db)):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)
    queue = await req_service.get_queue()
    courier_repo = CourierRepositoryImpl(db)
    couriers = await courier_repo.list_all()
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "queue": queue, "couriers": couriers})
    return request.app.state.templates.TemplateResponse(request=request, name="request_queue.html", context=ctx)


@router.post("/requests/fulfill")
async def fulfill_request(
    request: Request,
    request_id: int = Form(...),
    action: str = Form("single"),
    courier_id: int = Form(0),
    user: User = Depends(require_role(UserRole.archive, UserRole.admin)),
    db: Database = Depends(get_db),
):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)

    try:
        await req_service.fulfill_request(request_id, user.id)
    except ValueError as e:
        ctx = _menu_context(user)
        queue = await req_service.get_queue()
        ctx.update({"request": request, "username": user.username, "queue": queue, "error": str(e), "couriers": []})
        return request.app.state.templates.TemplateResponse(request=request, name="request_queue.html", context=ctx, status_code=400)

    # Get the fulfilled request to create return shipment
    req_row = await db.fetch_one("SELECT * FROM requests WHERE id = ?", (request_id,))
    card = await card_repo.get_by_id(req_row["card_id"])

    if action == "single" and courier_id > 0:
        # Create return shipment for single card
        courier_repo = CourierRepositoryImpl(db)
        shipment_service = ShipmentService(db, box_repo, courier_repo, card_repo)
        # Create a temporary box for the return card
        return_box = await box_repo.create(f"RB-{card.id:04d}", origin_branch_id=0, created_by=user.id)
        await box_repo.add_card(return_box.id, card.id)
        await box_repo.update_status(return_box.id, BoxStatus.ready)
        await shipment_service.transfer_box(return_box.id, courier_id, user.id)

    return RedirectResponse(url="/requests", status_code=303)


@router.get("/receive-return")
async def receive_return_page(
    request: Request,
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
):
    """Страница приёма возврата: сканер карты."""
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username})
    return request.app.state.templates.TemplateResponse(
        request=request, name="receive_return.html", context=ctx
    )


@router.post("/receive-return/confirm")
async def receive_return_confirm(
    request: Request,
    card_id: int = Form(...),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Подтвердить получение возвращённой карты."""
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    courier_repo = CourierRepositoryImpl(db)
    return_service = ReturnService(db, card_repo, box_repo, courier_repo)

    try:
        await return_service.receive_return(card_id, user.id)
    except ValueError as e:
        ctx = _menu_context(user)
        ctx.update({"request": request, "username": user.username, "error": str(e)})
        return request.app.state.templates.TemplateResponse(
            request=request, name="receive_return.html", context=ctx, status_code=400
        )

    return RedirectResponse(url="/my-shipments", status_code=303)


@router.get("/history")
async def history_page(
    request: Request,
    user: User = Depends(require_role(UserRole.archive, UserRole.viewer, UserRole.admin)),
    db: Database = Depends(get_db),
):
    from medarchive.services.movement_service import MovementService
    ms = MovementService(db)
    movements = await ms.get_recent_movements(50)
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "movements": movements})
    return request.app.state.templates.TemplateResponse(
        request=request, name="history.html", context=ctx
    )


@router.post("/requests/cancel")
async def cancel_request_route(
    request: Request,
    request_id: int = Form(...),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)
    try:
        await req_service.cancel_request(request_id)
    except ValueError:
        pass  # silently ignore invalid cancellations
    return RedirectResponse(url="/requests/my", status_code=303)
