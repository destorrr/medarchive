# Этап 4 — Справочники, админка, бэкап: Implementation Plan

> **Для исполнителя:** TDD строго. Сначала падающий тест, потом реализация.

**Goal:** Административная панель: CRUD филиалов, пользователей, курьеров; управление секциями/полками + QR-печать; инциденты; ручной бэкап/восстановление.

**Design doc:** `docs/plans/2026-08-05-medarchive-design.md`

**Текущее состояние:** 128 тестов, этапы 0–3 завершены. Админских роутов нет, меню admin уже ссылается на `/admin` (заглушка).

---

## Что уже есть

### Репозитории (частичные)
- `UserRepositoryImpl` — `get_by_id`, `get_by_username`, `create`. **Нет:** `list_all`, `update`, `delete`.
- `CourierRepositoryImpl` — `create`, `list_all`, `get_by_id`. **Нет:** `update`, `delete`.

### Отсутствует полностью
- `BranchRepositoryImpl` — нет совсем
- Репозитории для `archive_sections` / `archive_shelves` — нет
- Инциденты — нет ни сервиса, ни экранов
- Бэкап — нет ни сервиса, ни экранов
- Админский роутер — нет, только ссылка `/admin` в `MENU_BY_ROLE`

### Таблицы уже в БД
- `branches`, `users`, `couriers`, `archive_sections`, `archive_shelves` — создаются в миграции, но CRUD-операций нет

---

## Task 1: BranchRepository + UserRepository/CourierRepository расширения

**Files:** `src/medarchive/adapters/sqlite/repositories.py`, `tests/adapters/test_repositories.py`

**Goal:** Добить репозитории до полного CRUD для всех трёх сущностей.

**BranchRepositoryImpl (новый):**
- `list_all() → list[Branch]`
- `get_by_id(branch_id) → Branch | None`
- `create(name, code) → Branch`
- `update(branch_id, name, code) → None`
- `delete(branch_id) → None` — только если нет связанных boxes/users

**UserRepositoryImpl (дополнения):**
- `list_all() → list[User]`
- `update(user_id, username?, password_hash?, role?, branch_id?) → None`
- `delete(user_id) → None`

**CourierRepositoryImpl (дополнения):**
- `update(courier_id, name, phone?) → None`
- `delete(courier_id) → None`

**Тесты (10):** branch CRUD (5), user list/update/delete (3), courier update/delete (2)

---

## Task 2: Admin-роутер (каркас) + Branch-экраны

**Files:** `src/medarchive/routers/admin.py`, `src/medarchive/templates/admin/branches.html`, `tests/routers/test_admin_branches.py`

**Goal:** Админ-роутер с ролевой защитой (`admin` only), страница управления филиалами.

**Эндпоинты:**
- `GET /admin` — индексная страница админки (ссылки на все разделы)
- `GET /admin/branches` — таблица филиалов + форма создания
- `POST /admin/branches` — создать филиал
- `POST /admin/branches/{id}/edit` — редактировать
- `POST /admin/branches/{id}/delete` — удалить

**Роутер:** `require_role(UserRole.admin)`

**Тесты (5):** page renders, create, edit, delete, 403 for non-admin

---

## Task 3: User CRUD экраны

**Files:** обновление `src/medarchive/routers/admin.py`, `src/medarchive/templates/admin/users.html`, `tests/routers/test_admin_users.py`

**Goal:** Страница управления пользователями (список, создание, редактирование роли/пароля/филиала, удаление).

**Эндпоинты:**
- `GET /admin/users` — таблица пользователей + форма создания
- `POST /admin/users` — создать
- `POST /admin/users/{id}/edit` — редактировать
- `POST /admin/users/{id}/delete` — удалить (нельзя удалить себя)

**Тесты (5):** page renders, create, edit, delete, cannot delete self

---

## Task 4: Courier CRUD экраны

**Files:** обновление `src/medarchive/routers/admin.py`, `src/medarchive/templates/admin/couriers.html`, `tests/routers/test_admin_couriers.py`

**Goal:** Страница управления курьерами.

**Эндпоинты:**
- `GET /admin/couriers` — таблица + форма
- `POST /admin/couriers` — создать
- `POST /admin/couriers/{id}/edit` — редактировать
- `POST /admin/couriers/{id}/delete` — удалить

**Тесты (3):** page renders, create, edit

---

## Task 5: Archive Sections + Shelves CRUD + QR

**Files:** `src/medarchive/adapters/sqlite/repositories.py` (дополнение), `src/medarchive/services/archive_service.py`, обновление `src/medarchive/routers/admin.py`, `src/medarchive/templates/admin/sections.html`, `src/medarchive/templates/admin/shelves.html`, `tests/services/test_archive_service.py`, `tests/routers/test_admin_sections.py`

**Goal:** Управление секциями и полками архива. Генерация QR-кода для каждой полки, возможность печати сетки QR-наклеек на A4.

**Репозитории (новые):**
- `SectionRepositoryImpl`: `list_all`, `get_by_id`, `create`, `update`, `delete`
- `ShelfRepositoryImpl`: `list_all`, `get_by_id`, `get_by_section`, `create`, `update`, `delete`

