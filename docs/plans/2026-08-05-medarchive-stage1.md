# Этап 1 — Ядро: прямой поток: Implementation Plan

> **Для исполнителя:** TDD строго. Сначала падающий тест, потом реализация.

**Goal:** Полный цикл «филиал → курьер → архив → полка»: упаковка коробок → передача курьеру → приёмка → размещение на полку + экран «Мои отправки».

**Архитектура:** Луковые слои: сервисы (use cases) → репозитории (абстракции) → SQLite-адаптеры. Роутеры с HTMX для интерактивности без SPA.

**Design doc:** `docs/plans/2026-08-05-medarchive-design.md`

---

## Pre-Task: Фикс shutdown-хука (1 минута)

**Проблема:** Pytest иногда зависает из-за глобального `_db` из `dependencies.py`, который не закрывается на shutdown.

```python
# В src/medarchive/main.py добавить:
@app.on_event("shutdown")
async def shutdown():
    from medarchive.dependencies import _db
    if _db:
        await _db.close()
```

```bash
git add src/medarchive/main.py
git commit -m "fix: shutdown hook closes DB connection"
```

---

## Task 1: SQLite-реализации репозиториев

**Files:**
- Create: `src/medarchive/adapters/sqlite/repositories.py`
- Create: `tests/adapters/sqlite/test_repositories.py`

**Goal:** Реализовать CardRepository, BoxRepository, UserRepository + CourierRepository для SQLite.

**Ключевая логика:**
- `CardRepositoryImpl.create(full_name)` — INSERT + возврат Card
- `BoxRepositoryImpl.create(box_number, ...)` — INSERT + возврат Box с автоинкрементным номером
- `BoxRepositoryImpl.add_card(box_id, card_id)` — INSERT в box_cards + UPDATE card status=in_box
- `UserRepositoryImpl.get_by_username(name)` — SELECT

**Тесты (7):**
1. `test_create_and_find_card` — создать карту, найти по ID, проверить статус
2. `test_find_cards_by_name` — найти по ФИО (включая дубли)
3. `test_create_box_and_add_cards` — создать коробку, добавить 2 карты, проверить состав
4. `test_add_duplicate_card_to_box_ignored` — повторное добавление игнорируется
5. `test_get_user_by_username` — найти пользователя по логину
6. `test_create_courier` — создать курьера
7. `test_list_couriers` — список всех курьеров

**Верификация:** `pytest tests/adapters/sqlite/test_repositories.py -v` → 7 passed

---

## Task 2: BoxService — упаковка коробок

**Files:**
- Create: `src/medarchive/services/box_service.py`
- Create: `tests/services/test_box_service.py`

**Goal:** Бизнес-логика упаковки: создать коробку → сканировать карты → закрыть. С генерацией номера коробки.

**Ключевая логика:**
- `create_box(branch_id, user_id)` → создаёт коробку с `box_number = f"B-{id:04d}"`, статус `packing`
- `scan_card(box_id, full_name)` → ищет карту по ФИО или создаёт новую → добавляет в коробку → возвращает Card. Дублирования в той же коробке — игнорирует.
- `get_box_contents(box_id)` → список карт в коробке
- `close_box(box_id)` → проверка что коробка не пустая → статус `ready`

**Тесты (5):**
1. `test_create_box_generates_number` — номер формата B-XXXX
2. `test_scan_new_card_adds_to_box` — сканирование создаёт карту и добавляет
3. `test_scan_existing_card_adds_to_box` — существующая карта добавляется
4. `test_scan_duplicate_in_same_box_ignored` — повторное сканирование той же карты
5. `test_cannot_close_empty_box` — пустую коробку нельзя закрыть

**Верификация:** `pytest tests/services/test_box_service.py -v` → 5 passed

---

## Task 3: Экран «Упаковка коробки»

**Files:**
- Create: `src/medarchive/routers/packing.py`
- Create: `src/medarchive/templates/packing.html`
- Create: `tests/routers/test_packing.py`

**Goal:** UI упаковки: кнопка «Новая коробка» → поле сканера (autofocus, Enter) → список карт (htmx) → «Закрыть коробку».

**HTMX-эндпоинты:**
- `GET /packing` — страница упаковки
- `POST /packing/new-box` — создать коробку → HTMX-фрагмент (поле сканера + номер коробки)
- `POST /packing/scan-card` — принять ФИО, добавить карту → HTMX-фрагмент (новая строка таблицы)
- `POST /packing/close-box` — закрыть коробку → редирект на /transfer?box_id=X

**Шаблон:** поле `<input autofocus>` для сканера, таблица карт (htmx:after-swap очищает поле и возвращает фокус), кнопка «Закрыть» заблокирована пока 0 карт.

**Тесты (4):**
1. `test_packing_page_renders` — страница доступна для роли branch
2. `test_create_box_returns_htmx` — POST /new-box возвращает HTMX с номером коробки
3. `test_scan_card_adds_row` — POST /scan-card возвращает HTMX со строкой таблицы
4. `test_close_box_redirects` — после закрытия → 303 на /transfer

**Верификация:** `pytest tests/routers/test_packing.py -v` → 4 passed

---

## Task 4: ShipmentService — передача курьеру

**Files:**
- Create: `src/medarchive/services/shipment_service.py`
- Create: `tests/services/test_shipment_service.py`

**Goal:** Бизнес-логика передачи: создать shipment для коробки, обновить статусы.

**Ключевая логика:**
- `transfer_box(box_id, courier_id, issued_by)` → проверка что коробка в статусе `ready` → создаёт shipment(kind=box) → статус коробки → `in_transit` → статус карт → `in_transit`
- `list_couriers()` → список курьеров
- `get_shipments_for_branch(branch_id)` → отправки филиала

