"""Админ-роутер: справочники (филиалы, пользователи, ...), QR полок, инциденты, бэкап."""
import base64
import io
import shutil

import qrcode
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse, Response, FileResponse
from medarchive.dependencies import get_db, get_auth, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import (
    BranchRepositoryImpl,
    UserRepositoryImpl,
    CourierRepositoryImpl,
    SectionRepositoryImpl,
    ShelfRepositoryImpl,
    CardRepositoryImpl,
    BoxRepositoryImpl,
)
from medarchive.routers.dashboard import _menu_context
from medarchive.services.auth_service import AuthService
from medarchive.services.incident_service import IncidentService
from medarchive.services.backup_service import BackupService

router = APIRouter()

ROLE_LABELS = {
    UserRole.branch: "Филиал",
    UserRole.archive: "Архив",
    UserRole.viewer: "Просмотр",
    UserRole.admin: "Администратор",
}


def _shelf_qr_text(shelf: dict) -> str:
    """Текст QR-кода полки: S{section_id}-P{shelf_id}."""
    return f"S{shelf['section_id']}-P{shelf['id']}"


def _qr_png_bytes(text: str) -> bytes:
    """PNG-байты QR-кода для заданного текста."""
    qr = qrcode.make(text)
    buf = io.BytesIO()
    qr.save(buf, "PNG")
    buf.seek(0)
    return buf.getvalue()


def _qr_base64(text: str) -> str:
    """base64 PNG QR-кода (без префикса data URI)."""
    return base64.b64encode(_qr_png_bytes(text)).decode()


def _shelf_qr_base64(shelf: dict) -> str:
    """base64 QR для полки: берём сохранённый, при отсутствии генерируем."""
    if shelf.get("qr_code"):
        return shelf["qr_code"]
    return _qr_base64(_shelf_qr_text(shelf))


def _ctx(user: User, request: Request, **extra) -> dict:
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "user": user})
    ctx.update(extra)
    return ctx


@router.get("/admin")
async def admin_index(request: Request, user: User = Depends(require_role(UserRole.admin))):
    return request.app.state.templates.TemplateResponse(
        request=request, name="admin/index.html", context=_ctx(user, request)
    )


@router.get("/admin/branches")
async def branches_page(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    branch_repo = BranchRepositoryImpl(db)
    branches = await branch_repo.list_all()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/branches.html",
        context=_ctx(user, request, branches=branches),
    )


