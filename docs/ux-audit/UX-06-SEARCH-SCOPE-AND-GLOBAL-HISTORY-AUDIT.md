# UX-06 — search scope и границы глобальной истории

Дата: 2026-11-18  
Ветка: docs/ux-audit-project  
База: HEAD 86c9de10f228a01130b0ea535bf9038ed3a04218  
Тип: статический аудит исходников; не HTTP security test.

## 1. Целевое правило admin (решение владельца продукта)

Admin сохраняет глобальный поиск, глобальный просмотр карточек объектов и полную историю конкретного объекта. Глобальная лента ограничивается административным и security-аудитом. Отдельный глобальный operational feed не проектируется. История движения доступна в карточках объектов.

## 2. Текущая реализация search

В src/medarchive/routers/postgresql_dashboard.py helper _search_context:
- branch/archive/central_office получают активную Location соответствующего типа;
- карты доступны по current_location_id либо по current_shipment_id отправления in_transit, где текущая Location является отправителем или получателем;
- коробки используют аналогичное ограничение;
- при отсутствии Location для операционной роли возвращается пустой результат;
- admin не получает location_id, поэтому выполняет глобальный поиск;
- viewer также не получает location_id, а search routes явно допускают viewer, поэтому viewer фактически получает глобальный search scope;
- поиск карт сопоставляет card_number и full_name, но DTO выдачи содержит full_name, статус и сведения о размещении, не включая card_number; limit — 50 карт и 50 коробок.

Текущий admin search scope соответствует продуктовому решению. Viewer access противоречит D-06. Серверные guards должны закрыть viewer на GET /search, GET /search/results и POST /search.

## 3. History surfaces

- /history сейчас формирует выборку DomainEvent; branch использует origin_branch_id и может видеть события после передачи карты (D-08 gap).
- viewer и admin в этом handler не получают event filter; viewer global history нарушает D-06, admin global operational history нарушает уточнённую границу D-07 × D-16.
- Dashboard recent movements ограничен операционной Location для branch/archive/central_office; admin не должен получать глобальную операционную ленту через этот или другой dashboard feed.
- /admin/audit фильтрует DomainEvent по admin.% и является временным смешанным источником; целевой global audit должен читать отдельный admin/security audit store.
- Timeline конкретного shipment aggregate уже существует в shipment detail route. Требуется закончить inventory остальных object detail timelines и проверить их visibility/payload.

## 4. Целевые ограничения и негативные проверки

1. Admin может глобально искать карты/коробки и открывать карточки объектов; object history доступна в контексте конкретного объекта.
2. Admin не получает global operational event feed; global audit содержит только административные/security события.
3. Branch не видит события карты после handover в архив; право видеть manifest собственного shipment сохраняется по P-08.
4. Viewer запрещён на всех search/history endpoints независимо от скрытия пунктов меню.
5. Branch/archive/central_office search не возвращает карты/коробки вне текущей Location и собственных отправлений in_transit.
6. Не участвующая в shipment роль не получает его manifest или timeline.
7. Поиск и object detail не раскрывают дополнительные PII поля за пределами D-07/D-09.

## 5. Остаточная работа

- проверить object detail routes для card, box, shipment и archive request: visibility, PII, timeline source;
- завершить полный inventory DomainEvent consumers (routers, services, templates, jobs, exports, notifications, tests и внешние интеграции);
- перечислить все producer'ы admin.* и все административные/security действия, которые сейчас не пишут audit event;
- определить схему, permissions, retention и migration strategy отдельного admin/security audit;
- выполнить HTTP negative tests после отдельного разрешения на реализацию/тестирование.

## 6. Ограничения

Статическое чтение исходников на указанном HEAD. HTTP requests, pytest, PostgreSQL, SQL и CI не выполнялись. Production deployment и данные не проверялись. Исходники, тесты, миграции, workflows и deployment не менялись.
