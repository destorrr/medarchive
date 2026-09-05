# План: /search — живой поиск, регистронезависимость, итоговый макет

Дата: 2026-09-05
Дизайн: `docs/plans/2026-09-05-search-page-design.md` (утверждён, макет `search_final_mockup.html`)
Базовые тесты: 234 passed (master, до `docs`-коммитов концепции)

## Задача 1 — Сервис: регистронезависимость + «одна карта = одна строка»

**TDD:** сначала тест, потом реализация.

1. RED: переписать `tests/services/test_search_service.py`:
   - `search_cards("иванов")` строчными находит «Иванов Иван Иванович» (кириллица);
   - карта в 2+ активных коробках (`box_cards` без `removed_at`), обе `placed` с разными
     `box_placements.placed_at` → одна запись: `box_number` = коробка с более свежим `placed_at`,
     `stale` = [номер другой коробки], `place` = «Секция А / Полка 1» актуальной;
   - карта в одной коробке → `stale == []`;
   - карта без активных коробок → `box_number is None`, `stale == []`;
   - карта `in_transit` в коробке `in_transit` → актуальная она же, `place is None`;
   - `search_boxes("b-0010")` строчными находит «B-0010»; размещённая коробка возвращает `place`;
   - `get_card_location` — без изменений (регресс).
2. GREEN: `src/medarchive/services/search_service.py`:
   - двухфазный поиск (п.2 дизайна): выборка `id, full_name` → фильтр `.lower()` → join-запрос по id;
   - схлопывание в Python: актуальная коробка по цепочке статусов (п.1 дизайна), остальные — в `stale`;
   - `search_boxes`: тот же двухфазный приём + LEFT JOIN размещения для `place`.
3. Прогон: `pytest -q tests/services/test_search_service.py`.
4. Commit: `feat: поиск — регистронезависимость (кириллица), одна строка на карту (актуальная коробка + остатки)`.

## Задача 2 — Роутер: GET /search (с query), фрагмент /search/results, legacy POST

1. RED: переписать/дополнить `tests/routers/test_search.py`:
   - `GET /search` → 200, есть поле поиска;
   - `GET /search?query=иванов` → 200, «Иванов Иван Иванович», бейдж «Размещена», место «А / …»;
   - `GET /search/results?query=иванов` → 200, в теле результат, НЕТ полной страницы
     (нет `<html`/sidebar-маркера, есть `id="search-results"`/фрагментные классы);
   - `POST /search` (query=…) → 303, `Location` = `/search?query=…`;
   - запрос без совпадений → «ничего не найдено»;
   - коробка: `GET /search?query=B-0010` → секция «Коробки», нет секции «Карты».
2. GREEN: `src/medarchive/routers/dashboard.py`:
   - `GET /search` — query: str | None = None; рендер `search.html` (query, cards, boxes);
   - `GET /search/results` — рендер `search_fragment.html` (те же переменные);
   - `POST /search` → `RedirectResponse(f"/search?query={quote(query)}", 303)`;
   - helper подсветки (экранированные `<mark>`) и маппинг статусов в подписи/классы;
   - вызовы `SearchService.search_cards/search_boxes`.
3. Прогон: `pytest -q tests/routers/test_search.py`.
4. Commit: `feat: /search — query в GET, живой поиск через htmx-фрагмент /search/results`.

## Задача 3 — Шаблоны: итоговый макет (вариант C + остатки B-1)

1. RED: тесты в `tests/routers/test_search.py` (или `test_search_page_layout.py`):
   - страница содержит классы `.ci-top/.ci-fio/.ci-place/.ci-stale`, секцию «Карты (N)»;
   - карта с `stale` → строка «не выписана из коробок: B-…»;
   - `mark` вокруг совпадения (в `full_name`);
   - скрытие секции «Карты», когда найдены только коробки;
   - пустое состояние «ничего не найдено» при отсутствии совпадений.
2. GREEN:
   - `search.html`: h1, форма GET, поле + hint, `#search-results`, page-level `<style>` (классы
     `.ci*`, `.bi*`, пустые состояния — из макета), inline JS Esc/«✕»;
   - `search_fragment.html`: карточки карт и коробок по макету, секции скрываются если пусто,
     состояние «ничего не найдено»;
   - htmx-атрибуты на поле (delay 350ms, target `#search-results`).
3. Прогон: pytest по новым тестам + `pytest -q tests/routers/test_search.py`.
4. Commit: `style: /search — итоговый макет (вариант C + серые остатки B-1)`.

## Задача 4 — Полный прогон и финальная проверка

1. `pytest -q` — все зелёные, без регрессий по /receive, /placement, /transfer, /requests.
2. Ручная smoke-проверка (curl/httpx): GET /search, GET /search?query=…, GET /search/results,
   POST /search (редирект).
3. Commit при необходимости (док-правки).

## Ревью (после каждой задачи)

- spec-reviewer: код соответствует задачам плана и дизайн-доку;
- quality-reviewer (финальный): стиль, дубли, безопасность (экранирование), тесты.