@router.post("/admin/branches")
async def branches_create(
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    branch_repo = BranchRepositoryImpl(db)
    try:
        await branch_repo.create(name=name.strip(), code=code.strip())
    except Exception:
        pass  # дубликат кода и т.п. — просто остаёмся на странице
    return RedirectResponse(url="/admin/branches", status_code=303)


@router.post("/admin/branches/{branch_id}/edit")
async def branches_edit(
    branch_id: int,
    request: Request,
    name: str = Form(...),
    code: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    branch_repo = BranchRepositoryImpl(db)
    await branch_repo.update(branch_id, name=name.strip(), code=code.strip())
    return RedirectResponse(url="/admin/branches", status_code=303)


@router.post("/admin/branches/{branch_id}/delete")
async def branches_delete(
    branch_id: int,
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    branch_repo = BranchRepositoryImpl(db)
    # Снять привязку пользователей к филиалу, чтобы удаление не падало на FK
    await db.execute(
        "UPDATE users SET branch_id = NULL WHERE branch_id = ?", (branch_id,)
    )
    try:
        await branch_repo.delete(branch_id)
    except Exception:
        pass  # остались ссылки (коробки/отправки) — филиал не удаляем
    return RedirectResponse(url="/admin/branches", status_code=303)


@router.get("/admin/users")
async def users_page(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    user_repo = UserRepositoryImpl(db)
    branch_repo = BranchRepositoryImpl(db)
    users = await user_repo.list_all()
    branches = await branch_repo.list_all()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context=_ctx(
            user,
            request,
            users=users,
            branches=branches,
            roles=list(UserRole),
            role_labels=ROLE_LABELS,
        ),
    )


@router.post("/admin/users")
async def users_create(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    branch_id: str = Form(""),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
    auth: AuthService = Depends(get_auth),
):
    user_repo = UserRepositoryImpl(db)
    try:
        await user_repo.create(
            username=username.strip(),
            password_hash=auth.hash_password(password),
            role=role,
            branch_id=int(branch_id) if branch_id else None,
        )
    except Exception:
        pass  # дубликат логина и т.п. — остаёмся на странице
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/edit")
async def users_edit(
    user_id: int,
    request: Request,
    role: str = Form(...),
    branch_id: str = Form(""),
    password: str = Form(""),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
    auth: AuthService = Depends(get_auth),
):
    user_repo = UserRepositoryImpl(db)
    kwargs: dict = {
        "role": role,
        "branch_id": int(branch_id) if branch_id else None,
    }
    if password:
        kwargs["password_hash"] = auth.hash_password(password)
    await user_repo.update(user_id, **kwargs)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/delete")
async def users_delete(
    user_id: int,
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    if user.id == user_id:
        # Нельзя удалить самого себя
        return RedirectResponse(url="/admin/users", status_code=303)
    user_repo = UserRepositoryImpl(db)
    await user_repo.delete(user_id)
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/admin/couriers")
async def couriers_page(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    courier_repo = CourierRepositoryImpl(db)
    couriers = await courier_repo.list_all()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/couriers.html",
        context=_ctx(user, request, couriers=couriers),
    )


@router.post("/admin/couriers")
async def couriers_create(
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    courier_repo = CourierRepositoryImpl(db)
    await courier_repo.create(name=name.strip(), phone=phone.strip() or None)
    return RedirectResponse(url="/admin/couriers", status_code=303)


@router.post("/admin/couriers/{courier_id}/edit")
async def couriers_edit(
    courier_id: int,
    request: Request,
    name: str = Form(...),
    phone: str = Form(""),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    courier_repo = CourierRepositoryImpl(db)
    await courier_repo.update(
        courier_id, name=name.strip(), phone=phone.strip() or None
    )
    return RedirectResponse(url="/admin/couriers", status_code=303)


@router.post("/admin/couriers/{courier_id}/delete")
async def couriers_delete(
    courier_id: int,
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    courier_repo = CourierRepositoryImpl(db)
    await courier_repo.delete(courier_id)
    return RedirectResponse(url="/admin/couriers", status_code=303)


@router.get("/admin/sections")
async def sections_page(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    section_repo = SectionRepositoryImpl(db)
    shelf_repo = ShelfRepositoryImpl(db)
    # fetch_all возвращает sqlite3.Row (нельзя присваивать поля) — делаем dict,
    # чтобы добавить вложенные полки в секцию
    sections = [dict(s) for s in await section_repo.list_all()]
    # Для каждой секции — её полки
    for section in sections:
        section["shelves"] = await shelf_repo.get_by_section(section["id"])
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/sections.html",
        context=_ctx(user, request, sections=sections),
    )


@router.post("/admin/sections")
async def sections_create(
    request: Request,
    name: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    section_repo = SectionRepositoryImpl(db)
    await section_repo.create(name=name.strip())
    return RedirectResponse(url="/admin/sections", status_code=303)


@router.post("/admin/sections/{section_id}/edit")
async def sections_edit(
    section_id: int,
    request: Request,
    name: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    section_repo = SectionRepositoryImpl(db)
    await section_repo.update(section_id, name=name.strip())
    return RedirectResponse(url="/admin/sections", status_code=303)


@router.post("/admin/sections/{section_id}/delete")
async def sections_delete(
    section_id: int,
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    section_repo = SectionRepositoryImpl(db)
    try:
        await section_repo.delete(section_id)
    except Exception:
        pass  # внутри секции есть полки (FK) — секцию не удаляем
    return RedirectResponse(url="/admin/sections", status_code=303)


@router.post("/admin/shelves")
async def shelves_create(
    request: Request,
    section_id: int = Form(...),
    name: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    shelf_repo = ShelfRepositoryImpl(db)
    await shelf_repo.create(section_id=section_id, name=name.strip())
    return RedirectResponse(url="/admin/sections", status_code=303)


@router.post("/admin/shelves/{shelf_id}/edit")
async def shelves_edit(
    shelf_id: int,
    request: Request,
    name: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    shelf_repo = ShelfRepositoryImpl(db)
    await shelf_repo.update(shelf_id, name=name.strip())
    return RedirectResponse(url="/admin/sections", status_code=303)


@router.post("/admin/shelves/{shelf_id}/delete")
async def shelves_delete(
    shelf_id: int,
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    shelf_repo = ShelfRepositoryImpl(db)
    await shelf_repo.delete(shelf_id)
    return RedirectResponse(url="/admin/sections", status_code=303)


# --- QR-коды полок (Task 5-QR) ---


@router.get("/admin/shelves/{shelf_id}/qr")
async def shelf_qr_png(
    shelf_id: int,
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    """PNG с QR-кодом одной полки."""
    shelf_repo = ShelfRepositoryImpl(db)
    shelf = await shelf_repo.get_by_id(shelf_id)
    if shelf is None:
        raise HTTPException(status_code=404, detail="Полка не найдена")
    png = _qr_png_bytes(_shelf_qr_text(shelf))
    return Response(content=png, media_type="image/png")


@router.post("/admin/shelves/{shelf_id}/generate-qr")
async def shelf_generate_qr(
    shelf_id: int,
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Генерирует/обновляет QR-код полки и сохраняет в БД."""
    shelf_repo = ShelfRepositoryImpl(db)
    try:
        await shelf_repo.generate_qr(shelf_id)
    except ValueError:
        pass  # полка не найдена — просто остаёмся на странице
    return RedirectResponse(url="/admin/sections", status_code=303)


@router.get("/admin/shelves/qr-sheet")
async def shelves_qr_sheet(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    """HTML-страница с сеткой QR-кодов всех полок для печати на A4."""
    shelf_repo = ShelfRepositoryImpl(db)
    shelves = await shelf_repo.list_all()
    stickers = [
        {
            "label": _shelf_qr_text(dict(sh)),
            "qr_b64": _shelf_qr_base64(dict(sh)),
        }
        for sh in shelves
    ]
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/qr_sheet.html",
        context=_ctx(user, request, stickers=stickers),
    )


# --- Инциденты (Task 6) ---


@router.get("/admin/incidents")
async def incidents_page(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    svc = IncidentService()
    lost_boxes = await svc.get_lost_boxes(db)
    damaged_shipments = await svc.get_damaged_shipments(db)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/incidents.html",
        context=_ctx(
            user,
            request,
            lost_boxes=lost_boxes,
            damaged_shipments=damaged_shipments,
        ),
    )


@router.post("/admin/incidents/resolve-lost-box")
async def incidents_resolve_lost_box(
    request: Request,
    box_id: int = Form(...),
    action: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Разбор потерянной коробки: вернуть карты или считать утерянными."""
    svc = IncidentService()
    try:
        await svc.resolve_lost_box(db, box_id, action)
    except ValueError:
        pass
    return RedirectResponse(url="/admin/incidents", status_code=303)


@router.post("/admin/incidents/resolve-card")
async def incidents_resolve_card(
    request: Request,
    card_id: int = Form(...),
    new_status: str = Form(...),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Принудительная смена статуса карты (admin only)."""
    svc = IncidentService()
    try:
        await svc.resolve_card_status(db, card_id, new_status)
    except ValueError:
        pass
    return RedirectResponse(url="/admin/incidents", status_code=303)


# --- Бэкап и восстановление (Task 7) ---


@router.get("/admin/backup")
async def backup_page(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    db_info = BackupService.get_db_info(db.config.db_path)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/backup.html",
        context=_ctx(user, request, db_info=db_info),
    )


@router.post("/admin/backup/create")
async def backup_create(
    request: Request,
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Скачивание .db файла как FileResponse."""
    db_path = db.config.db_path
    _, filename = BackupService.create_backup(db_path)
    return FileResponse(
        path=db_path,
        filename=filename,
        media_type="application/octet-stream",
    )


@router.post("/admin/backup/restore")
async def backup_restore(
    request: Request,
    file: UploadFile = File(...),
    confirm: str = Form(""),
    user: User = Depends(require_role(UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Восстановление БД из загруженного .db файла (только с подтверждением)."""
    if confirm != "yes":
        return RedirectResponse(url="/admin/backup", status_code=303)

    data = await file.read()
    db_path = db.config.db_path

    # Сбрасываем WAL в основной файл, чтобы копия и restore были консистентны
    try:
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass

    # Авто-бэкап текущего состояния перед восстановлением
    try:
        shutil.copy2(db_path, db_path + ".pre_restore_backup")
    except OSError:
        pass

    BackupService.restore_backup(db_path, data)
    return RedirectResponse(url="/admin/backup", status_code=303)
