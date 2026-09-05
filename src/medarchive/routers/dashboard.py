"""Роутер дашборда и общих страниц."""
from datetime import datetime
from urllib.parse import quote, unquote

from fastapi import APIRouter, Request, HTTPException, Depends, Form
from fastapi.responses import RedirectResponse
from markupsafe import Markup, escape
from medarchive.dependencies import get_current_user, get_db, require_role
from medarchive.domain.models import User
from medarchive.domain.statuses import (
    UserRole,
    CARD_STATUS_LABELS,
    BOX_STATUS_LABELS,
    REQUEST_STATUS_LABELS,
)
from medarchive.adapters.sqlite.database import Database
from medarchive.adapters.sqlite.repositories import (
    CardRepositoryImpl,
    BoxRepositoryImpl,
    CourierRepositoryImpl,
)
from medarchive.services.request_service import RequestService
from medarchive.services.return_service import ReturnService

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


def _user_full_name(user: User) -> str:
    """Имя для профиля сайдбара: users.full_name, при пустом — username."""
    return user.full_name or user.username


def _user_initials(user: User) -> str:
    """Инициалы для аватара: первые буквы первого и второго слова full_name
    (например «Иванова П.С.» → «ИП»); если full_name пусто/одно слово —
    первая буква username в верхнем регистре."""
    words = [w for w in (user.full_name or "").split() if w]
    if len(words) >= 2:
        return (words[0][0] + words[1][0]).upper()
    username = user.username or "?"
    return username[0].upper()


def _menu_context(user: User) -> dict:
    """Общий контекст для шаблонов, расширяющих base.html.

    Сайдбар v5 (раунд 2): подпись-группа роли (role_label), пункты меню
    (menu_items, у Архива на «Очередь заявок» — badge со счётчиком активных
    заявок, если > 0), профиль внизу — user_full_name (ФИО или username),
    user_initials (аватар), user_branch (имя филиала или подпись роли),
    user_role_pill (пилюля роли). user_login оставлен для совместимости.
    active_requests_count кладёт на user dependencies.get_current_user
    (роль archive).
    """
    role_label = ROLE_LABELS.get(user.role, "Неизвестно")
    active_requests = getattr(user, "active_requests_count", 0) or 0
    items = []
    for it in MENU_BY_ROLE.get(user.role, []):
        row = dict(it)
        if user.role == UserRole.archive and row.get("url") == "/requests" and active_requests > 0:
            row["badge"] = str(active_requests)
        items.append(row)
    return {
        "role_label": role_label,
        "user_branch": getattr(user, "branch_name", None) or role_label,
        "user_login": user.username,
        "menu_items": items,
        "user_full_name": _user_full_name(user),
        "user_initials": _user_initials(user),
        "user_role_pill": role_label,
    }


def _flash_redirect(url: str, kind: str, text: str) -> RedirectResponse:
    """303-редирект с одноразовым флеш-сообщением.

    Сообщение кладётся в cookie ma_flash значением f"{kind}|{text}"
    (kind: success|error, path="/"). Значение квотируется urllib.parse.quote:
    Starlette сериализует cookie в latin-1, и сырая кириллица упала бы
    с UnicodeEncodeError. GET-страницы контура читают его через _pop_flash
    при рендере и удаляют cookie в ответе — флеш показывается ровно один раз.
    """
    response = RedirectResponse(url=url, status_code=303)
    response.set_cookie(
        "ma_flash", quote(f"{kind}|{text}", safe="|"), path="/", httponly=True
    )
    return response


def _pop_flash(request: Request) -> dict | None:
    """Прочитать флеш из cookie ma_flash: {"type": kind, "text": text} или None.

    kind ограничен whitelist'ом success|error (значение cookie контролируется
    клиентом — не даём подставить произвольный класс в flash-<type>).
    """
    raw = request.cookies.get("ma_flash")
    if not raw:
        return None
    kind, sep, text = unquote(raw).partition("|")
    if not sep or kind not in ("success", "error"):
        return None
    return {"type": kind, "text": text}


