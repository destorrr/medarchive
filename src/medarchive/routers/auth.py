"""Роутер аутентификации: логин/логаут."""
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_auth, get_pg_database, get_db
from medarchive.adapters.sqlite.database import Database
from medarchive.services.auth_service import AuthService
from medarchive.services.postgresql_auth_service import PostgreSQLAuthService
from medarchive.adapters.postgresql.repositories import UserRepository

router = APIRouter()


@router.get("/login")
async def login_page(request: Request):
    return request.app.state.templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    auth: AuthService = Depends(get_auth),
):
    config = request.app.state.config
    if config.auth_backend == "postgresql":
        pg_db = await get_pg_database()
        async with pg_db.session_factory() as pg_session:
            pg_auth = PostgreSQLAuthService(UserRepository(pg_session), auth)
            user = await pg_auth.authenticate(username, password)
        if user is None:
            return request.app.state.templates.TemplateResponse(
                request,
                "login.html",
                {"error": "Неверный логин или пароль"},
                status_code=200,
            )
    else:
        db = await get_db()
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
    destination = "/admin" if config.auth_backend == "postgresql" and user.role == "admin" else "/dashboard"
    response = RedirectResponse(url=destination, status_code=303)
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
