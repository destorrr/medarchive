"""HTML-страницы администрирования для целевой PostgreSQL-модели."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.adapters.postgresql.models import User
from medarchive.dependencies import get_auth, get_pg_session, require_pg_role
from medarchive.services.postgresql_admin_service import PostgreSQLAdminService

router = APIRouter()
admin_only = require_pg_role("admin")
ROLE_LABELS = {
    "branch": "Филиал",
    "central_office": "Центральный офис",
    "archive": "Архив",
    "admin": "Администратор",
    "viewer": "Просмотр",
}
ROLE_OPTIONS = [
    {"value": key, "label": value} for key, value in ROLE_LABELS.items()
]


def _context(request: Request, user: User, **extra):
    initials = "".join(part[0] for part in user.full_name.split()[:2]).upper()
    if not initials:
        initials = user.username[:1].upper()
    ctx = {
        "request": request,
        "user": user,
        "username": user.username,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "user_role_pill": ROLE_LABELS.get(user.role, user.role),
        "user_full_name": user.full_name or user.username,
        "user_initials": initials,
        "user_branch": ROLE_LABELS.get(user.role, user.role),
        "menu_items": [
            {"url": "/admin", "label": "⚙️ Справочники"},
            {"url": "/admin/branches", "label": "🏢 Филиалы"},
            {"url": "/admin/users", "label": "👤 Пользователи"},
        ],
    }
    ctx.update(extra)
    return ctx


def _redirect(path: str):
    return RedirectResponse(path, status_code=303)


@router.get("/admin")
async def admin_home(
    request: Request,
    user: User = Depends(admin_only),
):
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/postgresql_index.html",
        context=_context(request, user),
    )


@router.get("/admin/branches")
async def branches_page(
    request: Request,
    user: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    branches = await PostgreSQLAdminService(session).list_branches()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/postgresql_branches.html",
        context=_context(request, user, branches=branches),
    )


@router.post("/admin/branches")
async def branches_create(
    code: str = Form(...),
    name: str = Form(...),
    _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLAdminService(session).create_branch(code=code, name=name)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Код филиала уже используется") from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
    return _redirect("/admin/branches")


@router.post("/admin/branches/{branch_id}/edit")
async def branches_edit(
    branch_id: uuid.UUID,
    code: str = Form(...),
    name: str = Form(...),
    _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLAdminService(session).update_branch(
            branch_id, code=code, name=name
        )
        await session.commit()
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Код филиала уже используется") from exc
    except Exception:
        await session.rollback()
        raise
    return _redirect("/admin/branches")


@router.post("/admin/branches/{branch_id}/status")
async def branches_status(
    branch_id: uuid.UUID,
    is_active: bool = Form(...),
    _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLAdminService(session).set_branch_active(
            branch_id, is_active
        )
        await session.commit()
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
    return _redirect("/admin/branches")


@router.get("/admin/users")
async def users_page(
    request: Request,
    user: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    service = PostgreSQLAdminService(session)
    users = await service.list_users()
    branches = await service.list_branches()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="admin/postgresql_users.html",
        context=_context(
            request, user, users=users, branches=branches,
            role_options=ROLE_OPTIONS,
        ),
    )


@router.post("/admin/users")
async def users_create(
    username: str = Form(...),
    full_name: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    branch_id: str = Form(""),
    _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        await PostgreSQLAdminService(session).create_user(
            username=username,
            full_name=full_name,
            password_hash=get_auth().hash_password(password),
            role=role,
            branch_id=uuid.UUID(branch_id) if branch_id else None,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Логин уже используется или нарушено ограничение БД") from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Изменение конфликтует с ограничениями БД") from exc
    except Exception:
        await session.rollback()
        raise
    return _redirect("/admin/users")


@router.post("/admin/users/{user_id}/edit")
async def users_edit(
    user_id: uuid.UUID,
    full_name: str = Form(...),
    role: str = Form(...),
    branch_id: str = Form(""),
    password: str = Form(""),
    is_active: bool = Form(False),
    actor: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    if actor.id == user_id and (not is_active or role != "admin"):
        raise HTTPException(
            status_code=409,
            detail="Нельзя отключить себя или снять с себя роль администратора",
        )
    try:
        await PostgreSQLAdminService(session).update_user(
            user_id,
            full_name=full_name,
            role=role,
            branch_id=uuid.UUID(branch_id) if branch_id else None,
            password_hash=get_auth().hash_password(password) if password else None,
            is_active=is_active,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return _redirect("/admin/users")