def _render_page(request: Request, name: str, ctx: dict):
    """Рендер GET-страницы с одноразовым флешем (удобный паттерн).

    Если в запросе есть cookie ma_flash — кладём его в ctx["flash"]
    (base.html рисует блок .flash.flash-<type>) и удаляем cookie
    в ответе: флеш не повторится при следующем GET.
    """
    flash = _pop_flash(request)
    if flash is not None:
        ctx["flash"] = flash
    response = request.app.state.templates.TemplateResponse(
        request=request, name=name, context=ctx
    )
    if flash is not None:
        response.delete_cookie("ma_flash", path="/")
    return response


# Статусы (сырые значения из БД) → русские подписи и классы бейджей base.html.
_CARD_STATUS_LABELS_BY_VALUE: dict[str, str] = {
    s.value: label for s, label in CARD_STATUS_LABELS.items()
}
_BOX_STATUS_LABELS_BY_VALUE: dict[str, str] = {
    s.value: label for s, label in BOX_STATUS_LABELS.items()
}
_REQUEST_STATUS_LABELS_BY_VALUE: dict[str, str] = {
    s.value: label for s, label in REQUEST_STATUS_LABELS.items()
}


def _format_created_at(value) -> str:
    """Дата создания «YYYY-MM-DD HH:MM:SS» (локальное время в БД)
    → «ДД.ММ.ГГГГ ЧЧ:ММ» для показа в таблицах контура.

    Нераспознанное значение (None/другой формат) возвращается как есть —
    страница не должна падать из-за нестандартной строки в данных.
    """
    if not value:
        return ""
    try:
        return datetime.strptime(
            str(value), "%Y-%m-%d %H:%M:%S"
        ).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return str(value)


def _prepare_request_rows(requests: list[dict]) -> list[dict]:
    """Обогащение строк заявок для шаблона (по образцу _prepare_search_rows):
    status_label — русская подпись (REQUEST_STATUS_LABELS), status_badge —
    класс badge-<status>, created_at_display — дата «ДД.ММ.ГГГГ ЧЧ:ММ».
    Используется /requests/my и /requests (очередь, задача 4)."""
    prepared: list[dict] = []
    for req in requests:
        row = dict(req)
        status = row.get("status") or ""
        row["status_label"] = _REQUEST_STATUS_LABELS_BY_VALUE.get(status, status)
        row["status_badge"] = f"badge-{status}"
        row["created_at_display"] = _format_created_at(row.get("created_at"))
        prepared.append(row)
    return prepared


def highlight_match(text: str, query: str) -> Markup:
    """Экранированная серверная подсветка <mark> первого вхождения query.

    Поиск — регистронезависимый через .lower() (кириллица; дизайн /search,
    п.2). Все сегменты экранируются markupsafe.escape — никаких f-string
    конкатенаций неэкранированных данных в HTML (дизайн /search, п.5).
    """
    hay = str(text)
    needle = (query or "").strip().lower()
    if not needle:
        return Markup(escape(hay))
    idx = hay.lower().find(needle)
    if idx < 0:
        return Markup(escape(hay))
    end = idx + len(needle)
    # Все части — Markup (escape() уже возвращает Markup, теги оборачиваем
    # отдельно): Markup + str экранировал бы литеральные <mark>-теги.
    return (
        escape(hay[:idx])
        + Markup("<mark>")
        + escape(hay[idx:end])
        + Markup("</mark>")
        + escape(hay[end:])
    )


def _request_new_context(
    request: Request, user: User, *, results, search_name: str
) -> dict:
    """Контекст страницы /requests/new: базовый + результаты поиска.

    has_branch — реально ли к учётной записи привязан филиал: подсказку
    про авто-филиал показываем только тогда (у роли branch там имя филиала,
    у пользователя без филиала — подпись роли, подсказка не нужна).
    """
    ctx = _menu_context(user)
    ctx.update({
        "request": request,
        "username": user.username,
        "results": results,
        "search_name": search_name,
        "has_branch": user.branch_id is not None,
    })
    return ctx


def _card_result_row(card) -> dict:
    """Строка результата поиска карты: сырой статус + русская подпись
    (CARD_STATUS_LABELS) и класс бейджа badge-<status> для шаблона."""
    status = card.status.value
    return {
        "id": card.id,
        "full_name": card.full_name,
        "status": status,
        "status_label": _CARD_STATUS_LABELS_BY_VALUE.get(status, status),
        "status_badge": f"badge-{status}",
    }


