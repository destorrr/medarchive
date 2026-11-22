# UX-06 — DomainEvent consumer inventory: repository and operational surfaces

Дата: 2026-11-18; owner clarification: 2026-11-20  
Статус: read-only статический аудит; не реализация  
Проверенная ветка: `docs/ux-audit-project`  
Проверенный HEAD до фиксации: `3f72efcd147745c8e3052a37d68c7fb570d7ef52`

## Owner clarification — внешние потребители не планируются (2026-11-20)

Владелец продукта подтвердил, что внешние потребители DomainEvent на текущем этапе не предполагаются. Поэтому проектировать сейчас внешнюю доставку/подписки, outbox/consumer offsets, webhook/API integration contract или совместимость с внешними системами не требуется. Это продуктовое решение о текущем scope, а не доказательство отсутствия уже существующих deployment-level readers/jobs: перед изменением event schema, payload semantics или retention следует проверить фактические production jobs, integrations и effective DB grants. Внутренние потребители МедАрхива подлежат инвентаризации и сохраняют требования совместимости.

## 1. Границы проверки

Проверены дерево репозитория, PostgreSQL ORM model, PostgreSQL routers/services, legacy history/movement service, backup script/service и проектные readiness/audit документы. Это не проверка production deployment: фактические systemd units, environment, внешние интеграции, серверные cron, BI/ETL и сторонние consumers недоступны только из Git tree.

PostgreSQL, SQL, Alembic, pytest, CI, backup/restore и production hosts не запускались. Runtime данные не читались.

## 2. Подтверждённые DomainEvent readers

| Reader | Источник/фильтр | Назначение | Целевое решение |
|---|---|---|---|
| PostgreSQL dashboard recent movements | DomainEvent; выборка последних событий с role/scope predicates | Операционные события dashboard; для admin/viewer выявлен глобальный feed | Убрать глобальную операционную ленту; admin global feed — только admin/security audit (D-16), viewer исключён (D-06); operational summaries только в разрешённом scope |
| PostgreSQL dashboard history | DomainEvent по card/box/shipment/archive_request с visible-ID subqueries | Scoped history | Перевести на object timelines и проверить D-08; не превращать в глобальный feed |
| PostgreSQL shipment detail | DomainEvent по shipment aggregate ID | Timeline конкретного отправления | Сохранить; обеспечить постоянный доступ sender/receiver по P-08 |
| PostgreSQL admin audit page | DomainEvent с event_type LIKE 'admin.%' | Глобальный административный журнал; UI выводит JSON payload | Перенести authoritative admin/security evidence в отдельный audit store; сохранить исторические admin.* без потери, payload показывать по allowlist |
| PostgreSQL shipment exception-resolution detail | Точечное чтение shipment.exception_resolved по shipment aggregate | Actor/event для resolution view | Уточнить связь с целевым admin correction/audit flow |

Это перечень readers, найденных в просмотренных PostgreSQL router paths, а не доказательство полного перечня consumers во всех deployment environments.

## 3. Поправка: legacy /history не читает DomainEvent

Повторная проверка `dashboard.py::history_page` и `services/movement_service.py` показала: legacy `/history` вызывает `MovementService.get_recent_movements(50)`, который читает SQLite `card_movements` и JOIN к `cards`. Он не читает PostgreSQL `DomainEvent`.

Прежние формулировки в некоторых progress/audit записях, где legacy `/history` был назван DomainEvent consumer, неточны:
- legacy global `/history` — consumer старого `card_movements`;
- PostgreSQL dashboard history — отдельный DomainEvent reader;
- backend и router wiring нужно учитывать раздельно.

Legacy route всё равно требует retirement/replacement в рамках D-16/D-08, но не является DomainEvent consumer.

## 4. Backup, workers, export и external integration

### Backup

Обнаружены `scripts/backup.sh`, systemd service/timer и `services/backup_service.py`. Скрипт выполняет полный `pg_dump --format=custom`, проверяет архив через `pg_restore --list`, шифрует age и ротирует полные архивы (по умолчанию 24 копии). Он не выполняет выборочный экспорт DomainEvent, не публикует события и не поддерживает subscription/consumer offset.

Backup включает DomainEvent как часть всей БД, но не является прикладным consumer. Backup retention не задаёт retention DomainEvent или command ledger.

### Workers и schedulers

