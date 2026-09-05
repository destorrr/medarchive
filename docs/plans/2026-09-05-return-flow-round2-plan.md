# План (раунд 2): доработки контура возврата по замечаниям браузерной проверки

Дизайн-док: `docs/plans/2026-09-05-return-flow-redesign.md` §6 (коммит 7e96964)
Макет: `canvas/documents/medarchive/return_flow_redesign_mockup_v5.html` (утверждён)
Базовые коммиты раунда 1: e0fe687..ab93583 (305 passed)

Каждая задача: RED-тест → реализация → GREEN → полный pytest → коммит. НЕ пушить (по правилу).

---

## Задача 1. Миграция users.full_name + User.full_name + админка /admin/users

**Файлы:** `src/medarchive/adapters/sqlite/migrations.py`, `src/medarchive/domain/models.py`,
`src/medarchive/adapters/sqlite/repositories.py` (UserRepositoryImpl),
`src/medarchive/routers/admin.py`, `src/medarchive/templates/admin/users.html`,
`src/medarchive/dependencies.py` (get_current_user — подтягивать full_name в User),
тесты (fixtures/users).

1. Миграция: `ALTER TABLE users ADD COLUMN full_name TEXT NOT NULL DEFAULT ''`
   (идемпотентно, в стиле существующих миграций — проверь механизм миграций в migrations.py;
   если там список CREATE/ALTER с try/except или версионирование — следуй ему).
2. `User` dataclass: поле `full_name: str = ""`; `from_row`: `row.get("full_name") or ""`
   (row может быть dict без колонки в старых вызовах — безопасно).
3. `UserRepositoryImpl`: `create(..., full_name="")`, `update(..., full_name=None)` — как username.
4. `routers/admin.py` users_create/users_edit: Form-поле `full_name`; шаблон admin/users.html:
   поле «ФИО» в форме создания и в строке редактирования, колонка «ФИО» в таблице.
5. `get_current_user` в dependencies.py — ничего дополнительно не нужно, если SELECT * уже
   вернёт full_name после миграции (from_row его прочитает). Проверь тесты-фикстуры:
   users вставляются без full_name — DEFAULT '' покроет.

**Тесты (RED):** создание пользователя через админку с ФИО → full_name в БД;
редактирование ФИО; User.from_row с dict без full_name не падает; sidebar показывает ФИО
(см. Задачу 2 — там тест сайдбара; здесь минимум: репозиторий/админка).

**Verify:** полный pytest зелёный.

---

## Задача 2. Сайдбар v5: без иконки у логотипа, профиль внизу (ФИО+филиал+роль), счётчик у Архива

**Файлы:** `src/medarchive/templates/base.html`, `src/medarchive/routers/dashboard.py`
(_menu_context), `src/medarchive/routers/packing.py` (_page_ctx), `tests/routers/test_dashboard.py`.

1. Логотип: убрать эмодзи/иконку — текст «МедАрхив» + мелкая подпись «учёт медкарт».
2. Меню: группировка по роли (подпись-секция над пунктами, как в макете: «Филиал»/«Архив»/…).
3. Профиль внизу сайдбара (как макет v5):
   - аватар-кружок с инициалами (из full_name: первые буквы первого и второго слова;
     fallback — первая буква username в верхнем регистре);
   - строка 1: ФИО (full_name, если пусто — username);
   - строка 2: филиал (branch_name) или роль;
   - справа бейдж-пилюля с ролью (Филиал/Архив/Просмотр/Администратор).
   Ничего не обрезать (no ellipsis-nowrap у имени; flex-wrap или перенос строк допустим —
   главное, чтобы ФИО и филиал были видны полностью).
4. `_menu_context`/`_page_ctx`: отдавать `user_full_name`, `user_initials`,
   `user_branch`, `user_role_badge` (роль для пилюли). Блок `.who` сверху удалить.
5. Счётчик активных заявок у роли Архив на пункте «Очередь заявок»:
   - в `get_current_user` (dependencies.py) для роли archive дополнительно посчитать
     `SELECT COUNT(*) FROM requests WHERE status IN ('new','in_progress')` и положить
     на user атрибут `active_requests_count`;
   - `_menu_context`: если роль archive — пункту с url `/requests` добавить
     `badge` (число, скрывать при 0). base.html рендерит бейдж.

**Тесты (RED):** сайдбар branch-пользователя содержит ФИО (из users.full_name), филиал,
пилюлю роли, инициалы; сайдбар archive — счётчик «1» при активной заявке и без бейджа при 0;
эмодзи у логотипа нет.

**Verify:** полный pytest зелёный (обновить TestSidebarWho под новую разметку).

---

## Задача 3. /requests/new: живой поиск карт + guard дублей

**Файлы:** `src/medarchive/routers/dashboard.py`, `src/medarchive/services/request_service.py`,
`src/medarchive/templates/request_new.html` (+ фрагмент `request_new_results.html` — новый),
`tests/routers/test_requests.py`, `tests/test_stage2_smoke.py` (если шлёт search_name).

1. `RequestService.create_request`: перед INSERT проверить активную заявку на карту
   (`SELECT id FROM requests WHERE card_id=? AND status IN ('new','in_progress') LIMIT 1`);
   если есть → `ValueError("На карту уже есть активная заявка")`.
2. Роутер POST /requests/new (создание по card_id): ловить ValueError → 303 + флеш-error
   (как в cancel). Ветку «search_name» из POST убрать.
