# UX-06 — итоговая сверка готовности и реестр остаточных блокеров

Дата: 2026-11-18  
Ветка: `docs/ux-audit-project`  
База: HEAD `3f6b5a241a7b9c515f6fb0eed4c9c5d60ecb62cb`  
Тип: read-only cross-document status reconciliation; не реализация и не production readiness sign-off.

## 1. Итог

Основные целевые бизнес-процессы и архитектурные решения D-01…D-18, P-01…P-09 и DB-SEC-01…04 уже зафиксированы. Текущий readiness plan, однако, содержал устаревшие формулировки и смешивал решения владельца продукта, технические контракты, миграционные планы и доказательства, которые можно получить только на непроизводственной копии БД и в deployment environment.

Сверка обновляет раздел 10 readiness plan: D-10/P-03 и D-17 перенесены в закрытые read-only findings; append-only trigger DomainEvent описан корректно. Остаточные блокеры перечислены ниже.

## 2. Статус продуктовых решений

| Вопрос | Статус | Что требуется |
|---|---|---|
| P-04/D-13 — списание медицинской карты | Продуктово закрыто | Утверждены роли, отмена, один исполнитель, обязательный код и закрытый каталог `lost` / `damaged` / `other`; технические команды и guards остаются до реализации |
| P-07 — admin correction | Продуктово закрыто | Утверждены категории и запреты; точные clerical card fields и typed command contracts остаются технической детализацией |
| D-10/P-03 — Box ↔ BoxCycle | Read-only модель подтверждена | Сохранить семантику в target schema |
| D-17 — QR карты | Read-only parser contract подтверждён | Не менять payload; обеспечить совместимость |
| D-06 — viewer | Продуктовое решение принято, миграционный план открыт | Инвентаризация аккаунтов/ссылок/ACL и безопасная миграция |
| D-03 — incidents | Продуктовое решение принято, retirement plan открыт | Сверка legacy exception shipments, UI и истории без автозакрытия |

## 3. Технические блокеры

### 3.1 Контракты команд и событий
- Завершить canonicalization/hash contract по типам команд.
- Определить replayable deterministic negative outcomes и result/error mapping.
- Реализовать утверждённый lifecycle command ledger: UUID v4, server-side schema-specific SHA-256 canonicalization, deterministic replay и tombstone для необратимых команд.
- Завершить обычные command TTL после определения максимальных retry horizons; согласовать точный result envelope и API error mapping (не менять утверждённую replay semantics).
- Утвердить каталог event_type/event_version/payload schemas и compatibility policy.
- Завершить inventory всех DomainEvent consumers; текущие проверенные read surfaces не доказывают полноту списка.
- Спроектировать отдельный admin/security audit, его permissions и корреляцию с DomainEvent/command ledger.

### 3.2 Database security и principals
- Выбрать schema namespace.
- Завершить actor/scope enforcement и решить вопрос RLS; не использовать runtime-controlled GUC как proof.
- Уточнить login throttle DB boundary.
- Утвердить безопасный first-admin/system-locations bootstrap.
- Уточнить backup/restore/monitoring/maintenance principals под реальную deployment topology.
- Завершить PII read grants/views и application scopes.
- Проверить effective grants, ownership, default privileges, trigger protection и возможность обхода на непроизводственной PostgreSQL копии.

### 3.3 Миграция и rollout
- Утвердить поддерживаемые версии приложения и dual-path window.
- CardPlacement retention закрыт решением владельца продукта: исторические записи сохраняются read-only без физического удаления и TTL/purge; активные legacy placements требуют ручной сверки.
- Получить read-only preflight report на production-like копии: карты/коробки/cycles/memberships/placements, активные заявки, shipments, handovers, receipts, viewer references, legacy exception records.
- Подтвердить backup/restore rehearsal, migration rehearsal и rollback/forward-fix plan.
- Не исправлять автоматически неоднозначные карты или логистические связи.

## 4. Фактические уточнения по событиям