**ArchiveService:**
- `create_section(name)`, `update_section(id, name)`, `delete_section(id)`, `list_sections()`
- `create_shelf(section_id, name, qr_code?)`, `update_shelf(id, ...)`, etc.
- `generate_shelf_qr(shelf_id) → str` — генерирует/обновляет qr_code для полки (формат `S{section_id}-P{shelf_id}`)
- `get_shelf_qr_png(shelf_id) → bytes` — PNG с QR-кодом для одной полки

**Эндпоинты:**
- `GET /admin/sections` — список секций с полками, форма создания секции/полки
- `POST /admin/sections` — создать секцию
- `POST /admin/sections/{id}/edit` — переименовать
- `POST /admin/sections/{id}/delete` — удалить (только если нет полок)
- `POST /admin/shelves` — создать полку (в секции)
- `POST /admin/shelves/{id}/edit` — переименовать
- `POST /admin/shelves/{id}/delete` — удалить (только если нет размещённых коробок)
- `GET /admin/shelves/{id}/qr` — PNG с QR одной полки
- `GET /admin/shelves/qr-sheet` — PDF/A4 с сеткой QR всех полок (для печати наклеек)

**QR-генерация:**
- Библиотека: `qrcode[pil]` (уже используется?), добавить в `pyproject.toml`
- Формат QR: `S{section_id}-P{shelf_id}`
- PDF: `reportlab` или простой HTML→PDF

**Тесты (8):** section CRUD (4), shelf CRUD (4)

---

## Task 6: Инциденты (ручной разбор)

**Files:** `src/medarchive/services/incident_service.py`, обновление `src/medarchive/routers/admin.py`, `src/medarchive/templates/admin/incidents.html`, `tests/services/test_incident_service.py`, `tests/routers/test_admin_incidents.py`

**Goal:** Экран для ручного разбора проблем: потерянные коробки, расхождения приёмки, карты с неизвестным статусом.

**IncidentService:**
- `get_lost_boxes() → list[dict]` — коробки со статусом `lost`
- `get_damaged_boxes() → list[dict]` — shipments со статусом `lost/damaged`
- `get_discrepancies() → list[dict]` — расхождения (пока заглушка, реальный механизм на будущее)
- `resolve_lost_box(box_id, action) → None` — варианты: `mark_damaged` (карты = lost), `restore_to_branch` (карты обратно в филиал), `ignore`
- `resolve_card_status(card_id, new_status) → None`

**Эндпоинты:**
- `GET /admin/incidents` — список открытых инцидентов (lost boxes, discrepancies)
- `POST /admin/incidents/resolve` — разрешить инцидент

**Тесты (5):** get_lost_boxes, get_discrepancies, resolve_lost_box, resolve_card_status, page renders

---

## Task 7: Бэкап и восстановление

**Files:** `src/medarchive/services/backup_service.py`, обновление `src/medarchive/routers/admin.py`, `src/medarchive/templates/admin/backup.html`, `tests/services/test_backup_service.py`

**Goal:** Ручное создание бэкапа БД и восстановление из загруженного файла.

**BackupService:**
- `create_backup() → (bytes, str)` — копирует .db файл, возвращает (binary, filename)
- `get_backup_info() → dict` — размер БД, дата последнего бэкапа
- `restore_from_upload(file_bytes) → None` — заменяет текущую БД загруженным файлом (с подтверждением)

**Эндпоинты:**
- `GET /admin/backup` — страница: инфо о БД, кнопка «Создать бэкап», форма загрузки для восстановления
- `POST /admin/backup/create` — скачать `.db` файл
- `POST /admin/backup/restore` — загрузить `.db` для восстановления (с подтверждающим чекбоксом)

**Безопасность:**
- Восстановление только с явным подтверждением (чекбокс «Я понимаю, что текущие данные будут потеряны»)
- Перед восстановлением автоматически создаётся бэкап текущего состояния

**Тесты (4):** create_backup, get_backup_info, restore flow, page renders

---

## Task 8: E2E admin smoke test

**Files:** `tests/test_stage4_smoke.py`

**Scenario:** Логин как admin → создать филиал → создать пользователя → создать курьера → создать секцию + полку → сгенерировать QR → создать бэкап → проверить инциденты (пусто)

**Тесты (1):** full admin workflow

---

## Сводка

| # | Компонент | Тестов |
|---|-----------|--------|
| 1 | Branch/User/Courier репозитории (расширение) | 10 |
| 2 | Admin-роутер + Branch-экраны | 5 |
| 3 | User CRUD экраны | 5 |
| 4 | Courier CRUD экраны | 3 |
| 5 | Sections + Shelves CRUD + QR | 8 |
| 6 | Инциденты | 5 |
| 7 | Бэкап и восстановление | 4 |
| 8 | E2E admin smoke | 1 |

**Итого:** 8 задач, ~41 тест. Ожидаемый suite: 128 + 41 = ~169 тестов.

---

## Стратегия выполнения (superpowers subagents)

С учётом уроков (≤3 задачи на сабагента):

| Subagent | Задачи | Тестов |
|----------|--------|--------|
| SA-1 | Task 1: репозитории | 10 |
| SA-2 | Task 2 + 3: Branch + User экраны | 10 |
| SA-3 | Task 4 + 5: Courier + Sections/Shelves | 11 |
| SA-4 | Task 5 (QR) + 6 + 7: QR, Инциденты, Бэкап | 9 |
| SA-5 | Task 8: E2E smoke | 1 |

Всего 5 сабагентов, ~41 тест, цель: ≥169 passed.
