"""Админ-роутер: справочники (филиалы, пользователи, ...)."""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
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
)
from medarchive.routers.dashboard import _menu_context
from medarchive.services.auth_service import AuthService

router = APIRouter()

ROLE_LABELS = {
    UserRole.branch: "Филиал",
    UserRole.archive: "Архив",
    UserRole.viewer: "Просмотр",
    UserRole.admin: "Администратор",
}


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
    sections = await section_repo.list_all()
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
