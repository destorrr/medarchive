"""Роутеры архива: приёмка и размещение (роль: Архив)."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from medarchive.dependencies import get_current_user, get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import BoxRepositoryImpl, CardRepositoryImpl
from medarchive.services.receive_service import ReceiveService

router = APIRouter()


def _ctx(user: User, request: Request, **extra):
    from medarchive.routers.dashboard import _menu_context
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username})
    ctx.update(extra)
    return ctx


# === Приёмка ===

@router.get("/receive")
async def receive_page(
    request: Request,
    user: User = Depends(require_role(UserRole.archive, UserRole.admin)),
    db: Database = Depends(get_db),
):
    service = ReceiveService(db, BoxRepositoryImpl(db), CardRepositoryImpl(db))
    pending = await service.get_pending_boxes()
    ctx = _ctx(user, request, pending_boxes=pending)
    return request.app.state.templates.TemplateResponse(
        request=request, name="receive.html", context=ctx
    )


@router.post("/receive/scan")
async def scan_box(
    request: Request,
    box_number: str = Form(...),
    user: User = Depends(require_role(UserRole.archive, UserRole.admin)),
    db: Database = Depends(get_db),
):
    box_repo = BoxRepositoryImpl(db)
    box = await box_repo.get_by_number(box_number)
    if box is None:
        return HTMLResponse('<div class="flash">Коробка не найдена</div>')

    cards = await box_repo.get_cards(box.id)
    rows = "".join(f"<tr><td>{c.full_name}</td><td>{c.status.value}</td></tr>" for c in cards)
    fragment = f"""<div id="box-contents">
    <h3>Состав коробки {box.box_number}</h3>
    <table><thead><tr><th>ФИО</th><th>Статус</th></tr></thead><tbody>{rows}</tbody></table>
    <form hx-post="/receive/confirm" hx-swap="none">
        <input type="hidden" name="box_id" value="{box.id}">
        <button type="submit" class="btn btn-primary">Подтвердить приёмку</button>
    </form>
    </div>"""
    return HTMLResponse(fragment)


@router.post("/receive/confirm")
async def confirm_receive(
    request: Request,
    box_id: int = Form(...),
    user: User = Depends(require_role(UserRole.archive, UserRole.admin)),
    db: Database = Depends(get_db),
):
    service = ReceiveService(db, BoxRepositoryImpl(db), CardRepositoryImpl(db))
    try:
        await service.receive_box(box_id, user.id)
    except ValueError as e:
        return HTMLResponse(f'<div class="flash">{str(e)}</div>', status_code=400)
    return RedirectResponse(url=f"/placement?box_id={box_id}", status_code=303)


# === Размещение ===

@router.get("/placement")
async def placement_page(
    request: Request,
    box_id: int | None = None,
    user: User = Depends(require_role(UserRole.archive, UserRole.admin)),
    db: Database = Depends(get_db),
):
    service = ReceiveService(db, BoxRepositoryImpl(db), CardRepositoryImpl(db))
    received = await service.get_received_boxes()
    locations = await service.get_sections_and_shelves()

    # Build tree: section → shelves
    sections = {}
    for loc in locations:
        sid = loc["section_id"]
        if sid not in sections:
            sections[sid] = {"name": loc["section_name"], "shelves": []}
        if loc["shelf_id"]:
            sections[sid]["shelves"].append({
                "id": loc["shelf_id"], "name": loc["shelf_name"], "qr": loc["qr_code"]
            })

    ctx = _ctx(user, request, received_boxes=received, sections=sections, selected_box_id=box_id)
    return request.app.state.templates.TemplateResponse(
        request=request, name="placement.html", context=ctx
    )


@router.post("/placement/confirm")
async def confirm_placement(
    request: Request,
    box_id: int = Form(...),
    shelf_id: int = Form(...),
    user: User = Depends(require_role(UserRole.archive, UserRole.admin)),
    db: Database = Depends(get_db),
):
    service = ReceiveService(db, BoxRepositoryImpl(db), CardRepositoryImpl(db))
    try:
        await service.place_box(box_id, shelf_id)
    except ValueError as e:
        return HTMLResponse(f'<div class="flash">{str(e)}</div>', status_code=400)
    return RedirectResponse(url="/placement", status_code=303)
