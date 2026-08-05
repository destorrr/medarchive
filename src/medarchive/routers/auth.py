"""Роутер аутентификации: логин/логаут."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_db, get_auth
from medarchive.adapters.sqlite.database import Database
from medarchive.services.auth_service import AuthService

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return request.app.state.templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Database = Depends(get_db),
    auth: AuthService = Depends(get_auth),
):
    row = await db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))
    if row is None or not auth.verify_password(password, row["password_hash"]):
        return request.app.state.templates.TemplateResponse(
            request,
            "login.html",
            {"error": "Неверный логин или пароль"},
            status_code=200,
        )

    from medarchive.domain.models import User
    from medarchive.domain.statuses import UserRole

    user = User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=UserRole(row["role"]),
        branch_id=row["branch_id"],
    )
    token = auth.create_session_token(user)

    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("session", token, httponly=True, max_age=86400 * 7)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session")
    return response
