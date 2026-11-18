# UX-06 — read-only аудит legacy viewer, incidents и ACL

Дата статического среза: 2026-11-18  
Ветка: `docs/ux-audit-project`  
Базовый HEAD: `ae37df8a27320b76d47812b9466b16431e888339`  
Тип проверки: чтение исходников, ORM, migration files, templates и тестовых сценариев.  
**Не выполнялись** подключение к БД, SQL/preflight queries, миграции, pytest, CI или изменение данных.

## 1. Итог

Три legacy-контура не являются независимыми «таблицами, которые можно просто удалить»:

1. **viewer** — роль до сих пор разрешена PostgreSQL schema/ORM, admin service и UI. Она также явно допущена к PostgreSQL dashboard, search и history. В частности, history handler не строит scope для viewer, поэтому запрос истории выполняется без role-specific фильтра. Это фактическое расхождение с D-06 и потенциально чрезмерный доступ к операционным событиям.
2. **Incidents** — отдельной PostgreSQL таблицы `incidents` в рассмотренной модели нет. Состояние реализовано как `shipments.status='exception'` и поля `exception_by/exception_at/exception_note`, плюс DomainEvent и UI/очереди на страницах получателей и администратора. В SQLite legacy-процессе также нет отдельной таблицы incidents в базовом SCHEMA: сервис вычисляет проблемные отправления/коробки и выполняет корректирующие действия над shipment/card/box.
3. **ACL** — в проверенной PostgreSQL модели авторизация основана на роли пользователя и endpoint guards (`require_pg_role`) с дополнительными scope-предикатами в запросах. В рассмотренных ORM/migration files отдельная ACL/permissions сущность не обнаружена. При этом статическое чтение не подтверждает отсутствие дополнительных ACL-данных в реальной БД, deployment или других исторических источниках.

Нужен отдельный preflight и контролируемый retirement. Автоматически менять роли пользователей, закрывать exception shipments или удалять историю нельзя.

## 2. Viewer — подтверждённые пути

### 2.1. Схема и администрирование

Подтверждено в исходниках:

- PostgreSQL ORM `User` разрешает `role IN ('branch', 'central_office', 'archive', 'admin', 'viewer')`.
- Начальная PostgreSQL migration 0001 задаёт тот же CHECK.
- `PostgreSQLAdminService.VALID_ROLES` включает `viewer`.
- PostgreSQL admin pages объявляют label «Просмотр» и включают viewer в `ROLE_OPTIONS`; форма создания/редактирования пользователя предлагает эту роль.
- Legacy SQLite schema допускает `branch/archive/viewer/admin`; legacy `UserRole` enum и admin UI также содержат viewer.
- PostgreSQL auth проверяет существование пользователя и `is_active`, но не исключает роль viewer на этапе входа. Доступ далее зависит от guards конкретных endpoints.

### 2.2. Доступ viewer в PostgreSQL surface

Явные allowlists:

- `GET /dashboard`: branch, central_office, archive, admin, viewer.
- `GET /search`, `GET /search/results`, `POST /search`: viewer включён.
- `GET /history`: viewer включён.
- Dashboard context добавляет viewer пункты «Поиск» и «Журнал движений».

**Выявленный риск scope:** в `postgresql_history_page` фильтры строятся только для branch/central_office/archive. Для viewer `event_filters` остаётся пустым; вследствие этого `history_filter` становится `None`, а запрос к `DomainEvent` выполняется без scope-фильтра. Это означает, что текущий viewer history path потенциально получает глобальную операционную историю. Это статический вывод из построения запроса, не результат запроса к БД или HTTP-теста.

Административная role-matrix проверяет, что viewer получает 403 на admin pages/API. Это не подтверждает корректность viewer scope на dashboard/search/history и не является полным тестом D-06.

### 2.3. Миграционное следствие

- До миграции нужно получить количество viewer accounts по активности, филиалу/назначению, наличию ссылок из DomainEvent и других исторических записей.
- Нельзя автоматически превращать viewer в branch, central_office, archive или admin: это выдаст новые полномочия без решения.
- Для каждого действующего viewer должен быть определён владелец/бизнес-потребность. Если подходящей целевой роли нет, безопасный вариант — отключение учётной записи с сохранением исторической идентичности и ссылок.
- До удаления viewer из CHECK/ORM/UI необходимо выбрать технический способ сохранить actor identity для старых событий: например, отдельная legacy identity/archive mapping либо иной согласованный механизм. Простое удаление строк users нарушит FK/атрибуцию истории; простая замена role на другую роль искажает историю полномочий.
- Отдельно решить судьбу неактивных viewer и возможных учётных записей без подтверждённого владельца. Не использовать автоматическую миграцию ролей по догадке.

## 3. Incidents — подтверждённые пути и данные

### 3.1. PostgreSQL

В модели `Shipment` есть:

