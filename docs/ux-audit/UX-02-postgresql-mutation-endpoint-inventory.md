# UX-02 — инвентаризация изменяющих PostgreSQL endpoints

Дата: 2026-11-17  
Исходный HEAD: `a3526ed9c9823a58b1436851e2e9db6fa2e8d97e`  
Назначение: базовый перечень изменяющих маршрутов для последующей прямой сверки с тестовыми вызовами.

## 1. Метод и границы

Прочитаны декораторы маршрутов в десяти PostgreSQL router modules. В список включены HTTP POST/PUT/PATCH/DELETE; GET-маршруты оставлены за пределами mutation inventory. Список является статическим снимком точного HEAD, не результатом runtime route introspection.

Маршруты сгруппированы по router module. Один пользовательский сценарий может включать несколько endpoints; один тест может покрывать только часть ветвей обработчика.

## 2. Реестр изменяющих маршрутов

### Branch — `postgresql_branch.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/branch/receive/confirm` | `branch_receive_confirm` | `test_postgresql_full_logistics_cycle.py`; `test_postgresql_incident_pages.py` | Позитивно частично; нет полной role/foreign shipment matrix |
| POST | `/branch/receive/resolve` | `branch_receive_resolve` | `test_postgresql_incident_pages.py` | Позитив/негатив отдельных случаев; не полная матрица |

### Branch packing — `postgresql_packing.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/packing/new-box` | `create_box` | `test_postgresql_packing_integration.py`; full logistics cycle | Позитив; полный role denial matrix не найден |
| POST | `/packing/scan-card` | `scan_card` | `test_postgresql_packing_integration.py` | Позитивный preview и неизвестная карта; чужие существующие карты отдельно не подтверждены |
| POST | `/packing/confirm-card` | `confirm_card_registration` | `test_postgresql_packing_integration.py`; admin integration QR lock test | Позитив и отдельная гонка с деактивацией локации; не все роли/состояния |
| POST | `/packing/remove-card` | `remove_card` | `test_postgresql_packing_integration.py` | Позитив; чужая коробка/карта через HTTP не подтверждена полной матрицей |
| POST | `/packing/write-off-box` | `write_off_box` | `test_postgresql_packing_integration.py` | Позитив списания; полный набор невалидных состояний/ролей не подтверждён |

### Branch transfer — `postgresql_transfer.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/transfer/confirm` | `confirm_transfer` | `test_postgresql_full_logistics_cycle.py` | Позитив в сквозном сценарии; тесты role denial и существующей чужой коробки для этого POST не установлены |

### Shipment read scope — `postgresql_shipments.py`

Изменяющих маршрутов нет. Реестр и карточка — GET; их location scope покрывается `test_postgresql_transfer_integration.py`.

### Central Office — `postgresql_central_office.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/central/receive/confirm` | `central_receive_confirm` | full logistics cycle; archive fulfillment; incident pages | Позитив и отдельные негативные случаи |
| POST | `/central/packing/new-box` | `central_create_box` | full logistics cycle; central custody | Позитив; полный foreign-object matrix не найден |
| POST | `/central/packing/add-card` | `central_add_card` | central custody; shipment workflow service tests | Позитив/отказ внешнего хранения; не все HTTP ownership случаи |
| POST | `/central/packing/remove-card` | `central_remove_card` | shipment workflow service tests | Сервисная опора; прямое HTTP покрытие в просмотренной выборке не установлено |
| POST | `/central/packing/write-off-box` | `central_write_off_box` | shipment workflow/admin service tests | Прямой HTTP позитивный тест не установлен в просмотренной выборке |
| POST | `/central/transfer/confirm` | `central_transfer_confirm` | full logistics cycle; central custody | Позитив; полный отказ чужих ролей/локаций не подтверждён |
| POST | `/central/custody/mark` | `central_mark_external` | `test_postgresql_central_custody.py` | Позитив и негативные роли/состояния |
| POST | `/central/custody/return` | `central_return_external` | `test_postgresql_central_custody.py` | Позитив и негативные роли/повторы |
| POST | `/central/receive/resolve` | `central_receive_resolve` | `test_postgresql_incident_pages.py` | Позитив и неправильный получатель; полная матрица отсутствует |

