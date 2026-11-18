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
| P-04/D-13 — списание медицинской карты | Открыто, владелец продукта | Основания, подтверждения, обязательные поля и проверки |
| P-07 — admin correction | Открыто, владелец продукта | Allowlist действий, допустимые состояния, причины, before/after и защита истории |
| D-10/P-03 — Box ↔ BoxCycle | Read-only модель подтверждена | Сохранить семантику в target schema |
| D-17 — QR карты | Read-only parser contract подтверждён | Не менять payload; обеспечить совместимость |
| D-06 — viewer | Продуктовое решение принято, миграционный план открыт | Инвентаризация аккаунтов/ссылок/ACL и безопасная миграция |
| D-03 — incidents | Продуктовое решение принято, retirement plan открыт | Сверка legacy exception shipments, UI и истории без автозакрытия |

## 3. Технические блокеры

### 3.1 Контракты команд и событий
- Завершить canonicalization/hash contract по типам команд.
- Определить replayable deterministic negative outcomes и result/error mapping.
- Утвердить retention и lifecycle command ledger.
- Утвердить versioned result envelope.
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
- Определить CardPlacement read-only retention и deletion criteria.
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
| Event/audit | Ownership концептуально разведено; schema, consumers и permissions открыты |
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