- статус `exception`;
- `exception_by`, `exception_at`, `exception_note`;
- CHECK, связывающий эти поля со статусами exception/received;
- DomainEvent-типы `shipment.exception_registered` и `shipment.exception_resolved`.

Пользовательские пути:

- `PostgreSQLShipmentWorkflow` регистрирует и разрешает exception.
- `exception_shipments()` строит очередь по `Shipment.status='exception'` для конкретного получателя.
- Страницы приёмки branch, archive и central_office передают такую очередь в шаблоны.
- Admin UI имеет `/admin/incidents` и detail page по shipment; detail показывает маршрут, причину, автора/время, манифест и событие разрешения.
- Shipment register отображает статус exception как «Инцидент», а события имеют подписи про регистрацию/разрешение инцидента.

Следовательно, D-03 затрагивает не только admin menu: нужно вывести exception из обычных receive queues, статусов, workflow endpoints, labels и event presentation, а также согласовать судьбу старых записей.

### 3.2. Legacy SQLite

Базовый SQLite SCHEMA содержит `shipments`, `boxes`, `cards`, `box_cards`, `requests` и `card_movements`, но не отдельную таблицу `incidents`. `IncidentService` используется legacy admin router для поиска потерянных коробок/повреждённых отправлений и корректирующих действий с коробкой/картой. Это отдельная реализация legacy-операций, которую нельзя механически переносить в целевой процесс.

### 3.3. Retention и unresolved exception

D-03 запрещает целевой пользовательский процесс incidents; D-02 требует полной сверки/приёмки без частичной приёмки и не допускает молчаливого изменения местоположения при расхождении.

До любых изменений нужен read-only preflight на копии реальных данных:

- число shipments по status, отдельно `exception`;
- для каждого exception shipment — from/to, handover time, exception actor/time/note, состав manifest, текущие pointers cards/boxes и наличие receipt/resolution event;
- выявить exception shipments с неполным/противоречивым manifest и рассогласованным current_location/current_shipment;
- определить, какие исключения уже разрешены, но остались в legacy status/metadata;
- оценить ссылки на actor users и DomainEvent.

Нельзя автоматически переводить все `exception` в `received`, `cancelled` или иной статус. Для unresolved records требуется контролируемая ручная сверка/операционное решение; критерии и допустимые переходы должны быть зафиксированы до миграции. Исторические exception metadata/events должны сохраняться согласно audit retention, даже когда пользовательский процесс и экран будут выведены.

## 4. ACL и границы доказанного

В проверенных PostgreSQL ORM/migration files видны users.role CHECK и таблицы предметной области; отдельная ACL/permissions таблица в этих файлах не найдена. В runtime application authorization используются:

- `get_pg_current_user` — проверка сессии, существования и активности пользователя;
- `require_pg_role(...)` — allowlist ролей на endpoint;
- дополнительные query predicates для scope объектов/локаций.

Это application-level role/scope enforcement, а не PostgreSQL GRANT/RLS audit. Реальные grants, role memberships, database roles, RLS policies и deployment credentials не проверялись подключением к БД. Отсутствие отдельной ACL модели в просмотренных файлах не доказывает отсутствие ручных ACL в deployment или исторических данных.

Read-only preflight/инвентаризация должна дополнительно проверить:

- все пользовательские таблицы и колонки, связанные с role/permission/scope/ACL;
- PostgreSQL roles, memberships, grants, RLS policies и owners через `pg_catalog` на отдельном безопасном audit principal;
- SQLite schema/data и любые внешние конфигурационные ACL, если они реально используются;
- endpoint inventory: actor role, branch/location scope, объектное ownership, отрицательные случаи;
- viewer allowlists во всех routers, templates, navigation, API schemas, tests и background jobs.

## 5. Рекомендуемая последовательность retirement

1. **Data preflight (read-only):** выгрузить counts и consistency reports для viewer, exception shipments, actor references и ACL. Выполнять на согласованной копии БД; production не менять.
2. **Viewer mapping:** классифицировать каждого активного viewer; владелец продукта/организации утверждает целевую роль или отключение. Сохранить исторический actor identity и FK.
3. **Viewer access containment:** до cutover исключить viewer из новых назначений; отдельно закрыть глобальный history scope. Это отдельное изменение приложения и требует разрешения на реализацию, сейчас не выполняется.
4. **Incident reconciliation:** классифицировать каждый незавершённый exception shipment и подтвердить корректное физическое состояние/полную приёмку. Не вводить новую incident сущность и не создавать новый incident workflow.
5. **Legacy retention:** определить срок/формат хранения exception metadata и resolution DomainEvents; отделить историческое отображение от активных очередей и команд.
6. **Retirement migration:** только после согласования mapping и retention удалить legacy UI/routes/status paths и ограничения/колонки, если доказано, что история перенесена и не потеряна.
7. **Negative verification:** проверка viewer 401/403 на всех target endpoints; проверки отсутствия глобального event feed; проверки запрета legacy incident mutations; регрессионные проверки участника отправления и admin audit.

