# План: /placement — вариант A (htmx-флоу без перезагрузок)

Дата: 2026-09-01
Дизайн: `docs/plans/2026-09-01-placement-page-design.md` (утверждён)
Базовые тесты: 222 passed (`ec62b08`)

## Задача 1 — Сервис: обогащение get_received_boxes + place_box

**TDD:** сначала тест, потом реализация.

1. RED: в `tests/services/test_receive_service.py`:
   - `get_received_boxes` возвращает `branch_name`, `received_at`, `cards_count` (фикстура:
     филиал, коробка `received` + shipment `received` + 2 карты в box_cards);
   - `place_box` возвращает имя полки;
   - `place_box` с несуществующей полкой → `ValueError("Полка не найдена")`.
2. GREEN: `src/medarchive/services/receive_service.py`:
   - `get_received_boxes` — SQL из дизайн-дока (JOIN branches, LEFT JOIN shipments status='received',
     подзапрос COUNT box_cards);
   - `place_box` — guard `SELECT name FROM archive_shelves WHERE id=?` → если нет — `ValueError`;
     возвращать `shelf_name`.
3. Прогон: `pytest -q tests/services/test_receive_service.py`.
4. Commit: `feat: сервис размещения — обогащённый список принятых коробок + guard полки`.

## Задача 2 — Шаблоны: partial placement_area + placement_panel + стили флешей

1. RED: в `tests/routers/test_archive.py` (или новом `tests/routers/test_placement.py`):
   - `GET /placement` без коробок → «Все коробки размещены» (пустое состояние);
   - `GET /placement` с коробками → «Ожидают размещения», заголовки «Коробка/Откуда/Принята/Карт»,
     кнопка `hx-get="/placement/select?box_id=…"`;
   - фрагмент `select` (после Задачи 3 — здесь только шаблонные части).
2. GREEN:
   - `base.html`: классы `.flash-success`, `.flash-error`;
   - `placement.html`: h1 + include `placement_area.html` (с `selected_box_id`);
   - `placement_area.html`: флеш (переменная `flash` = dict{type, text}), список с htmx-кнопками,
     скрытые `tr#panel-row-N`, пустое состояние; авто-открытие панели для `selected_box_id`;
   - `placement_panel.html`: chip, каскадные селекты, `{{ sections | tojson }}`, кнопки,
     «Отмена» (inline JS), «Разместить» (hx-post на confirm).
3. Прогон: pytest по новым тестам.
4. Commit: `style: /placement — partial'ы списка и панели размещения (каскад «Секция → Полка») + флеши`.

## Задача 3 — Роутер: /placement/select и /placement/confirm → фрагменты

1. RED: в тестах роутера:
   - `GET /placement/select?box_id=N` (коробка received) → 200, chip «B-…», `select#section`, `select#shelf`
     c `disabled`, hidden `input[name=box_id]`, кнопка «Разместить»;
   - `GET /placement/select` для несуществующей коробки → флеш-ошибка «не найдена»;
   - `POST /placement/confirm` (успех) → 200, в теле success-флеш «размещена на полке», в списке
     размещённой коробки нет; в БД статус `placed`;
   - `POST /placement/confirm` повторно → error-флеш (коробка уже размещена), список без изменений;
   - `POST /placement/confirm` без `shelf_id` → error-флеш «Выберите полку»;
   - старый `test_placement_page_shows_sections` — обновить под новую разметку (страница больше
     не рендерит селекты полок напрямую).
2. GREEN: `src/medarchive/routers/archive.py`:
   - `GET /placement/select` (box_id: int | None; проверка статуса; рендер `placement_panel.html`
     или флеш-фрагмента);
   - `POST /placement/confirm` (shelf_id: int | None = Form(None); успех/ошибка → рендер
     `placement_area.html` с `flash`; имя полки из возврата `place_box`);
   - `GET /placement` — контекст для partial'ов (received_boxes, sections, selected_box_id, flash=None).
3. Прогон: `pytest -q tests/routers/test_archive.py` (+ новые placement-тесты).
4. Commit: `feat: /placement — htmx-фрагменты select/confirm (без перезагрузок)`.

## Задача 4 — Полный прогон + финальная проверка

1. `pytest -q` — все тесты зелёные (без регрессий по /receive, /transfer, /packing).
2. Ручная smoke-проверка (curl): GET /placement (список), select-фрагмент, confirm (success/error).
3. Commit при необходимости (например, док-правки).
