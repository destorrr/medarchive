# Этап 3 — Поиск, аудит, дашборд: Implementation Plan

> **Для исполнителя:** TDD строго. Сначала падающий тест, потом реализация.

**Goal:** Поиск карт и коробок, журнал движений, живой дашборд со статистикой.

**Design doc:** `docs/plans/2026-08-05-medarchive-design.md`

**Текущее состояние:** 108 тестов, меню уже ссылается на /search и /history, но эндпоинтов нет. Дашборд — заглушка «Добро пожаловать».

---

## Task 1: SearchService — поиск карт и коробок

**Files:** `src/medarchive/services/search_service.py`, `tests/services/test_search_service.py`

**Goal:** Сервис поиска: карты по ФИО (подстрока) + местонахождение, коробки по номеру + состав.

**Ключевая логика:**
- `search_cards(query: str) → list[dict]` — поиск по `full_name LIKE '%query%'` + текущее местонахождение (статус + коробка/полка)
- `search_boxes(query: str) → list[dict]` — поиск по `box_number LIKE '%query%'` + состав (количество карт), статус, размещение
- `get_card_location(card_id: int) → dict` — детальная информация о местонахождении карты

**Тесты (5):** search_cards finds by substring, search_cards returns location, search_boxes by number, search_boxes returns card count, get_card_location full info

---

## Task 2: Экран «Поиск» (архив + viewer + admin)

**Files:** обновление `src/medarchive/routers/dashboard.py`, `src/medarchive/templates/search.html`, `tests/routers/test_search.py`

**Goal:** Страница поиска: поле ввода → результаты (карты + коробки в двух секциях).

**Эндпоинты:**
- `GET /search` — страница с полем поиска + формой
- `POST /search` — выполняет поиск, рендерит результаты

**Роутер:** требует роль `archive`, `viewer` или `admin`

**Тесты (3):** page renders, search finds card, search finds box

---

## Task 3: MovementService — журнал движений

**Files:** `src/medarchive/services/movement_service.py`, `tests/services/test_movement_service.py`

**Goal:** Запись и чтение card_movements.

**Ключевая логика:**
- `log_movement(card_id, action, from_location, to_location, user_id)` — INSERT в card_movements
- `get_card_history(card_id) → list[dict]` — вся история карты
- `get_recent_movements(limit=50) → list[dict]` — последние движения, с JOIN на cards.full_name

**Тесты (4):** log_movement, get_card_history, get_recent_movements, history empty for new card

---

## Task 4: Интеграция MovementService в существующие сервисы

**Files:** `src/medarchive/services/box_service.py`, `src/medarchive/services/receive_service.py`, `src/medarchive/services/request_service.py`, `src/medarchive/services/return_service.py`

**Goal:** В каждый существующий сервис добавить вызов `MovementService.log_movement()` при изменении статуса/местоположения карты.

**Точки записи:**
- Упаковка карты в коробку → `action='packed', to_location='box {n}'`
- Приёмка коробки → `action='received', to_location='archive'`
- Размещение → `action='placed', to_location='shelf {name}'`
- Выполнение заявки (изъятие) → `action='retrieved_for_return', from_location='box {n}'`
- Приём возврата → `action='returned', to_location='branch {name}'`
- Передача курьеру → `action='dispatched', to_location='courier {name}'`

**Важно:** НЕ ломаем существующие тесты (они не проверяют card_movements). Добавляем вызов безусловно, но без assertion в старых тестах.

**Тесты (3):** movement logged on pack, movement logged on receive, full chain has complete history

---

## Task 5: Экран «Журнал движений» (viewer + admin)

**Files:** обновление `src/medarchive/routers/dashboard.py`, `src/medarchive/templates/history.html`, `tests/routers/test_history.py`

**Goal:** Страница с фильтруемой таблицей последних движений.

**Эндпоинт:**
- `GET /history` — таблица: время, карта, действие, откуда, куда, пользователь

**Роутер:** требует роль `viewer` или `admin`

**Тесты (2):** page renders, shows recent movements

---

## Task 6: Живой дашборд со статистикой

**Files:** обновление `src/medarchive/routers/dashboard.py`, `src/medarchive/templates/dashboard.html`, `tests/routers/test_dashboard.py`

**Goal:** Заменить заглушку на реальную статистику.

**Показывать:**
- Карточки: всего карт, в архиве, в филиалах, в пути
- Коробок: всего, в пути, в архиве
- Открытых заявок
- Последние 5 движений

**Эндпоинт:** обновить `GET /dashboard` — собирать статистику из БД

**Тесты (3):** dashboard shows stats, dashboard shows recent movements, dashboard accessible to all roles

---

## Task 7: E2E smoke — поиск + аудит

**Files:** `tests/test_stage3_smoke.py`

**Scenario:** Упаковать карту → передать → принять → разместить → поиск находит карту → журнал содержит все шаги → дашборд показывает актуальную статистику

**Тесты (1):** full search + audit flow

---

## Сводка

| # | Компонент | Тестов |
|---|-----------|--------|
| 1 | SearchService | 5 |
| 2 | Экран поиска | 3 |
| 3 | MovementService | 4 |
| 4 | Интеграция движений | 3 |
| 5 | Экран журнала | 2 |
| 6 | Живой дашборд | 3 |
| 7 | E2E smoke | 1 |

**Итого:** 7 задач, ~21 тест. Ожидаемый suite: 108 + 21 = ~129 тестов.