## 6. Матрица риска

| Область | Текущий факт | Риск | Что закрывает риск |
|---|---|---|---|
| Viewer role | Разрешена в schema, ORM, admin UI и service | Новые viewer accounts продолжают создаваться вопреки D-06 | Mapping/disable plan и запрет новых назначений |
| Viewer history | Viewer допущен к /history без scope filter | Глобальная операционная история доступна viewer | Закрыть viewer route access; тестировать все роли и scopes |
| Viewer identity | User rows могут быть actor references | Удаление пользователя ломает атрибуцию/FK | Сохранить legacy identity и ссылки |
| Exception shipment | Статус и поля живут в shipments | Нельзя удалить «incident table» — её нет; статус встроен в shipment | Preflight и отдельная migration strategy |
| Exception receive queue | Очереди есть у всех получателей | Сохранение legacy process противоречит D-03 | Убрать активные exception queues после reconciliation |
| Legacy SQLite incident service | Корректирует cards/boxes | Риск переноса старых admin corrections в target process | Отдельно инвентаризировать и вывести legacy endpoints |
| ACL | Application role guards; pg grants не проверены | Ошибочно считать code review доказательством DB security | pg_catalog audit и endpoint negative matrix |

## 7. Ограничения и границы

- Выводы основаны на статическом чтении текущей ветки; это не runtime security test.
- Не выполнялись запросы к PostgreSQL/SQLite данным, pg_catalog, migration smoke tests, pytest или CI.
- Не проверялись production credentials, внешние ACL, deployment role memberships и backup/restore.
- Исходники, ORM, API/UI, tests, migrations, workflows и deployment не изменялись.
- Этот документ — технический аудит и план preflight/retirement, а не разрешение на реализацию и не решение о судьбе конкретных пользовательских записей.


## 5. Дополнительная проверка role-scoped history/search read paths

При последующей проверке postgresql_dashboard.py обнаружены два важных расхождения с D-06/D-08/D-16.

### 5.1 Branch сохраняет историю карты после ухода из филиала

В postgresql_history_page для роли branch подзапрос visible_cards фильтруется по Card.origin_branch_id == user.branch_id. Комментарий рядом с кодом прямо описывает сохранение доступа к audit trail зарегистрированной карты после её ухода из помещения филиала.

Это расходится с D-08: после передачи карты из филиала в архив исходный филиал не видит дальнейшую историю карты. Право просмотра собственного shipment manifest по P-08 не расширяет доступ к последующим событиям карты.

Статический вывод: текущая выборка потенциально включает события карты после её передачи из филиала. HTTP-поведение и реальные данные не проверялись. Target predicate должен ограничивать историю филиала событиями до/в пределах его участия, а не постоянным origin_branch_id. Shipment history участника сохраняется отдельно по P-08.

### 5.2 Viewer и admin получают глобальный /history

Тот же route разрешает роли branch, archive, central_office, viewer, admin. event_filters инициализируется пустым списком и заполняется только для трёх операционных ролей. Для viewer и admin history_filter становится None, поэтому запрос к DomainEvent выполняется без role/aggregate scope.

- Для viewer это прямое расхождение с D-06: роль исключена из целевой системы и не должна получать доступ к history/search.
- Для admin это требует согласования D-07 с D-16. D-07 сохраняет глобальный доступ admin к данным и истории, но D-16 требует оставить глобальным только административный/security audit, а операционную историю показывать в карточках объектов. Нельзя автоматически трактовать D-07 как разрешение на отдельный глобальный operational event feed.

### 5.3 Search route

В просмотренном search handler postgresql_search_results также явно разрешает viewer наряду с операционными ролями и admin. Полный анализ _search_context/всех search query scopes требуется завершить; текущий факт достаточен для отдельного negative gate на блокировку viewer.

### 5.4 Требуемые действия

1. В миграционном плане D-06 предусмотреть блокировку viewer до/во время cutover, preflight его account references и negative tests для всех search/history endpoints.
2. Перепроектировать branch history predicate по D-08; сохранить P-08 доступ только к манифестам собственных отправлений.
3. Зафиксировать явную трактовку D-07 × D-16 для admin: глобальный просмотр карточек/их истории через object detail не должен незаметно превращаться в global operational journal.
4. Admin audit должен читать отдельный admin/security audit source после его внедрения; не считать текущий фильтр admin.* в DomainEvent целевой реализацией.
5. Добавить negative tests: branch не видит события карты после handover в архив; viewer получает отказ на search/history; admin не получает глобальный operational feed, если отдельное продуктовое решение не разрешит его явно.

Проверка статическая по исходнику. HTTP tests, PostgreSQL, pytest и CI не запускались. Исходники не менялись.