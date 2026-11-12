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
        if config.auth_backend == "sqlite":
            from medarchive.dependencies import get_db
            db = await get_db()
            await db.init()
        if config.postgres_enabled:
            from medarchive.dependencies import get_pg_database
            from medarchive.services.postgresql_bootstrap import ensure_system_locations
            pg_db = await get_pg_database()
            await pg_db.health_check()
            async with pg_db.session_factory() as session:
                async with session.begin():
                    await ensure_system_locations(session)

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

    # Keep the PostgreSQL and legacy SQLite application surfaces separate.
    if config.auth_backend == "postgresql":
        from medarchive.routers.postgresql_dashboard import router as pg_dashboard_router
        from medarchive.routers.postgresql_packing import router as pg_packing_router
        from medarchive.routers.postgresql_transfer import router as pg_transfer_router
        from medarchive.routers.postgresql_archive import router as pg_archive_router
        from medarchive.routers.postgresql_central_office import router as pg_central_router
        from medarchive.routers.postgresql_admin import router as pg_admin_router
        from medarchive.routers.postgresql_admin_pages import router as pg_admin_pages_router
        app.include_router(pg_dashboard_router)
        app.include_router(pg_packing_router)
        app.include_router(pg_transfer_router)
        app.include_router(pg_archive_router)
        app.include_router(pg_central_router)
        app.include_router(pg_admin_router)
        app.include_router(pg_admin_pages_router)
    else:
        from medarchive.routers.packing import router as packing_router
        from medarchive.routers.archive import router as archive_router
        from medarchive.routers.dashboard import router as dashboard_router
        from medarchive.routers.admin import router as admin_router
        from medarchive.routers.transfer import router as transfer_router
        app.include_router(packing_router)
        app.include_router(archive_router)
        app.include_router(dashboard_router)
        app.include_router(admin_router)
        app.include_router(transfer_router)

    return app