В просмотренном дереве `src/medarchive` есть FastAPI routers, CLI/bootstrap, workflow services и backup systemd timer. Отдельный event consumer, Celery/RQ worker, application scheduler, outbox dispatcher или webhook publisher не обнаружен.

Ограниченный вывод по репозиторию не доказывает отсутствие server-side cron/systemd jobs, внешних ETL/BI/reporting jobs, ручных SQL consumers, read replicas, sidecars/agents или сторонних систем, читающих БД.

### Export и integrations

В просмотренных DomainEvent readers отдельный export endpoint или фоновая доставка событий не обнаружены. Backup — полный DB dump, не event export. Доступные материалы не позволяют подтвердить или опровергнуть внешнее чтение `domain_events`.

Внешние интеграции и потребители сейчас не входят в утверждённый продуктовый scope. Перед изменением event schema всё равно необходимо проверить deployment jobs и effective DB grants/credentials, чтобы исключить фактически существующее чтение. Нельзя переименовывать event types, менять payload semantics или включать retention purge до такой проверки.

## 5. Consequences для целевого контракта

Producer families: card intake/custody, archive workflow, shipment workflow, admin service (текущие `admin.*`).

Цель: один общий append-only DomainEvent для бизнес-агрегатов и отдельный admin/security audit для привилегированных/security фактов. Текущие admin service events должны быть перенесены в отдельный audit record по non-destructive plan; это не второй DomainEvent stream и не RequestEvent.

До cutover необходимо:
1. описать allowlisted `event_type + event_version + payload schema`;
2. обеспечить object timelines для card, box, shipment и archive request;
3. сохранить shipment sender/receiver access по P-08;
4. убрать global operational feed с admin dashboard;
5. заменить legacy `card_movements` global history согласованными object-scoped surfaces, сохранив историю;
6. проверить отсутствие фактически действующих внешних consumers/jobs на deployment; новые внешние подписчики сейчас не проектируются;
7. не вводить механизмы дедупликации/offset для несуществующих внешних подписчиков;
8. согласовать retention отдельно для DomainEvent, admin/security audit, command ledger, backup и application logs.

## 6. Итог и readiness

Внутренние PostgreSQL readers в просмотренных router paths перечислены выше. Внешние потребители не предполагаются по текущему решению владельца продукта; deployment-level проверка фактических jobs/readers остаётся обязательной перед изменением event contract. Backup не является event consumer; app-level queue/worker/outbox/webhook dispatcher в просмотренном source tree не обнаружен.

UX-06 остаётся открытым. Следующие read-only участки: завершить P-04/P-07 decision brief и согласовать admin/security audit retention/outage; затем cross-document consistency pass. Effective ACL, production-like data preflight и внешние integration confirmations остаются readiness gates.

## D-03 — исключение legacy exception events (2026-11-21)

Старый процесс «Инциденты» исключён из целевой системы, рабочих данных нет. Поэтому `shipment.exception_registered` и `shipment.exception_resolved` не входят в целевой event catalog; exception-resolution detail reader не переносится. Не требуется миграция или retention plan для legacy exception events. Это не отменяет общего append-only DomainEvent для утверждённых бизнес-операций и отдельного admin/security audit. До изменения event schema в реальном deployment по-прежнему требуется проверить фактические jobs/readers и effective grants; это общий operational safeguard, а не основание сохранять исключённые event types.

## 2026-11-22 — consumer/read-model impact of item-level receipt

The current consumer inventory must be extended before implementation to project two distinct granular workflows. For P-09, one parent projects one-to-many fulfillment batches; each batch has a subset manifest and independent draft/handover/partial-receipt projection. A batch is not the parent request and its receipt does not complete the parent unless every request item is terminal.

1. Ordinary incoming box shipments: one receipt fact per manifest box; per-box custody transition; shipment remains open until all boxes and discrepancies are resolved; received boxes can independently enter the ready-to-place surface. Shipment counters derive from the immutable original box manifest and individual receipts.
2. P-09 archive → CO: one request item per medical card; one parent may have multiple fulfillment batches; each batch has its own subset manifest and independent shipment status projection. Partial archive issue and item-level actual CO receipt are separate events. Archive custody remains until item receipt; atomic bulk receipt emits one item receipt event per card in the same transaction. Parent remains processing while any item is nonterminal.

