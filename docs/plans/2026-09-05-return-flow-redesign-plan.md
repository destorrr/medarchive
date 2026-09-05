# План: редизайн контура возврата (этап 2) — единый стиль, авто-филиал, русские статусы

Дизайн-док: `docs/plans/2026-09-05-return-flow-redesign.md` (коммит ff4a7db)
Макет: `canvas/documents/medarchive/return_flow_redesign_mockup_v3.html` (утверждён)

Каждая задача: RED-тест → реализация → GREEN → полный pytest → коммит.

---

## Задача 1. Фундамент: статусы, сайдбар с филиалом, общие стили, флеш-хелпер

**Файлы:** `src/medarchive/domain/statuses.py`, `src/medarchive/dependencies.py`,
`src/medarchive/routers/dashboard.py`, `src/medarchive/routers/packing.py`,
`src/medarchive/templates/base.html`

1. `domain/statuses.py`: добавить `REQUEST_STATUS_LABELS: dict[RequestStatus, str]`:
   new=«Новая», in_progress=«В работе», completed=«Выполнена», cancelled=«Отменена»,
   not_found=«Не найдена».
2. `dependencies.get_current_user`: SELECT `users` c `LEFT JOIN branches` →
   имя филиала кладётся на User как атрибут `branch_name` (None если нет филиала).
3. `dashboard._menu_context(user)`: добавить `user_branch` (имя филиала если есть, иначе
   `ROLE_LABELS[role]`) и `user_login` (= `user.username`). `role_label`/`menu_items` оставить
   (совместимость).
4. `routers/packing.py::_page_ctx`: добавить `user_branch`/`user_login` тем же правилом
   (сейчас ctx собирается вручную, а не через _menu_context).
5. `templates/base.html`:
   - блок `.who` в сайдбаре вместо `.role-badge`:
     `<span class="branch">{{ user_branch }}</span><span class="role">{{ user_login }}</span>`
     (fallback: если user_branch не передан — показать role_label);
   - общие стили: `.who`, таблицы `.data` (+ th/td, colgroup, nowrap-классы `.fio`/`.date`),
     `.act`, `.row-sel`, `.table-footer`, бейджи статусов заявок
     (`.badge-new/-in_progress/-completed/-cancelled/-not_found`), флеш-блок
     `{% if flash %}<div class="flash flash-{{ flash.type }}">…</div>{% endif %}`;
   - даты/филиал в сайдбаре рендерятся из ctx (без хардкода).
6. Флеш-хелпер в `dashboard.py`: `_flash_redirect(url, kind, text)` — ставит cookie
   `ma_flash` (значение `kind|text`, path=/), возвращает `RedirectResponse(303)`;
   `_pop_flash(request) -> dict|None` читает cookie; GET-страницы контура передают в ctx
   `flash` и удаляют cookie в ответе.

**Тесты (RED):**
- `tests/domain/test_statuses.py` (или существующий файл статусов): REQUEST_STATUS_LABELS
  покрывает все значения RequestStatus, значения непустые.
- рендер любой страницы роли branch: в сайдбаре есть имя филиала (например, `B1` из фикстур)
  и логин пользователя.

**Verify:** `pytest -q` — весь набор зелёный; ручная проверка сайдбара на 2-3 страницах ролей.

---

## Задача 2. /requests/new: авто-филиал, бейджи, без селекта

**Файлы:** `src/medarchive/routers/dashboard.py`, `src/medarchive/templates/request_new.html`,
`tests/routers/test_requests.py`

1. GET-страница: форма поиска (input + «Найти»), подсказка «Филиал возврата — <имя> (ваш,
   из учётной записи)» — имя филиала из `user_branch`.
2. POST-поиск (`search_name`): результаты — строки `{full_name, status_label, status_badge}`
   (русские подписи карт через CARD_STATUS_LABELS, класс `.badge-<status>`); селект филиала
   и список `branches` убраны.
3. POST-создание (`card_id`): `target_branch_id = user.branch_id`; если None → флеш-error
   «К вашей учётной записи не привязан филиал», рендер страницы 400. Успех →
   `_flash_redirect("/requests/my", "success", "Заявка на возврат создана…")`.
4. Шаблон: таблица `.data` (ФИО | Статус | действие), кнопка «Запросить» без подписи
   «→ Филиал_2»; пустое состояние поиска.

