"""Роутер дашборда."""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse
from medarchive.dependencies import get_current_user
from medarchive.domain.models import User
from medarchive.domain.statuses import UserRole

router = APIRouter()

ROLE_LABELS = {
    UserRole.branch: "Филиал",
    UserRole.archive: "Архив",
    UserRole.viewer: "Просмотр",
    UserRole.admin: "Администратор",
}

MENU_BY_ROLE = {
    UserRole.branch: [
        {"url": "/packing", "label": "📦 Упаковка коробки"},
        {"url": "/transfer", "label": "🚚 Передача курьеру"},
        {"url": "/my-shipments", "label": "📋 Мои отправки"},
        {"url": "/requests/new", "label": "📝 Заявки на возврат"},
        {"url": "/receive-return", "label": "📥 Приём возврата"},
    ],
    UserRole.archive: [
        {"url": "/receive", "label": "📥 Приёмка"},
        {"url": "/placement", "label": "📍 Размещение"},
        {"url": "/requests", "label": "📋 Очередь заявок"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/relocate", "label": "🔄 Перестановка"},
    ],
    UserRole.viewer: [
        {"url": "/dashboard", "label": "📊 Дашборд"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/history", "label": "📜 Журнал движений"},
    ],
    UserRole.admin: [
        {"url": "/dashboard", "label": "📊 Дашборд"},
        {"url": "/packing", "label": "📦 Упаковка"},
        {"url": "/receive", "label": "📥 Приёмка"},
        {"url": "/requests", "label": "📋 Заявки"},
        {"url": "/search", "label": "🔍 Поиск"},
        {"url": "/admin", "label": "⚙️ Справочники"},
    ],
}


def _menu_context(user: User) -> dict:
    return {
        "role_label": ROLE_LABELS.get(user.role, "Неизвестно"),
        "menu_items": MENU_BY_ROLE.get(user.role, []),
    }


@router.get("/dashboard")
async def dashboard(request: Request):
    try:
        user = await get_current_user(request)
    except HTTPException:
        # Без сессии — редирект на логин (как в заглушке из main.py).
        return RedirectResponse(url="/login", status_code=303)
    ctx = _menu_context(user)
    ctx["username"] = user.username
    ctx["request"] = request
    return request.app.state.templates.TemplateResponse(
        request=request, name="dashboard.html", context=ctx
    )