### Archive — `postgresql_archive.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/receive/confirm` | `receive_confirm` | full logistics cycle; archive integration | Позитив; все маршруты/роли не покрыты |
| POST | `/placement/add-card` | `archive_add_card_to_internal_box` | shipment workflow service tests; full cycle | Сервисная опора; прямой HTTP тест не установлен |
| POST | `/placement/remove-card-from-box` | `archive_remove_card_from_internal_box` | full logistics cycle; shipment workflow service tests | Позитивный путь; полная HTTP negative matrix не найдена |
| POST | `/placement/new-internal-box` | `create_internal_box` | full logistics cycle | Позитив; отрицательные комбинации ограничены |
| POST | `/placement/box` | `place_box` | archive integration; full logistics cycle | Позитив и часть state guards |
| POST | `/placement/card` | `place_card` | archive integration | Позитив, неактивная полка, повторное размещение и запрет движения размещённой карты |
| POST | `/placement/remove-box` | `remove_box_from_shelf` | full logistics cycle; shipment workflow tests | Позитивный путь; полный foreign-object matrix не установлен |
| POST | `/placement/write-off-box` | `archive_write_off_box` | shipment workflow service tests | Прямой HTTP позитивный тест не установлен в просмотренной выборке |
| POST | `/placement/remove-card` | `remove_card_from_shelf` | archive integration | Позитив снятия и повторного размещения карты |
| POST | `/requests/not-found` | `archive_request_not_found` | archive fulfillment; shipment workflow tests | Проверка обязательного пояснения и статуса; все HTTP role/ownership варианты не установлены |
| POST | `/requests/unpack-card` | `unpack_requested_card` | full logistics cycle; shipment workflow tests | Сервисная/сквозная опора; прямые негативные HTTP случаи ограничены |
| POST | `/requests/fulfill` | `fulfill_archive_requests` | archive fulfillment; full logistics cycle; archive integration | Позитив; viewer denial; нет полного foreign request/courier matrix |
| POST | `/requests/create` | `central_create_archive_requests` | central requests; full logistics cycle | Позитив и duplicate filtering; все роли/чужие карты не покрыты полной матрицей |
| POST | `/receive/resolve` | `receive_resolve` | incident pages | Позитив/валидация отдельных случаев; не все роли и статусы |

### Admin JSON API — `postgresql_admin.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/branches` | `create_branch` | admin integration; admin role matrix | Позитив и запрет не-admin |
| PUT | `/branches/{branch_id}` | `update_branch` | admin integration; admin role matrix | Позитив и запрет не-admin |
| POST | `/branches/{branch_id}/deactivate` | `deactivate_branch` | admin integration/service tests | Инварианты деактивации покрыты |
| POST | `/branches/{branch_id}/activate` | `activate_branch` | admin integration | Позитив реактивации; полный role/object matrix не нужен сверх общего admin guard, но отдельно не перечислен |
| POST | `/users` | `create_user` | admin integration; admin role matrix | Позитив и запрет не-admin |
| PUT | `/users/{user_id}` | `update_user` | admin integration; admin role matrix | Позитив, self-demotion/self-disable и last-admin guards; запрет не-admin |

### Admin HTML — `postgresql_admin_pages.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/admin/branches` | `branches_create` | admin integration; admin role matrix | Позитив и запрет не-admin |
| POST | `/admin/branches/{branch_id}/edit` | `branches_edit` | admin integration; admin role matrix | Позитив и запрет не-admin |
| POST | `/admin/branches/{branch_id}/status` | `branches_status` | admin integration; admin role matrix | Позитив/инварианты и запрет не-admin |
| POST | `/admin/users` | `users_create` | admin integration; admin role matrix | Позитив/валидация и запрет не-admin |
| POST | `/admin/users/{user_id}/edit` | `users_edit` | admin integration; admin role matrix | Позитив/self-protection и запрет не-admin |
| POST | `/admin/couriers` | `couriers_create` | admin integration; admin role matrix | Позитив и запрет не-admin |
| POST | `/admin/couriers/{courier_id}/status` | `couriers_status` | admin integration; admin role matrix | Позитив/активные shipment guard и запрет не-admin |
| POST | `/admin/sections` | `shelves_create` | admin integration; admin role matrix | Позитив и запрет не-admin |
| POST | `/admin/sections/{shelf_id}/edit` | `shelves_edit` | admin integration; admin role matrix | Позитив и запрет не-admin |
| POST | `/admin/sections/{shelf_id}/status` | `shelves_status` | admin integration; admin role matrix | Позитив/активные placements guard и запрет не-admin |

