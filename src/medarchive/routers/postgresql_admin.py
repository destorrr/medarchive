"""Административные API целевой PostgreSQL-модели."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from medarchive.dependencies import get_auth, get_pg_session, require_pg_role
from medarchive.adapters.postgresql.models import User
from medarchive.services.postgresql_admin_service import PostgreSQLAdminService

router = APIRouter(prefix="/api/admin", tags=["postgresql-admin"])
admin_only = require_pg_role("admin")


class BranchInput(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)

    @field_validator("code", "name")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Поле не должно быть пустым")
        return value


class UserCreateInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=12)
    role: str
    branch_id: uuid.UUID | None = None


class UserUpdateInput(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    role: str
    branch_id: uuid.UUID | None = None
    password: str | None = Field(default=None, min_length=12)
    is_active: bool | None = None


def _branch_json(branch):
    return {
        "id": str(branch.id), "code": branch.code, "name": branch.name,
        "is_active": branch.is_active,
    }


def _user_json(user):
    return {
        "id": str(user.id), "username": user.username,
        "full_name": user.full_name, "role": user.role,
        "branch_id": str(user.branch_id) if user.branch_id else None,
        "is_active": user.is_active, "created_at": user.created_at.isoformat(),
    }


@router.get("/branches")
async def list_branches(
    _: User = Depends(admin_only), session: AsyncSession = Depends(get_pg_session)
):
    return [_branch_json(item) for item in await PostgreSQLAdminService(session).list_branches()]


@router.post("/branches", status_code=201)
async def create_branch(
    data: BranchInput, _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        branch = await PostgreSQLAdminService(session).create_branch(
            code=data.code, name=data.name
        )
        result = _branch_json(branch)
        await session.commit()
        return result
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        await session.rollback()
        if "unique" in str(exc).lower():
            raise HTTPException(status_code=409, detail="Код филиала уже используется") from exc
        raise


@router.put("/branches/{branch_id}")
async def update_branch(
    branch_id: uuid.UUID, data: BranchInput, _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        branch = await PostgreSQLAdminService(session).update_branch(
            branch_id, code=data.code, name=data.name
        )
        result = _branch_json(branch)
        await session.commit()
        return result
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/branches/{branch_id}/deactivate")
async def deactivate_branch(
    branch_id: uuid.UUID, _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        branch = await PostgreSQLAdminService(session).set_branch_active(branch_id, False)
        result = _branch_json(branch)
        await session.commit()
        return result
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Изменение конфликтует с ограничениями БД") from exc


@router.post("/branches/{branch_id}/activate")
async def activate_branch(
    branch_id: uuid.UUID, _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    try:
        branch = await PostgreSQLAdminService(session).set_branch_active(branch_id, True)
        result = _branch_json(branch)
        await session.commit()
        return result
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/users")
async def list_users(
    _: User = Depends(admin_only), session: AsyncSession = Depends(get_pg_session)
):
    return [_user_json(item) for item in await PostgreSQLAdminService(session).list_users()]


@router.post("/users", status_code=201)
async def create_user(
    data: UserCreateInput, _: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    auth = get_auth()
    try:
        user = await PostgreSQLAdminService(session).create_user(
            username=data.username, full_name=data.full_name,
            password_hash=auth.hash_password(data.password),
            role=data.role, branch_id=data.branch_id,
        )
        result = _user_json(user)
        await session.commit()
        return result
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Логин уже используется или нарушено ограничение БД") from exc


@router.put("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID, data: UserUpdateInput,
    actor: User = Depends(admin_only),
    session: AsyncSession = Depends(get_pg_session),
):
    if actor.id == user_id and (data.is_active is False or data.role != "admin"):
        raise HTTPException(status_code=409, detail="Нельзя отключить себя или снять с себя роль администратора")
    auth = get_auth()
    try:
        user = await PostgreSQLAdminService(session).update_user(
            user_id, full_name=data.full_name, role=data.role,
            branch_id=data.branch_id,
            password_hash=auth.hash_password(data.password) if data.password else None,
            is_active=data.is_active,
        )
        result = _user_json(user)
        await session.commit()
        return result
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Изменение конфликтует с ограничениями БД",
        ) from exc