- Миграция `0002_workflow_guards.py` создаёт `medarchive_domain_events_append_only()` и `trg_domain_events_append_only` для UPDATE/DELETE. Append-only защита от изменения/удаления представлена в миграционном DDL.
- Trigger не запрещает INSERT. Runtime INSERT permissions, ownership, trigger bypass и effective ACL не подтверждены без подключения к целевой БД.
- Текущая ORM не показывает event_version, command_id, correlation_id и command ledger.
- DomainEvent и admin/security audit имеют разные authoritative purposes. Для административного изменения бизнес-агрегата допустимы две связанные атомарные записи; отдельный admin audit не становится вторым операционным event stream.

## 5. Gate status

| Gate/контур | Статус по имеющимся evidence |
|---|---|
| Продуктовые правила D/P/DB-SEC | В основном согласованы; P-04 и P-07 открыты |
| Target storage/request/shipment model | Проектно описана; требует preflight и конкретного DDL design |
| DB command contracts | Значительная часть описана; idempotency/result/error details открыты |
| Grants/principals | Проектная матрица есть; effective ACL и deployment topology не подтверждены |
| Event/audit | Authoritative ownership clarified; event catalog, schema, consumers, permissions, retention and outage policy open |
| Data preflight/backfill | Не выполнен; production-like DB snapshot не проверялся |
| API/UI implementation | Не начата и не разрешена |
| Automated verification | В рамках этого этапа не запускалась |
| Production cutover | Не готов и не разрешён |

## 6. Рекомендуемый порядок оставшейся проектной работы

1. Получить решения владельца продукта по P-04 и P-07.
2. Завершить технические спецификации command ledger/result/error, event catalog/consumers и admin/security audit.
3. Завершить actor/read scope, RLS decision, schema namespace, login throttle, bootstrap и infrastructure principal design.
4. Провести полный static inventory consumers/read paths и deployment paths.
5. Получить согласованную непроизводственную production-like копию и выполнить read-only data preflight без исправления данных.
6. Свести итоговый DDL/rollout design и пройти Gate 0–2 readiness review.
7. Только после отдельного разрешения планировать реализацию. UX-07 не начинать до формального завершения UX-06.

## 7. Граница аудита

Изменяются только проектные документы. Код приложения, ORM, API/UI, tests, migrations, workflows, deployment и данные не менялись. PostgreSQL/pg_catalog, SQL, Alembic, pytest и CI не запускались. Фактические ACL, production data state и operational deployment не проверялись.

## 8. Дополнительный security/role-scope finding

Последующая статическая проверка postgresql_dashboard.py выявила, что /history для branch строит visible_cards по Card.origin_branch_id, сохраняя историю после ухода карты из филиала. Это расходится с D-08; P-08 даёт только просмотр собственного shipment manifest, а не дальнейшей истории карты.

Тот же route разрешает viewer и admin, но для этих ролей event_filters остаётся пустым, что приводит к выборке DomainEvent без role/aggregate scope. Viewer route access — расхождение с D-06. Для admin требуется явная трактовка D-07 × D-16: глобальный доступ к объектам/истории не следует автоматически приравнивать к глобальному operational event feed.

Search handler также явно разрешает viewer; полный search-scope review ещё требуется. Эти находки повышают приоритет D-06 containment и должны войти в preflight/negative-test gates. Проверка статическая; HTTP tests не запускались.

## 6. P-04/P-07 static workflow inventory (2026-11-18)

Добавлен `UX-06-P04-P07-WORKFLOW-GAP-AUDIT.md`. В inspected PostgreSQL source найден `write_off_box`, но не найдено отдельного medical-card write-off command/route; текущие Card statuses не определяют процесс D-13/P-04. Admin service/pages содержат справочный CRUD, но explicit corrective commands по business aggregates не найдены; `correction_of_event_id` в DomainEvent model не доказывает реализованную коррекцию. P-04 и P-07 требуют продуктового allowlist/evidence решения перед соответствующей реализацией. Source-only inspection; runtime/tests не запускались.


## 9. Актуализация статусов после решений P-04/P-07 (2026-11-18)

Разделы 2 и 6 выше содержат исторический snapshot на дату исходной сверки. Их прежние статусы P-04/P-07 «открыто» superseded последующими решениями владельца продукта и текущими документами `PRODUCT-DECISIONS-D-01-D-18.md`, `UX-06-P04-P07-WORKFLOW-GAP-AUDIT.md` и `UX-06-IMPLEMENTATION-READINESS-PLAN.md`:
- P-04/D-13 продуктово закрыто: согласованы workflow, границы отмены, исполнитель и закрытый каталог оснований.
- P-07 продуктово закрыто по категориям/запретам; точный field-level allowlist карточки ещё требует решения, предложен кандидат `Card.phone`.
- Command idempotency lifecycle/hash/replay/tombstone утверждён как технический стандарт; остаются ordinary-command TTL, итоговый result envelope/API mapping и эксплуатационные горизонты retry.