### Search — `postgresql_dashboard.py`

| HTTP | Path | Handler | Найденные тестовые опоры | Оценка |
|---|---|---|---|---|
| POST | `/search` | `postgresql_search_submit` | search tests; UI flow tests | Нужна отдельная проверка, действительно ли POST меняет состояние или только обрабатывает форму/redirect; не считать mutation бизнес-операцией до чтения handler |

## 3. Итоговый счётчик

По декораторам перечисленных модулей:
- **48** POST/PUT маршрутов, включая `POST /search` (его семантика требует уточнения);
- **47** маршрутов с явным изменяющим назначением, если `POST /search` является только поисковой отправкой формы;
- **0** найденных PATCH/DELETE в этих десяти PostgreSQL router modules.

Это счётчик именно просмотренных модулей, не всех endpoints приложения: общий auth router и иные вспомогательные маршруты не включены.

## 4. Ключевые пробелы, подтверждённые инвентаризацией

1. Для ряда операций есть только сервисная опора, но не найден прямой HTTP-тест (например, центральное извлечение карты/списание коробки, архивное добавление карты во внутреннюю коробку/списание).
2. Для branch transfer есть позитивный путь, но отдельный POST role-denial matrix не найден.
3. Для branch/central/archive нет общего теста, перебирающего реальные foreign objects на каждом endpoint.
4. Admin покрыт шире остальных контуров, но позитивные операции и отказные роли распределены между разными тестами.
5. POST /search нужно классифицировать по семантике handler, не смешивать с доменными мутациями.
6. Статус shipment `cancelled` не представлен отдельным действием в перечисленных пользовательских PostgreSQL router modules.

## 5. Следующий шаг

Проверить каждый отмеченный «сервисный, но без прямого HTTP-теста» путь в конкретном handler и тестах; отдельно открыть handler `POST /search`, уточнить реальную семантику и зафиксировать окончательный mutation count. Затем подготовить минимальную таблицу недостающих тестов по endpoint, не создавая их до согласования.


## 6. Уточнение семантики POST /search

Handler `postgresql_search_submit` принимает query, требует одну из пяти разрешённых ролей и возвращает только `RedirectResponse` на `/search?query=...` с HTTP 303. Он не вызывает service, не пишет в БД и не меняет доменное состояние.

Следовательно, корректная классификация:
- 48 POST/PUT маршрутов в просмотренных модулях;
- 47 доменных изменяющих маршрутов;
- 1 POST маршрутизирует поисковую форму и не является доменной мутацией.

## 7. Проверка CI-триггеров и фактического статуса

На том же проверенном HEAD прочитан `.github/workflows/postgresql.yml`:
- push запускается для `master` и `docs/target-architecture-spec`;
- pull_request запускается для PR в `master`;
- `docs/ux-audit-project` не входит в push branch filter;
- workflow поднимает PostgreSQL 16, применяет Alembic, проверяет таблицы, выполняет `pytest -q -rs`, затем проверяет encrypted backup/restore.

Проверка GitHub status для HEAD `31012befc6b8a1b8f17a701155955638017c513c` вернула пустой список statuses; PR-triggered workflow runs для этого commit также не возвращены. Это не доказывает отсутствие любых запусков, но в проверенных источниках нет подтверждения, что PostgreSQL workflow выполнялся для данного HEAD. Workflow runs по branch filter не удалось получить через доступный GitHub fetch endpoint.

**Вывод:** PostgreSQL CI конфигурация содержательно существует, но текущая рабочая ветка не покрыта её push trigger. Изменять workflow не стал — это запрещённая область до отдельного согласования.