Object timelines must show item-level actor/time without exposing unrelated card history. Queue/dashboard projections must not infer parent completion from a shipment draft, courier dispatch or partial item receipt. Normalized target event types are specified in the event catalog: `shipment.draft_created`, `shipment.handed_over`, `archive_request.item_marked_not_found`, `archive_request.item_result_corrected`, `archive_request.item_removed_by_archive`, `archive_request.item_received_by_central_office`, `archive_request.remaining_items_cancelled`, `archive_request.completed`. Final integer schema versions, exact payload validators and effective-grant review remain pre-implementation gates.


## 2026-11-22 — повторная сверка readers и producers с целевым каталогом

Повторно просмотрены конкретные PostgreSQL router/service paths, а не только прежняя сводная таблица.

### Подтверждённые внутренние readers

| Код | Текущий источник | Фильтрация/использование | Целевое действие |
|---|---|---|---|
| `routers/postgresql_dashboard.py` recent movements | `select(DomainEvent)` | Общая лента; далее payload используется для формирования действий/сводок | Сохранить только scoped operational projection; глобальный feed не переносить |
| `routers/postgresql_dashboard.py` history | `select(DomainEvent)` | История по доступным card/box/shipment/archive_request IDs | Сохранить object-scoped timeline и проверить новые parent/item/batch aggregates |
| `routers/postgresql_shipments.py` detail | `DomainEvent` по shipment aggregate | Выводит event label, actor и payload | Сохранить; payload отображать через allowlist и с object access checks |
| `routers/postgresql_admin_pages.py` audit | `event_type LIKE 'admin.%'` | Глобальная административная лента и фильтры | Перенести новые admin/security facts в отдельный audit store; исторические DomainEvent сохранить read-only до миграции/retirement |
| `routers/postgresql_admin_pages.py` exception resolution | Точечный `shipment.exception_resolved` | Показывает resolution note/actor | Не переносить: legacy «Инциденты» исключены решением D-03; удалить reader вместе с legacy surface, не использовать как target audit |

### Несовпадения producer ↔ target catalog

- Текущий shipment workflow записывает `shipment.created`; целевой нормализованный каталог P-09 использует `shipment.draft_created`. Нужен переходный адаптер/новый producer contract; нельзя молча переименовать исторические события.
- Текущий shipment workflow всё ещё содержит producers `shipment.exception_registered` и `shipment.exception_resolved`, а также exception-resolution reader в admin pages. Они относятся к исключённому legacy-процессу D-03 и не должны входить в target event allowlist. Их наличие в исходниках означает остаточный legacy код, а не отмену решения D-03.
- Текущий admin service пишет `admin.*` в общий `DomainEvent`. Это подтверждённое текущее состояние; целевая архитектура требует отдельный AdminSecurityAudit. Исторические записи не удалять и не переписывать.
- Текущая ORM-модель `DomainEvent` не содержит `event_version`, `command_id` и `correlation_id`. Поэтому target catalog нельзя считать реализованным только на основании существующего JSONB payload.

### Scope и граница

Внешние event consumers по решению владельца продукта сейчас не проектируются. Это не снимает обязательную проверку deployment-level readers/jobs и DB credentials/grants перед изменением event contract. В этом проходе просмотрены только доступные исходники; production jobs и внешние SQL readers не подтверждены.


## 2026-11-22 — event schema versioning and legacy reader contract

The normalized P-09 event catalog currently supplies event names and semantic payload fields, but final integer versions and machine-readable validators are not yet defined. The ownership/idempotency audit now records a proposed convention: first schema for each new normalized event starts at event_version 1; incompatible payload changes increment the version; reject unsupported versions/unknown keys/oversized payloads. This is a design proposal, not a previously approved integer-version decision.

Compatibility rules:
- preserve historical event_type/payload unchanged; never backfill guessed event_version/command_id/correlation_id;
- keep historical shipment.created unchanged; only a read-only adapter may present it as the draft-created concept when payload proves equivalence, while retaining original event identity/type;
- never synthesize missing item receipt or custody facts;
- retire D-03 exception event producers/readers instead of adding them to target allowlist;
- retain historical admin.* DomainEvent as legacy evidence while routing new admin/security writes to AdminSecurityAudit.

One command/correlation pair links all mandatory records emitted by a command; atomic bulk P-09 receipt emits one item receipt event per card under that shared pair. Result envelope version, command input version and event payload version are distinct version axes. Remaining command_id lifecycle, canonicalization/hash, replayable negative outcomes, exact validators, HTTP mapping and deployment consumer/grant preflight remain open gates.
