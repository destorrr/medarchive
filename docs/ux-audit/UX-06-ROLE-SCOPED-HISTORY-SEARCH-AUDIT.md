# UX-06 — аудит role-scoped history и search read paths

Дата: 2026-11-18  
Ветка: docs/ux-audit-project  
База: HEAD f6debfb20131c32454b4288cd68644c90e06c857  
Тип: статическое чтение PostgreSQL routers; не HTTP security test.

## 1. Branch history — расхождение с D-08

В src/medarchive/routers/postgresql_dashboard.py, handler postgresql_history_page, для branch visible_cards строится по Card.origin_branch_id == user.branch_id. Рядом оставлен комментарий, что branch сохраняет audit trail карт, которые он изначально зарегистрировал, даже после их ухода из помещения.

Это расходится с согласованным D-08: после передачи карты из филиала в архив исходный филиал не видит дальнейшую историю карты. P-08 отдельно сохраняет просмотр карточки собственного отправления и манифеста; это не право на последующие события карты.

Риск: если history route доступен в текущем deployment с этим кодом, филиал может видеть события карты после передачи в архив. Это статический risk finding; фактический HTTP response не проверялся.

## 2. Viewer — глобальный history и разрешённый search

postgresql_history_page разрешает роли branch/archive/central_office/viewer/admin. event_filters создаётся пустым и наполняется только для branch/archive/central_office. Для viewer и admin фильтр остаётся None; history query не получает WHERE scope и может прочитать всю таблицу DomainEvent.

postgresql_search_results также явно допускает viewer в require_pg_role. Это прямое расхождение с D-06, исключающим viewer из целевой системы. Полный scope search implementation через _search_context требует отдельного завершения; но сам допуск viewer уже является подтверждённым статическим фактом.

## 3. Admin — D-07 × D-16: решение принято

Уточнение владельца продукта от 2026-11-18 снимает неоднозначность:
- admin сохраняет глобальный поиск и просмотр карточек объектов, включая идентифицирующие сведения;
- admin сохраняет полную историю конкретного объекта;
- глобальная лента ограничивается административным и security-аудитом;
- глобальную ленту операционных событий движения не проектировать; операционная история показывается в карточках объектов.

Следовательно, текущий глобальный /history для admin не соответствует целевой модели и должен быть устранён при реализации. Это не ограничивает доступ admin к истории конкретной карты, коробки, отправления или заявки через соответствующую карточку.

Текущая /admin/audit читает общий DomainEvent с фильтром admin.% — это не отдельный admin/security audit store целевой архитектуры. При миграции нужно сохранить глобальную административную/security-видимость, но отделить источник и схему аудита от DomainEvent.

## 4. Дополнительные read surfaces

- /shipments/{id} формирует timeline по событиям конкретного shipment aggregate. Это согласуется с объектной историей и P-08, при условии проверки visibility shipment и корректной минимизации payload.
- Dashboard recent movements ограничивает события текущей location scope для branch/archive/central_office; это не тот же случай, что глобальный /history.
- Admin audit page ограничивает текущий общий журнал префиксом admin.%, но целевой отдельный admin/security audit ещё не реализован.

## 5. Требуемые проектные и тестовые действия

1. D-06: блокировать viewer на всех search/history surfaces; провести account/actor reference preflight и добавить negative tests.
2. D-08: заменить branch origin-based history scope на правило прекращения доступа к дальнейшей истории после handover в архив. Не расширять его за пределы P-08 shipment manifest.
3. D-07 × D-16: решение принято 2026-11-18 — admin global object search/details and per-object history remain; global feed is admin/security audit only. Remove admin from global operational /history in target design.
4. Разделить источник /admin/audit от DomainEvent после внедрения admin/security audit.
5. Завершить полный search read-scope inventory и проверить, что query results не раскрывают чужие карты/PII.
6. Negative tests: branch cannot see post-handover card events; viewer denied on search/history; admin only sees global admin/security audit and permitted object details.

## 6. Ограничения

Проверены исходники на ветке, но не выполнялись HTTP requests, pytest, PostgreSQL, SQL или CI. Production deployment и данные не проверялись. Исходники, тесты, миграции, workflows и deployment не менялись.

## 7. Search read path — статическое уточнение 2026-11-18

Проверен полный helper _search_context в src/medarchive/routers/postgresql_dashboard.py (HEAD 86c9de1):

- Для branch, archive и central_office определяется активная Location соответствующего типа. Если location не найдена, возвращается пустой результат.
- Карты фильтруются по current_location_id этой Location либо по current_shipment_id отправления со статусом in_transit, где Location является отправителем или получателем.
- Коробки фильтруются аналогично: current_location_id либо активное отправление in_transit с участием Location.
- Поиск карт сопоставляет card_number и full_name, но возвращаемый search DTO содержит full_name, статус и сведения о размещении; номер карты в DTO не включён. Ограничение выборки — 50 совпадений для каждого типа.
- Admin не получает location_id и потому выполняет глобальный поиск карт и коробок. Это соответствует уточнённому D-07, если доступ остаётся только у admin и все выдаваемые поля/переходы проверяются на сервере.
- Viewer также не получает location_id и технически проходит в search routes, поэтому фактически получает тот же глобальный search scope. Это прямой D-06 gap; блокировка viewer должна быть на всех трёх endpoints /search, /search/results и POST /search, а не только в меню.
- Location scope поиска ограничен текущим местонахождением и отправлениями in_transit; он не выдаёт историю объекта. Доступ к карточке и её timeline должен отдельно проверяться в object detail route.

Статический вывод не подтверждает фактические HTTP responses или эксплуатационную доступность маршрутов. Проверка шаблонов, object detail visibility и всех query endpoints остаётся частью дальнейшего аудита.

## 8. D-06 decision update — 2026-11-21

The owner confirmed that MedArchive has not entered operation and contains no user or operational data. No viewer-account migration, actor-reference preservation, or legacy ACL compatibility work is required. The `viewer` role is removed entirely from the target model and future implementation across schema/ORM, API, admin UI, guards, templates/navigation, and tests. The target roles are `admin`, `branch`, `central_office`, and `archive`; no viewer permissions are automatically reassigned. Legacy viewer search/history/dashboard paths identified in this audit are prototype remnants to remove during implementation. This supersedes the earlier recommendation to perform account preflight/mapping. Implementation remains separately gated.