Это обновление статуса не означает выполнения миграций, DDL, тестов или implementation readiness sign-off.


## 10. D-06 — viewer исключён без миграции (2026-11-21)

МедАрхив ещё не введён в эксплуатацию; пользовательских/операционных данных нет. Миграция viewer accounts, actor references и legacy ACL не требуется. Роль `viewer` полностью исключается из целевой модели и будущей реализации во всех слоях; целевые роли — `admin`, `branch`, `central_office`, `archive`. Удалить legacy viewer routes, UI/options, schema/ORM allowances и тестовые ожидания при реализации. Не перераспределять права автоматически. Исторические статические findings остаются основанием для удаления потенциально глобальных viewer paths, но account preflight/migration plan больше не требуются. Реализация не разрешена.

## 2026-11-22 — P-09 technical baseline superseded

The earlier readiness findings that assumed one indivisible fulfillment shipment and full shipment receipt are superseded. The approved P-09 workflow requires item-level archive issue, item-level actual CO receipt, partial issue, later issue of pending cards within the same request, partial cancellation of only unissued items, and atomic bulk receipt of selected cards that have actually arrived. Archive retains accountable custody until the corresponding CO receipt is confirmed. A courier's physical possession does not itself transfer accountable custody to CO.

The base storage model, DB transaction architecture and command mapping now contain explicit superseding addenda. Remaining readiness work is to normalize their earlier body text and derive one consistent set of persisted states, typed command signatures, event names/payloads, read models, queue counters and concurrency guards. This is a documentation/specification task before implementation; no DB or runtime behavior has been changed.

Ordinary box receipt remains distinct: no card count, seal or packaging inspection; placement is separate; bulk place/move is atomic; multi-box card extraction is prohibited.


## 2026-11-22 — dashboard queues and counters: current implementation gap

Read-only review of `src/medarchive/routers/postgresql_dashboard.py` (dashboard context, lines 145–250 and history reader) compared with approved queue decisions and target P-09 model found:

1. The current dashboard counts `ArchiveRequest.status IN ('new','in_progress')`. Current ORM requests are per-card; target P-09 is a parent with states `submitted/processing/completed/cancelled`. The target archive work queue must include parent requests in `submitted` and `processing`; the navigation “new requests” counter counts only `submitted`. One legacy count cannot serve both surfaces.
2. Current CO count filters per-card `requested_by` through users with role central_office; target scope is the parent request owner/creator and must be enforced on parent rows. Archive queue is shared for authorized active archive staff; branch has no P-09 queue.
3. Current shipment counters count rows joined to shipments with status exactly `in_transit`. Target incoming ordinary box shipments and P-09 card batches need manifest-item projections: for P-09 include outstanding unreceived cards in `in_transit` and `partially_received`; already received items must not be counted as in transit. For ordinary shipments, count each original manifest box exactly once based on its own receipt fact, independently of placement.
4. Current location counters use broad `from_location_id == scope OR to_location_id == scope`. Target lists/counters must use the explicitly allowed route pair and direction for each role, not merely any shipment touching the location. Current product scope excludes branch→CO and CO→branch.
5. Current dashboard history resolves request aggregates through the legacy per-card `ArchiveRequest` and its single `fulfillment_shipment_id`. The target projection must resolve parent request, item, and all linked fulfillment batches; batch completion cannot imply parent completion.

These are static source-to-target discrepancies, not proof of incorrect production behavior: the target P-09 schema is not implemented. Before implementation, derive separate SQL/read-model definitions and test fixtures for (a) archive active P-09 queue, (b) archive new-request badge, (c) CO own-request list, (d) sender/receiver shipment queues, (e) box-level receipt counters, and (f) item-level P-09 receipt counters. Apply role/scope filtering before counting, pagination or aggregation; avoid exposing patient PII in queue rows.
