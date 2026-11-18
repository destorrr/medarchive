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

## 3. Admin — D-07 × D-16

Admin также попадает в глобальный /history без event filter. D-07 сохраняет глобальный доступ admin к поиску, истории и данным, включая идентифицирующие сведения. D-16 одновременно устанавливает, что глобальным остаётся только административный/security audit, а операционная история движения показывается в карточках объектов.

Это требует явного разрешения семантики: глобальный доступ admin к карточкам и истории конкретных объектов может сохраняться, но не должен автоматически означать отдельный глобальный operational journal. Если владелец продукта подразумевал глобальный operational feed, это будет отдельным изменением D-16, а не техническим допущением.

Текущая /admin/audit читает общий DomainEvent с фильтром admin.% — это не отдельный admin/security audit store, предусмотренный целевой архитектурой.

## 4. Дополнительные read surfaces

- /shipments/{id} формирует timeline по событиям конкретного shipment aggregate. Это согласуется с объектной историей и P-08, при условии проверки visibility shipment и корректной минимизации payload.
- Dashboard recent movements ограничивает события текущей location scope для branch/archive/central_office; это не тот же случай, что глобальный /history.
- Admin audit page ограничивает текущий общий журнал префиксом admin.%, но целевой отдельный admin/security audit ещё не реализован.

## 5. Требуемые проектные и тестовые действия

1. D-06: блокировать viewer на всех search/history surfaces; провести account/actor reference preflight и добавить negative tests.
2. D-08: заменить branch origin-based history scope на правило прекращения доступа к дальнейшей истории после handover в архив. Не расширять его за пределы P-08 shipment manifest.
3. D-07 × D-16: уточнить границу admin global object access и global operational journal; default target — object history + separate global admin/security audit.
4. Разделить источник /admin/audit от DomainEvent после внедрения admin/security audit.
5. Завершить полный search read-scope inventory и проверить, что query results не раскрывают чужие карты/PII.
6. Negative tests: branch cannot see post-handover card events; viewer denied on search/history; admin only sees global admin/security audit and permitted object details.

## 6. Ограничения

Проверены исходники на ветке, но не выполнялись HTTP requests, pytest, PostgreSQL, SQL или CI. Production deployment и данные не проверялись. Исходники, тесты, миграции, workflows и deployment не менялись.