# Этап 2 — Возвраты и заявки: Implementation Plan

> **Для исполнителя:** TDD строго. Сначала падающий тест, потом реализация.

**Goal:** Обратный поток: подача заявки (филиал/медотдел) → очередь архива → изъятие карты из коробки → создание возвратной отправки (одиночная карта или коробка возврата) → приём возврата получателем.

**Design doc:** `docs/plans/2026-08-05-medarchive-design.md`

---

## Task 1: RequestService — заявки на выдачу

**Files:** `src/medarchive/services/request_service.py`, `tests/services/test_request_service.py`

**Goal:** CRUD заявок: создать, получить очередь, выполнить (изъять карту из коробки), отменить.

**Ключевая логика:**
- `create_request(card_id, requested_by, target_branch_id, target_department)` → INSERT в requests, статус `new`
- `get_queue()` → все заявки со статусом `new`/`in_progress`, сортировка по дате
- `get_my_requests(user_id)` → заявки конкретного пользователя
- `fulfill_request(request_id, user_id)` → статус → `in_progress` → изъять карту из коробки (`box_cards.removed_at = now`) → статус → `completed` → карта → `pending_return`
- `cancel_request(request_id, user_id)` → только для `new`/`in_progress`

**Тесты (6):** create, get_queue, get_my_requests, fulfill_removes_card, cancel, cannot_fulfill_completed

---

## Task 2: Экран «Подать заявку» (филиал)

**Files:** `src/medarchive/routers/requests.py`, `src/medarchive/templates/request_new.html`, `tests/routers/test_requests.py`

**Goal:** Поиск карты по ФИО → выбор цели (филиал/медотдел) → создать заявку → отслеживать статус.

**Эндпоинты:**
- `GET /requests/new` — форма подачи
- `POST /requests/new` — search + create
- `GET /requests/my` — мои заявки со статусами

**Тесты (3):** page renders, create request, my requests list

---

## Task 3: Экран «Очередь заявок» (архив)

**Files:** `src/medarchive/templates/request_queue.html`, обновление `src/medarchive/routers/requests.py`

**Goal:** Список заявок → «Выполнить»: скан карты → выбор способа (одиночная / коробка) → создать возвратную отправку.

**Эндпоинты:**
- `GET /requests` — очередь (роль archive)
- `POST /requests/fulfill` — изъять карту + создать shipment(kind=card)
- `POST /requests/fulfill-box` — создать коробку возврата + shipment(kind=box)

**Тесты (3):** queue renders, fulfill creates shipment, fulfill-box creates box

---

## Task 4: ReturnService — возвратная отправка

**Files:** `src/medarchive/services/return_service.py`, `tests/services/test_return_service.py`

**Goal:** Создание возвратной отправки: одиночная карта (`shipments.kind=card`) или коробка возврата (`shipments.kind=box`).

**Ключевая логика:**
- `return_single_card(card_id, courier_id, to_branch_id, issued_by)` → shipment(kind=card) → карта → `in_transit`
- `return_as_box(request_id, courier_id, issued_by)` → создать коробку (нумерация `RB-XXXX`) → shipment(kind=box) → карта → `in_transit`
- `receive_return_card(card_id, user_id)` → shipment → received → карта → `in_branch`

**Тесты (4):** return_single, return_as_box, receive_return, cannot_return_card_not_pending

---

## Task 5: Экран «Приём возврата» (филиал)

**Files:** `src/medarchive/templates/receive_return.html`, обновление `src/medarchive/routers/requests.py`

**Goal:** Скан карты → подтверждение получения.

**Эндпоинт:**
- `GET /receive-return` — страница приёма
- `POST /receive-return/confirm` — подтвердить получение

**Тесты (2):** page renders, confirm updates status

---

## Task 6: E2E smoke test — полный обратный поток

**Files:** `tests/test_stage2_smoke.py`

**Scenario:** Создать заявку (филиал) → архив выполняет (одиночная карта) → курьер → получатель подтверждает.

**Тесты (1):** full return flow E2E

---

## Сводка

| # | Компонент | Тестов |
|---|-----------|--------|
| 1 | RequestService | 6 |
| 2 | Подать заявку (UI) | 3 |
| 3 | Очередь заявок (UI) | 3 |
| 4 | ReturnService | 4 |
| 5 | Приём возврата (UI) | 2 |
| 6 | E2E smoke | 1 |

**Итого:** 6 задач, ~19 тестов. Ожидаемый suite: 89 + 19 = ~108 тестов.