def _prepare_search_rows(cards: list[dict], boxes: list[dict], query: str):
    """Обогащение строк результата для шаблонов: русская подпись статуса
    (карты — CARD_STATUS_LABELS, коробки — BOX_STATUS_LABELS), класс бейджа
    badge-<status> и экранированная подсветка <mark> совпадения."""
    prepared_cards: list[dict] = []
    for card in cards:
        row = dict(card)
        status = row.get("status") or ""
        row["status_label"] = _CARD_STATUS_LABELS_BY_VALUE.get(status, status)
        row["status_badge"] = f"badge-{status}"
        row["full_name"] = highlight_match(row["full_name"], query)
        prepared_cards.append(row)
    prepared_boxes: list[dict] = []
    for box in boxes:
        row = dict(box)
        status = row.get("status") or ""
        row["status_label"] = _BOX_STATUS_LABELS_BY_VALUE.get(status, status)
        row["status_badge"] = f"badge-{status}"
        row["box_number"] = highlight_match(row["box_number"], query)
        prepared_boxes.append(row)
    return prepared_cards, prepared_boxes


async def _search_context(
    request: Request, user: User, query: str | None, db: Database
) -> dict:
    """Общий контекст поиска для страницы и htmx-фрагмента: query, cards, boxes."""
    from medarchive.services.search_service import SearchService

    query = (query or "").strip()
    cards: list[dict] = []
    boxes: list[dict] = []
    if query:
        svc = SearchService(db)
        cards = await svc.search_cards(query)
        boxes = await svc.search_boxes(query)
    cards, boxes = _prepare_search_rows(cards, boxes, query)
    ctx = _menu_context(user)
    ctx.update({
        "request": request,
        "username": user.username,
        "query": query,
        "cards": cards,
        "boxes": boxes,
    })
    return ctx


@router.get("/dashboard")
async def dashboard(request: Request):
    try:
        user = await get_current_user(request)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)
    db = await get_db()
    ctx = _menu_context(user)
    ctx["username"] = user.username
    ctx["request"] = request

    # Статистика
    total_cards = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards")
    cards_placed = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards WHERE status='placed'")
    cards_in_branch = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards WHERE status='in_branch'")
    cards_in_transit = await db.fetch_one("SELECT COUNT(*) as cnt FROM cards WHERE status='in_transit'")
    total_boxes = await db.fetch_one("SELECT COUNT(*) as cnt FROM boxes")
    open_requests = await db.fetch_one("SELECT COUNT(*) as cnt FROM requests WHERE status IN ('new','in_progress')")

    from medarchive.services.movement_service import MovementService
    ms = MovementService(db)
    recent = await ms.get_recent_movements(5)

    ctx.update({
        "total_cards": total_cards["cnt"],
        "cards_placed": cards_placed["cnt"],
        "cards_in_branch": cards_in_branch["cnt"],
        "cards_in_transit": cards_in_transit["cnt"],
        "total_boxes": total_boxes["cnt"],
        "open_requests": open_requests["cnt"],
        "recent_movements": recent,
    })
    return request.app.state.templates.TemplateResponse(
        request=request, name="dashboard.html", context=ctx
    )