**Тесты (4):**
1. `test_transfer_box_creates_shipment` — shipment создан с правильными полями
2. `test_transfer_updates_box_and_card_statuses` — коробка + карты = in_transit
3. `test_cannot_transfer_box_not_ready` — только ready коробки можно передать
4. `test_get_shipments_for_branch` — фильтр по филиалу

**Верификация:** `pytest tests/services/test_shipment_service.py -v` → 4 passed

---

## Task 5: Экран «Передача курьеру»

**Files:**
- Create: `src/medarchive/routers/transfer.py`
- Create: `src/medarchive/templates/transfer.html`
- Create: `tests/routers/test_transfer.py`

**Goal:** UI передачи: скан/выбор коробки → выбор курьера → подтверждение.

**Эндпоинты:**
- `GET /transfer` — страница передачи (список ready-коробок + список курьеров)
- `POST /transfer/confirm` — box_id + courier_id → создать shipment → редирект на /my-shipments

**Тесты (3):**
1. `test_transfer_page_renders` — страница с выбором коробки и курьера
2. `test_transfer_confirm_success` — успешная передача → редирект
3. `test_transfer_archive_role_denied` — роль «Архив» не может передавать

**Верификация:** `pytest tests/routers/test_transfer.py -v` → 3 passed

---

## Task 6: ReceiveService + PlacementService — приёмка и размещение

**Files:**
- Create: `src/medarchive/services/receive_service.py`
- Create: `tests/services/test_receive_service.py`

**Goal:** Бизнес-логика приёмки (принять коробку в архиве) и размещения (на полку).

**Ключевая логика:**
- `receive_box(box_id, user_id)` → статус shipment → received → статус коробки → received → статус карт → in_archive
- `get_pending_boxes()` → коробки в статусе in_transit (ожидают приёмки)
- `get_received_boxes()` → коробки в статусе received (ожидают размещения)
- `place_box(box_id, shelf_id)` → статус коробки → placed → статус карт → placed → создаёт box_placements
- `get_sections_and_shelves()` → дерево секций/полок для выбора

**Тесты (5):**
1. `test_receive_box_updates_statuses` — коробка + карты = received
2. `test_place_box_creates_placement` — box_placements создана
3. `test_place_box_updates_card_statuses` — карты = placed
4. `test_get_pending_boxes` — только in_transit
5. `test_cannot_place_unreceived_box` — разместить можно только после приёмки

**Верификация:** `pytest tests/services/test_receive_service.py -v` → 5 passed

---

## Task 7: Экраны «Приёмка» и «Размещение»

**Files:**
- Create: `src/medarchive/routers/archive.py`
- Create: `src/medarchive/templates/receive.html`
- Create: `src/medarchive/templates/placement.html`
- Create: `tests/routers/test_archive.py`

**Goal:** Два экрана роли «Архив»: приёмка (скан → состав → подтвердить) и размещение (выбор полки).

**Эндпоинты приёмки:**
- `GET /receive` — список коробок в пути
- `POST /receive/scan` — скан коробки → HTMX с составом
- `POST /receive/confirm` — подтвердить приёмку → редирект на /placement?box_id=X

**Эндпоинты размещения:**
- `GET /placement` — список принятых коробок
- `POST /placement/scan` — скан коробки → HTMX с выбором полки
- `POST /placement/confirm` — box_id + shelf_id → разместить

**Тесты (4):**
1. `test_receive_page_lists_pending_boxes`
2. `test_scan_box_shows_contents` — HTMX с таблицей карт
3. `test_placement_page_shows_sections`
4. `test_branch_cannot_access_receive` — 403 для роли Филиал

**Верификация:** `pytest tests/routers/test_archive.py -v` → 4 passed

---

## Task 8: Экран «Мои отправки» + E2E smoke test

**Files:**
- Create: `src/medarchive/templates/my_shipments.html`
- Update: `src/medarchive/routers/dashboard.py` → добавить /my-shipments
- Create: `tests/test_stage1_smoke.py`

**Goal:** Таблица отправок филиала + полный E2E-тест прямого потока.

**Эндпоинт:**
- `GET /my-shipments` — таблица: номер коробки, дата, курьер, статус

**E2E smoke test:** Создать коробку (филиал) → 3 карты → закрыть → передать курьеру → принять (архив) → разместить на полку → проверить все статусы в БД.

**Тесты (3):**
1. `test_my_shipments_page` — рендеринг таблицы
2. `test_full_forward_flow_e2e` — сквозной сценарий

**Верификация:** Полный suite `pytest tests/ -v`

---

## Сводка

| # | Компонент | Файлы | Тестов |
|---|-----------|-------|--------|
| Pre | Shutdown fix | main.py | — |
| 1 | SQLite-репозитории | adapters/sqlite/repositories.py | 7 |
| 2 | BoxService | services/box_service.py | 5 |
| 3 | Упаковка (UI) | routers/packing.py + template | 4 |
| 4 | ShipmentService | services/shipment_service.py | 4 |
| 5 | Передача курьеру (UI) | routers/transfer.py + template | 3 |
| 6 | ReceiveService + PlacementService | services/receive_service.py | 5 |
| 7 | Приёмка + Размещение (UI) | routers/archive.py + 2 templates | 4 |
| 8 | Мои отправки + E2E | template + smoke test | 3 |

**Итого:** 8 задач, ~35 тестов. Ожидаемый полный suite к концу этапа: 52 + 35 = **~87 тестов**.
