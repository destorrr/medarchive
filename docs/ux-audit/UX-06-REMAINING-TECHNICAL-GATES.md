# UX-06 — остаточные технические и операционные gates

**Дата:** 2026-11-18  
**Базовый HEAD:** `d3fc9d541676ae40096875bfc4282482598e2de3`  
**Статус:** read-only audit synthesis; no implementation authorization.

## 1. Актуальные утверждённые основания

- P-04/D-13 и P-07 продуктово согласованы. P-07 card master-data allowlist пуст, включая `Card.phone`.
- DB-SEC-01…04 и command idempotency baseline утверждены.
- Один append-only/versioned DomainEvent stream используется для бизнес-фактов; отдельный AdminSecurityAudit — для security/privileged actions; command ledger — для idempotency/result replay.
- Для корректировки бизнес-агрегата DomainEvent и AdminSecurityAudit атомарны, но семантически различны; глобальный admin/security feed не дублирует операционные бизнес-события.

## 2. Остаточные gates и рекомендуемая последовательность

| Gate | Что ещё требуется | Почему блокирует |
|---|---|---|
| External consumers and grants | Подтвердить cron/systemd, ETL/BI, read replicas, ручные SQL consumers, интеграции и effective grants | Нельзя безопасно менять event schema/payload/retention, пока неизвестны читатели и обходные права |
| Deployment topology | Зафиксировать реальные runtime/migrator/backup/monitoring/maintenance identities, DB URLs и secret distribution | Нельзя окончательно проектировать grants, rotation и operational access на предположительной топологии |
| Typed command/event catalog | Завершить schemas, versions, event types, guards, outcomes и API mapping для P-04/P-07 и остальных critical workflows | Нужна однозначная DB/API/UI/test boundary до DDL |
| Admin/security audit policy | Retention/legal hold, IP/identity digest, HMAC key lifecycle, privileged-read scope, export/access logging, tamper evidence, outage behavior | Без политики нельзя проектировать retention, индексы, key handling и безопасный failover |
| Command ledger retention | Составить retry horizon для browser clients, workers, scheduled jobs и integrations; определить reversible/irreversible command catalog | TTL до этого может разрешить повторное исполнение старого command_id |
| Migration/reconciliation | Legacy SQLite, viewer, incident, CardPlacement и admin event evidence; mapping/checksums/reconciliation/rollback | Нельзя безопасно планировать destructive retirement или cutover |
| Evidence matrix | Positive/negative role tests, DB grants, append-only, atomicity, concurrent replay, rollback, migration verification | Нельзя считать технические контракты реализуемыми без проверяемых критериев приёмки |

## 3. Утверждённая политика при недоступности audit store (2026-11-18)

Владелец продукта утвердил baseline **fail closed** для привилегированных изменений и успешного входа:

- не выполнять privileged mutation, если audit запись не может быть зафиксирована в той же транзакции;
- не выдавать новую authenticated session после успешной проверки credentials, если обязательное событие успешного входа не сохранено;
- не блокировать автоматически обычные чтения только из-за недоступности audit store;
- не превращать все 401/403 в синхронно обязательные audit writes без отдельной оценки шума/устойчивости; policy для denial events должна иметь отдельный bounded design.

Это утверждённое целевое поведение, но не реализация. Для privileged mutation обязательная AdminSecurityAudit запись и бизнес-изменение фиксируются одной транзакцией; при ошибке вставки/commit откатывается вся операция. При успешном входе новая сессия не выдаётся до commit обязательного auth-success события. Обычные чтения автоматически не блокируются. Синхронное обязательное журналирование каждого 401/403 не утверждено и остаётся отдельной ограниченной политикой. Текущая реализация не менялась и требует отдельного внедрения/проверки.

## 4. Неизменяемые ограничения

- Не менять master и не сливать PR без отдельного разрешения.
- Не менять application source, ORM, SQL/DDL, migrations, tests, workflows или deployment на этой стадии.
- Не запускать/не заявлять PostgreSQL, pytest, CI или production проверки без фактического запуска и разрешённой среды.
- Не удалять legacy SQLite/viewer/incident/CardPlacement до отдельного migration/retirement plan.
- Не открывать UX-07 до закрытия UX-06 gates и read-only preflight.

## 5. Evidence

Документ основан на статической сверке Markdown-документов и Git tree на указанном HEAD. Это не подтверждает состояние production grants, внешних jobs или deployment. Runtime/DB/CI/tests не запускались.
