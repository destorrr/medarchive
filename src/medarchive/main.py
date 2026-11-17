"""Точка входа FastAPI-приложения."""
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from medarchive.config import Config
from medarchive.dependencies import init_dependencies, get_current_user


def _origin_tuple(value: str) -> tuple[str, str, int] | None:
    """Normalize an Origin or Referer URL to scheme/host/port."""
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if scheme not in {"http", "https"} or not hostname:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, hostname.rstrip(".").casefold(), port


def create_app(config: Config | None = None) -> FastAPI:
    if config is None:
        config = Config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup: initialize the configured database backend.
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

        try:
            yield
        finally:
            # Shutdown: close database resources, allowing clean app re-initialization.
            import medarchive.dependencies as dependencies
            if dependencies._db is not None:
                try:
                    db = dependencies._db
                    dependencies._db = None
                    await db.close()
                except Exception:
                    pass
            if dependencies._pg_db is not None:
                try:
                    pg_db = dependencies._pg_db
                    dependencies._pg_db = None
                    await pg_db.close()
                except Exception:
                    pass

    app = FastAPI(title="МедАрхив", version="0.1.0", lifespan=lifespan)
    app.state.config = config

    # Resolve package resources from this module, not from the process working directory.
    # This keeps systemd, CLI and test launches consistent.
    package_dir = Path(__file__).resolve().parent
    app.mount("/static", StaticFiles(directory=str(package_dir / "static")), name="static")

    @app.middleware("http")
    async def protect_state_changing_requests(request: Request, call_next):
        """Reject browser state changes initiated from a different origin."""
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            fetch_site = request.headers.get("sec-fetch-site", "").lower()
            if fetch_site == "cross-site":
                return HTMLResponse("Cross-site request rejected", status_code=403)

            source = request.headers.get("origin")
            if source is None:
                source = request.headers.get("referer")
            if source is not None:
                if _origin_tuple(source) != _origin_tuple(str(request.base_url)):
                    return HTMLResponse("Cross-site request rejected", status_code=403)

        return await call_next(request)

    @app.get("/", include_in_schema=False)
    async def root():
        """Send visitors to the sign-in page instead of FastAPI's JSON 404."""
        return RedirectResponse(url="/login", status_code=303)

    # Jinja2 templates
    app.state.templates = Jinja2Templates(directory=str(package_dir / "templates"))

    # Init dependencies
    init_dependencies(config)

    # Routers
    from medarchive.routers.auth import router as auth_router
    app.include_router(auth_router)

    # Keep the PostgreSQL and legacy SQLite application surfaces separate.
    if config.auth_backend == "postgresql":
        from medarchive.routers.postgresql_dashboard import router as pg_dashboard_router
        from medarchive.routers.postgresql_packing import router as pg_packing_router
        from medarchive.routers.postgresql_box_labels import router as pg_box_labels_router
        from medarchive.routers.postgresql_branch import router as pg_branch_router
        from medarchive.routers.postgresql_transfer import router as pg_transfer_router
        from medarchive.routers.postgresql_archive import router as pg_archive_router
        from medarchive.routers.postgresql_central_office import router as pg_central_router
        from medarchive.routers.postgresql_admin import router as pg_admin_router
        from medarchive.routers.postgresql_admin_pages import router as pg_admin_pages_router
        app.include_router(pg_dashboard_router)
        app.include_router(pg_packing_router)
        app.include_router(pg_box_labels_router)
        app.include_router(pg_branch_router)
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
