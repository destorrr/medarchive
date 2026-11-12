"""Точка входа FastAPI-приложения."""
from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
from medarchive.config import Config
from medarchive.dependencies import init_dependencies, get_current_user


def create_app(config: Config | None = None) -> FastAPI:
    if config is None:
        config = Config()

    app = FastAPI(title="МедАрхив", version="0.1.0")
    app.state.config = config

    # Jinja2 templates
    app.state.templates = Jinja2Templates(directory="src/medarchive/templates")

    # Init dependencies
    init_dependencies(config)

    # Startup: init DB
    @app.on_event("startup")
    async def startup():
        from medarchive.dependencies import get_db
        db = await get_db()
        await db.init()
        if config.postgres_enabled:
            from medarchive.dependencies import get_pg_database
            pg_db = await get_pg_database()
            await pg_db.health_check()

    # Shutdown: close DB (safe — no hang on cleanup errors)
    @app.on_event("shutdown")
    async def shutdown():
        import medarchive.dependencies as dependencies
        if dependencies._db is not None:
            try:
                db = dependencies._db
                dependencies._db = None  # prevent double-close
                await db.close()
            except Exception:
                pass
        if dependencies._pg_db is not None:
            try:
                pg_db = dependencies._pg_db
                dependencies._pg_db = None  # allow clean app re-initialization
                await pg_db.close()
            except Exception:
                pass

    # Routers
    from medarchive.routers.auth import router as auth_router
    app.include_router(auth_router)

    # Packing router (Task 3)
    try:
        from medarchive.routers.packing import router as packing_router
        app.include_router(packing_router)
    except ImportError:
        pass

    # Archive router (Task 7: приёмка и размещение)
    try:
        from medarchive.routers.archive import router as archive_router
        app.include_router(archive_router)
    except ImportError:
        pass

    # Dashboard router imported later (Task 7)
    try:
        from medarchive.routers.dashboard import router as dashboard_router
        app.include_router(dashboard_router)
    except ImportError:
        # Временный защищённый роут-заглушка: без сессии → редирект на /login.
        # Исчезнет автоматически, когда появится routers/dashboard.py (Task 7).
        @app.get("/dashboard")
        async def dashboard_stub(request: Request):
            try:
                await get_current_user(request)
            except HTTPException:
                return RedirectResponse(url="/login", status_code=303)
            return HTMLResponse("<h1>МедАрхив</h1><p>Дашборд появится в Task 7.</p>")

    # PostgreSQL administration API is enabled only with PostgreSQL authentication.
    if config.auth_backend == "postgresql":
        from medarchive.routers.postgresql_admin import router as pg_admin_router
        app.include_router(pg_admin_router)

    # Admin router (Этап 4: справочники)
    try:
        from medarchive.routers.admin import router as admin_router
        app.include_router(admin_router)
    except ImportError:
        pass

    # Transfer router (Task 5)
    try:
        from medarchive.routers.transfer import router as transfer_router
        app.include_router(transfer_router)
    except ImportError:
        pass

    # Archive router (Task 7)
    try:
        from medarchive.routers.archive import router as archive_router
        app.include_router(archive_router)
    except ImportError:
        pass

    return app