**Тесты (RED→GREEN):**
- POST create без `target_branch_id` → заявка создана с `target_branch_id == user.branch_id`;
- POST create → 303 на /requests/my; повторный GET /requests/my содержит текст флеша;
- поиск рендерит русскую подпись статуса карты («Размещена») и не содержит `target_branch_id`;
- пользователь без филиала (admin) → 400 + сообщение.

**Verify:** `pytest -q`; обновить устаревшие тесты (старые слали target_branch_id в data).

---

## Задача 3. /requests/my: русские статусы, даты, флеши, отмена

**Файлы:** `src/medarchive/routers/dashboard.py`, `src/medarchive/templates/requests_my.html`,
`tests/routers/test_requests.py`

1. Список заявок обогащается: `status_label` (REQUEST_STATUS_LABELS), `status_badge`,
   `created_at_display` (`ДД.ММ.ГГГГ ЧЧ:ММ` из `created_at`).
2. Шаблон: карточка с таблицей `.data` (Карта | Статус-бейдж | Дата | действие);
   кнопка «Отменить» (`.btn-danger-ghost`) только для new/in_progress; пустое состояние
   со ссылкой на /requests/new.
3. POST /requests/cancel: успех → флеш «Заявка отменена»; ошибки (нельзя отменить) →
   флеш-error с текстом ошибки (сейчас ошибки молча глотаются — вернуть осмысленный ответ).
4. GET /requests/my: читает flash-cookie → ctx, удаляет cookie.

**Тесты (RED→GREEN):** русские подписи статусов на странице; отмена → 303 + флеш;
дата отформатирована (`\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}`); повторный GET без флеша (cookie удалён).

---

## Задача 4. /requests (очередь): колонка «Филиал», селект курьера, флеш

**Файлы:** `src/medarchive/services/request_service.py`,
`src/medarchive/routers/dashboard.py`, `src/medarchive/templates/request_queue.html`,
`tests/routers/test_requests.py`

1. `request_service.get_queue`: JOIN `branches` → `branch_name` (имя филиала-получателя из
   `target_branch_id`).
2. Роутер: обогащение строк (status_label/badge/дата, как задача 3).
3. Шаблон: таблица `.data` (Карта | Филиал | Статус | Дата | действие); действие — `.row-sel`
   селект курьеров + кнопка «Выполнить» (btn-primary); подсказка про смену статусов;
   пустое состояние.
4. POST /requests/fulfill: успех → 303 + флеш «Заявка выполнена»; ValueError → флеш-error
   на странице очереди (400), текст ошибки сохранить.

**Тесты (RED→GREEN):** в очереди есть имя филиала; селект курьера в строке;
выполнение → 303 + флеш; ошибка выполнения → 400 + текст.

---

## Задача 5. /receive-return: двухшаговый приём с превью карты

**Файлы:** `src/medarchive/routers/dashboard.py`,
`src/medarchive/templates/receive_return.html`,
`src/medarchive/templates/receive_return_result.html` (новый фрагмент),
`tests/routers/test_requests.py` (или новый `tests/routers/test_receive_return.py`)

1. GET /receive-return: карточка сканера (input + «Проверить», htmx hx-post
   `/receive-return/check`, target `#scan-result`).
2. POST /receive-return/check (card_id): сервисная проверка — карта существует,
   статус `in_transit`, есть активная отправка (`kind='card'`, `status='issued'`,
   `to_branch_id`); фрагмент: таблица `.data` (Карта | Статус-бейдж | Назначение =
   имя филиала to_branch_id) + кнопка «Подтвердить получение» под таблицей;
   ошибки — фрагмент с флеш-error (текст сервиса).
3. POST /receive-return/confirm — оставить как есть (обратная совместимость с E2E);
   только рендер ошибок во фрагменте/странице единообразно.
4. Стили фрагмента — те же `.data`/`.btn`.

**Тесты (RED→GREEN):** check для карты in_transit с активной card-отправкой → 200, в ответе
ФИО, русский статус «В пути», имя филиала назначения, кнопка «Подтвердить получение»;
check для карты без отправки → флеш-error с текстом; confirm-флоу E2E остаётся зелёным
(`tests/test_stage2_smoke.py`).

---

## Финальная проверка

- `pytest -q` полностью зелёный (все старые + новые тесты).
- `git status` чистый, коммиты по задачам.
- Ручная проверка в браузере (роли filial/arhiv): сайдбар с филиалом, 4 страницы контура
  в едином стиле, флеши, авто-филиал.
- Пуш в origin — только после подтверждения пользователем.
