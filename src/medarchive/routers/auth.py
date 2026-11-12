"""Роутер аутентификации: логин/логаут."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_db, get_auth, get_pg_auth
from medarchive.adapters.sqlite.database import Database
from medarchive.services.auth_service import AuthService
from medarchive.services.postgresql_auth_service import PostgreSQLAuthService

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
    pg_auth: PostgreSQLAuthService = Depends(get_pg_auth),
):
    config = request.app.state.config
    if config.auth_backend == "postgresql":
        user = await pg_auth.authenticate(username, password)
        if user is None:
            return request.app.state.templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Неверный логин или пароль"},
                status_code=200,
            )
    else:
        row = await db.fetch_one("SELECT * FROM users WHERE username = ?", (username,))
        if row is None or not auth.verify_password(password, row["password_hash"]):
            return request.app.state.templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Неверный логин или пароль"},
                status_code=200,
            )

        from medarchive.domain.models import User
        user = User.from_row(row)

    token = auth.create_session_token(user)
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        "session",
        token,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        max_age=86400 * 7,
        path="/",
    )
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("session", path="/")
    return response