@router.get("/search")
async def search_page(
    request: Request,
    query: str | None = None,
    user: User = Depends(require_role(UserRole.archive, UserRole.viewer, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Страница поиска: GET-параметр query (нет-JS фолбэк и прямые ссылки),
    результаты рендерит search.html (включает search_fragment.html)."""
    ctx = await _search_context(request, user, query, db)
    return request.app.state.templates.TemplateResponse(
        request=request, name="search.html", context=ctx
    )


@router.get("/search/results")
async def search_results_fragment(
    request: Request,
    query: str | None = None,
    user: User = Depends(require_role(UserRole.archive, UserRole.viewer, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """htmx-фрагмент живого поиска: только блок результатов (#search-results),
    без полной страницы."""
    ctx = await _search_context(request, user, query, db)
    return request.app.state.templates.TemplateResponse(
        request=request, name="search_fragment.html", context=ctx
    )


@router.post("/search")
async def search_submit_legacy(
    query: str = Form(...),
    user: User = Depends(require_role(UserRole.archive, UserRole.viewer, UserRole.admin)),
):
    """Legacy POST-форма: 303-редирект на GET /search?query=… (совместимость)."""
    return RedirectResponse(url=f"/search?query={quote(query)}", status_code=303)


@router.get("/my-shipments")
async def my_shipments(
    request: Request,
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
):
    db = await get_db()
    rows = await db.fetch_all(
        """SELECT s.*, b.box_number, c.name as courier_name
           FROM shipments s
           JOIN boxes b ON s.box_id = b.id
           JOIN couriers c ON s.courier_id = c.id
           WHERE s.from_branch_id = ?
           ORDER BY s.issued_at DESC""",
        (user.branch_id or 1,),
    )
    shipments = [dict(r) for r in rows]
    ctx = _menu_context(user)
    ctx["username"] = user.username
    ctx["request"] = request
    ctx["shipments"] = shipments
    return request.app.state.templates.TemplateResponse(
        request=request, name="my_shipments.html", context=ctx
    )


@router.get("/requests/new")
async def request_new_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin))):
    """Страница новой заявки: форма поиска карты + подсказка про авто-филиал.
    Селект филиала убран — филиал возврата берётся из учётной записи."""
    ctx = _request_new_context(request, user, results=None, search_name="")
    return _render_page(request, "request_new.html", ctx)


@router.post("/requests/new")
async def request_new_search_or_create(
    request: Request,
    search_name: str | None = Form(None),
    card_id: int | None = Form(None),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """POST /requests/new: поиск карты по ФИО или создание заявки.

    Создание — с авто-филиалом: target_branch_id берётся из user.branch_id,
    форма его больше не шлёт (селект убран). Если к учётной записи филиал
    не привязан — 400 с флеш-ошибкой вместо 500.
    """
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)

    # Phase 1: поиск карты — строки с русской подписью статуса и бейджем.
    if search_name:
        cards = await card_repo.find_by_name(search_name)
        results = [_card_result_row(c) for c in cards]
        ctx = _request_new_context(request, user, results=results, search_name=search_name)
        return _render_page(request, "request_new.html", ctx)

    # Phase 2: создание заявки с авто-филиалом из учётной записи.
    if card_id:
        target_branch_id = user.branch_id
        if target_branch_id is None:
            ctx = _request_new_context(request, user, results=None, search_name="")
            ctx["flash"] = {"type": "error", "text": "К вашей учётной записи не привязан филиал"}
            response = request.app.state.templates.TemplateResponse(
                request=request, name="request_new.html", context=ctx, status_code=400
            )
            # старый flash-cookie (если был) не должен всплыть на странице ошибки
            response.delete_cookie("ma_flash", path="/")
            return response
        branch_name = getattr(user, "branch_name", None) or ""
        if not branch_name:
            row = await db.fetch_one(
                "SELECT name FROM branches WHERE id = ?", (target_branch_id,)
            )
            branch_name = row["name"] if row else ""
        await req_service.create_request(
            card_id=card_id,
            requested_by=user.id,
            target_branch_id=target_branch_id,
            target_department=False,
        )
        return _flash_redirect(
            "/requests/my",
            "success",
            f"Заявка на возврат создана. Карта будет доставлена в «{branch_name}».",
        )

    # Ни поиск, ни создание (пустой POST) — просто страница.
    ctx = _request_new_context(request, user, results=None, search_name="")
    return _render_page(request, "request_new.html", ctx)


@router.get("/requests/my")
async def requests_my_page(request: Request, user: User = Depends(require_role(UserRole.branch, UserRole.admin)), db: Database = Depends(get_db)):
    """Мои заявки: строки с русскими статусами-бейджами и датой
    «ДД.ММ.ГГГГ ЧЧ:ММ»; читает одноразовый flash-cookie (создание/отмена
    заявки) через _render_page — флеш показывает base.html и удаляет cookie."""
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)
    my_requests = await req_service.get_my_requests(user.id)
    my_requests = _prepare_request_rows(my_requests)
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "my_requests": my_requests})
    return _render_page(request, "requests_my.html", ctx)


@router.get("/requests")
async def request_queue_page(request: Request, user: User = Depends(require_role(UserRole.archive, UserRole.admin)), db: Database = Depends(get_db)):
    """Очередь заявок архива (редизайн контура возврата, задача 4).

    Строки обогащаются _prepare_request_rows (русский статус-бейдж, дата
    «ДД.ММ.ГГГГ ЧЧ:ММ»), branch_name приходит из get_queue (JOIN branches —
    филиал-получатель вместо сырого requested_by); в действии строки —
    селект курьеров + «Выполнить». Одноразовый флеш (после
    /requests/fulfill) читается через _render_page."""
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)
    queue = await req_service.get_queue()
    queue = _prepare_request_rows(queue)
    courier_repo = CourierRepositoryImpl(db)
    couriers = await courier_repo.list_all()
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "queue": queue, "couriers": couriers})
    return _render_page(request, "request_queue.html", ctx)


@router.post("/requests/fulfill")
async def fulfill_request(
    request: Request,
    request_id: int = Form(...),
    action: str = Form("single"),
    courier_id: int = Form(0),
    user: User = Depends(require_role(UserRole.archive, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Выполнить заявку из очереди (редизайн контура возврата, задача 4).

    Успех → 303 на /requests с одноразовым флешем «Заявка выполнена»
    (при courier_id>0 — «…и передана курьеру»); ValueError (заявки нет,
    статус не new/in_progress) — тоже 303, но с флешем-error (текст ошибки
    сервиса сохранён) вместо прежнего рендера 400. courier_id>0 валидируется
    ДО выполнения заявки: несуществующий курьер не должен оставить заявку
    выполненной без отправки (по review-gates Задачи 4, Minor-1);
    courier_id=0 — выполнение без отправки."""
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)

    # Валидация курьера до изменения состояния: подделанный/устаревший
    # courier_id (FK shipments.courier_id → couriers.id) иначе дал бы
    # IntegrityError → 500 уже после коммита fulfill_request (заявка
    # выполнена, возвратной отправки нет — несогласованные данные).
    if action == "single" and courier_id > 0:
        courier_repo = CourierRepositoryImpl(db)
        courier = await courier_repo.get_by_id(courier_id)
        if courier is None:
            return _flash_redirect(
                "/requests", "error", f"Курьер с id={courier_id} не найден."
            )

    try:
        await req_service.fulfill_request(request_id, user.id)
    except ValueError as e:
        return _flash_redirect("/requests", "error", str(e))

    # Get the fulfilled request to create return shipment
    req_row = await db.fetch_one("SELECT * FROM requests WHERE id = ?", (request_id,))
    card = await card_repo.get_by_id(req_row["card_id"])

    if action == "single" and courier_id > 0:
        # Возвратная отправка одиночной карты — в филиал-заказчик заявки
        # (to_branch_id = target_branch_id): shipment kind='card', как в
        # smoke-E2E; её принимает /receive-return на стороне филиала.
        # Легаси-ветка (RB-коробка с origin_branch_id=0 + transfer_box)
        # падала с FK-ошибкой: филиала 0 не существует (boxes.origin_branch_id
        # REFERENCES branches(id)) — заменена на штатный ReturnService.
        courier_repo = CourierRepositoryImpl(db)
        return_service = ReturnService(db, card_repo, box_repo, courier_repo)
        await return_service.return_single_card(
            card_id=card.id,
            courier_id=courier_id,
            to_branch_id=req_row["target_branch_id"],
            issued_by=user.id,
        )
        return _flash_redirect(
            "/requests", "success", "Заявка выполнена и передана курьеру."
        )

    return _flash_redirect("/requests", "success", "Заявка выполнена.")


@router.get("/receive-return")
async def receive_return_page(
    request: Request,
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
):
    """Страница приёма возврата (редизайн контура возврата, задача 5).

    Двухшаговый паттерн /receive: карточка сканера (input card_id + кнопка
    «Проверить») с htmx-формой на /receive-return/check (target #scan-result),
    результат проверки приходит фрагментом в #scan-result. Рендер через
    _render_page — одноразовый флеш (ошибка подтверждения) показывается
    вверху страницы и удаляется из cookie.
    """
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username})
    return _render_page(request, "receive_return.html", ctx)


@router.post("/receive-return/check")
async def receive_return_check(
    request: Request,
    card_id: int = Form(...),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Проверка карты для приёма возврата (htmx-фрагмент в #scan-result).

    Состояние НЕ меняется — только превью (return_service.get_receivable_card_info:
    карта есть, статус in_transit, есть активная возвратная отправка).
    Успех — карточка «Карта к приёму» (таблица Карта | Статус | Назначение)
    с ОБЫЧНОЙ формой подтверждения (POST /receive-return/confirm, не htmx):
    успех подтверждения — 303-редирект на /my-shipments, и 303 браузер
    выполняет штатно только для обычной навигации. Ошибки — фрагмент
    с div.flash.flash-error и текстом сервиса; статус 200 (htmx не подменяет
    контент на 4xx/5xx по умолчанию).
    """
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    courier_repo = CourierRepositoryImpl(db)
    return_service = ReturnService(db, card_repo, box_repo, courier_repo)

    ctx = {"request": request, "info": None, "error": None}
    try:
        info = await return_service.get_receivable_card_info(card_id)
    except ValueError as e:
        ctx["error"] = str(e)
        return request.app.state.templates.TemplateResponse(
            request=request, name="receive_return_result.html", context=ctx
        )
    # Обогащение строки для шаблона (как _card_result_row): русская подпись
    # статуса и класс бейджа badge-<status>.
    status = info["status"]
    info["status_label"] = _CARD_STATUS_LABELS_BY_VALUE.get(status, status)
    info["status_badge"] = f"badge-{status}"
    ctx["info"] = info
    return request.app.state.templates.TemplateResponse(
        request=request, name="receive_return_result.html", context=ctx
    )


@router.post("/receive-return/confirm")
async def receive_return_confirm(
    request: Request,
    card_id: int = Form(...),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Подтвердить получение возвращённой карты.

    Бизнес-логика не менялась (return_service.receive_return; обратная
    совместимость: smoke-E2E шлёт card_id и ждёт 303 на /my-shipments).
    Рендер ошибок приведён к флеш-паттерну контура: вместо 400-страницы —
    303 на /receive-return с одноразовым флешем-error (текст сервиса),
    который показывает GET-страница через _render_page.
    """
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    courier_repo = CourierRepositoryImpl(db)
    return_service = ReturnService(db, card_repo, box_repo, courier_repo)

    try:
        await return_service.receive_return(card_id, user.id)
    except ValueError as e:
        return _flash_redirect("/receive-return", "error", str(e))

    return RedirectResponse(url="/my-shipments", status_code=303)


@router.get("/history")
async def history_page(
    request: Request,
    user: User = Depends(require_role(UserRole.archive, UserRole.viewer, UserRole.admin)),
    db: Database = Depends(get_db),
):
    from medarchive.services.movement_service import MovementService
    ms = MovementService(db)
    movements = await ms.get_recent_movements(50)
    ctx = _menu_context(user)
    ctx.update({"request": request, "username": user.username, "movements": movements})
    return request.app.state.templates.TemplateResponse(
        request=request, name="history.html", context=ctx
    )


@router.post("/requests/cancel")
async def cancel_request_route(
    request: Request,
    request_id: int = Form(...),
    user: User = Depends(require_role(UserRole.branch, UserRole.admin)),
    db: Database = Depends(get_db),
):
    """Отмена заявки: только своей (requested_by == user.id) и только в статусе
    new/in_progress (статус проверяет сервис). Результат — 303 на /requests/my
    с одноразовым флешем: success «Заявка отменена.» или error с текстом
    причины (вместо прежнего молчаливого except ValueError: pass).
    Невалидный id/чужая заявка/повторная отмена не роняют 500."""
    owner = await db.fetch_one(
        "SELECT requested_by FROM requests WHERE id = ?", (request_id,)
    )
    if owner is None:
        return _flash_redirect("/requests/my", "error", "Заявка не найдена")
    if owner["requested_by"] != user.id:
        return _flash_redirect("/requests/my", "error", "Нельзя отменить чужую заявку")
    card_repo = CardRepositoryImpl(db)
    box_repo = BoxRepositoryImpl(db)
    req_service = RequestService(db, card_repo, box_repo)
    try:
        await req_service.cancel_request(request_id)
    except ValueError as e:
        return _flash_redirect("/requests/my", "error", str(e))
    return _flash_redirect("/requests/my", "success", "Заявка отменена.")