3. Новый GET /requests/new/search (htmx-фрагмент, query): живой поиск карт по ФИО —
   python-фильтр подстроки с .lower() (кириллица), как SearchService (фаза 1);
   для каждой найденной карты: id, full_name, status_label/badge (CARD_STATUS_LABELS),
   `has_active` (есть ли активная заявка — один запрос `IN (...)`),
   `my_active` (активная заявка текущего пользователя — если да, кнопка «Запросить»
   скрыта и показана ссылка на /requests/my).
   Фрагмент `request_new_results.html`: таблица/список .data (ФИО | Статус | действие):
   - нет активной заявки → кнопка «Запросить» (форма POST /requests/new, hidden card_id);
   - активная заявка (моя) → строка «⏳ заявка уже создана — мои заявки» (без кнопки);
   - активная заявка (чужая) → текст «карта уже запрошена» (guard сервера всё равно сработает).
   Пустое состояние «ничего не найдено».
4. request_new.html: подсказка «Филиал возврата — …» удалена; инпут hx-get=/requests/new/search,
   hx-trigger="input changed delay:350ms, search", hx-target=#results; кнопка «Найти» убрана;
   hint «Результаты обновляются по мере ввода…».
5. Поиск по GET, а не POST (как /search); старые тесты, POST-ившие search_name — обновить.

**Тесты (RED):** живой поиск находит по подстроке (регистронезависимо, кириллица);
карта без заявки → кнопка «Запросить»; карта с моей активной заявкой → нет кнопки,
есть «заявка уже создана»; POST create на карту с чужой активной заявкой → 303 + флеш-error,
новая заявка не создана (count в БД не вырос); подсказка «Филиал возврата» отсутствует в HTML.

---

## Задача 4. /requests/my: колонка «Карта сейчас» + /requests: убрать подсказку

**Файлы:** `src/medarchive/services/request_service.py` (get_my_requests: + c.status card_status),
`src/medarchive/routers/dashboard.py` (_prepare_request_rows: + card_status_label/badge),
`src/medarchive/templates/requests_my.html`, `src/medarchive/templates/request_queue.html`,
`tests/routers/test_requests.py`.

1. get_my_requests: SELECT дополнить `c.status AS card_status`.
2. _prepare_request_rows: для карты добавить `card_status_label` (CARD_STATUS_LABELS по value)
   и `card_status_badge` (`badge-<status>`).
3. requests_my.html: таблица — колонки Карта | Заявка | **Карта сейчас** | Дата | действие;
   в «Карта сейчас» бейдж статуса карты; colgroup пересчитать (широкие колонки — ФИО не режется).
4. request_queue.html: удалить блок `.search-hint` про «Выполнить».
5. Проверить: кнопка «Отменить» только у new/in_progress — осталась.

**Тесты (RED):** в /requests/my у выполненной заявки в «Карта сейчас» бейдж «В пути»
(карта in_transit) / «В филиале» (in_branch) и т.п.; в /requests нет текста «карта изымается».

---

## Задача 5. /receive-return: живой поиск по ФИО

**Файлы:** `src/medarchive/routers/dashboard.py`, `src/medarchive/services/return_service.py`
(get_receivable_card_info — переиспользовать для списка), `src/medarchive/templates/receive_return.html`,
`src/medarchive/templates/receive_return_result.html` (переделать под список/поиск),
`tests/routers/test_receive_return.py`, `tests/test_stage2_smoke.py` (check → search).

1. Убрать POST /receive-return/check (int card_id — источник «тихого 422»).
   Вместо него GET /receive-return/search (htmx, query) — живой поиск карт по ФИО
   (python-фильтр .lower(), кириллица); результат — строки карт: id, full_name,
   status_label/badge, назначение (для in_transit c активной card-отправкой — имя филиала
   to_branch_id; иначе «—»), `receivable` (in_transit + активная отправка kind='card' или
   kind='box' через box_cards — переиспользовать логику get_receivable_card_info/_find_active_return_shipment).
2. receive_return.html: инпут на всю ширину hx-get=/receive-return/search (без кнопки),
   #scan-result-контейнер; hint «Результаты по мере ввода».
3. receive_return_result.html: для каждой receivable-карты — форма POST /receive-return/confirm
   (hidden card_id) + кнопка «Подтвердить получение» (обычная форма, не htmx — 303 работает);
   для остальных — статус без кнопки; пустое состояние.
4. POST /receive-return/confirm — БЕЗ изменений (обратная совместимость, smoke-E2E).
5. ВАЖНО: сервисная проверка «карта в пути к МОЕМУ филиалу» — НЕ вводим (pre-existing
   поведение; наблюдение в бэклог, как в ревью раунда 1).

**Тесты (RED→GREEN):** GET /receive-return не содержит кнопки «Проверить» и формы с card_id
(только живой поиск); search по подстроке находит карту в пути → в ответе «Подтвердить получение»
с form action=/receive-return/confirm и hidden card_id; карта без отправки → без кнопки;
карта не найдена/пустой ввод → пустое состояние; smoke-E2E test_full_return_flow зелёный
(он ходит на /receive-return/confirm напрямую — проверить, что путь не сломан).

---

## Финальная проверка

- Полный pytest зелёный (305 базовых + новые).
- Браузерная проверка пользователем; пуш — после подтверждения.
