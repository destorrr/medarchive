# UX-06 — аудит владения событиями и идемпотентности команд

Дата: 2026-11-18  
Статус: read-only аудит и техническая спецификация; не реализация  
База аудита: `docs/ux-audit-project`, HEAD `2a1752459002bf8400f690e334bc97f6148e50c7`.

## 1. Цель и границы

Проверить фактическое использование PostgreSQL `DomainEvent`, определить authoritative record для операционных и административных фактов, зафиксировать связь событий с командами, а также закрыть технический вопрос идемпотентного исполнения защищённых DB-команд.

Проверка выполнена статическим чтением ORM, миграции 0001, PostgreSQL workflow services, admin services, routers и проектных контрактов. PostgreSQL, pg_catalog, SQL, Alembic, pytest и CI не запускались. Выводы о фактических production grants, триггерах и consumers в deployment не делаются.

## 2. Текущее состояние DomainEvent

### 2.1 ORM и schema

В `src/medarchive/adapters/postgresql/models.py` модель `DomainEvent` отображает таблицу `domain_events` и содержит:
- identity `id`;
- `event_type`, `aggregate_type`, `aggregate_id`;
- nullable `actor_user_id`;
- `occurred_at`;
- JSONB `payload`;
- `correction_of_event_id`;
- nullable `request_id`.

В рассмотренной модели отсутствуют отдельные поля `event_version`, `command_id` и `correlation_id`. Типизированный каталог событий и валидация payload по версии схемы в этих ORM-полях не выражены.

Миграция `0001_initial_postgresql_schema.py` создаёт ту же таблицу. Важное уточнение после проверки следующей миграции: `0002_workflow_guards.py` действительно создаёт функцию `medarchive_domain_events_append_only()` и trigger `trg_domain_events_append_only` на UPDATE/DELETE. Следовательно, append-only защита от изменения и удаления реализована на уровне миграционного DDL, а не только упомянута комментарием. При этом trigger не запрещает INSERT и сам по себе не ограничивает, кто вправе вставлять произвольные события; фактические grants/ownership и возможность обхода trigger привилегированными principals не проверялись.

### 2.2 Producers

Статически подтверждены следующие способы записи:
- `postgresql_archive_workflow.py::_event` добавляет DomainEvent в ту же AsyncSession, что и архивные изменения;
- `postgresql_shipment_workflow.py::_event` аналогично пишет события shipment workflow;
- `postgresql_admin_service.py::_audit_admin_change` пишет административные изменения в ту же таблицу с типами `admin.*`;
- `postgresql_card_intake.py` создаёт событие регистрации карты;
- другие workflow paths используют тот же общий event model.

Событие добавляется через ORM session/flush. Сам по себе flush не является commit; граница общей транзакции зависит от вызывающего router/session lifecycle. Это подтверждает совместное использование таблицы, но не гарантирует append-only защиту на уровне БД.

### 2.3 Consumers и read surfaces

В просмотренных файлах обнаружены как минимум два разных read use case:
1. `/admin/audit` в `postgresql_admin_pages.py` фильтрует только `event_type LIKE 'admin.%'`. Это административный интерфейс, но источник — общий `domain_events`.
2. Карточка отправления в `postgresql_shipments.py` выбирает события по `aggregate_type='shipment'` и `aggregate_id`, а UI отображает `event_type`/label. Это операционная история отправления.

Также admin page получает типы событий, агрегаты и список actors из общего журнала, ограничивая выборку префиксом `admin.%`.

Полный список consumers нельзя считать закрытым только по перечисленным маршрутам: отдельный исчерпывающий inventory всех SQL/read paths и внешних интеграций остаётся preflight-задачей. Наличие поля `request_id` само по себе не доказывает наличие работающего correlation pipeline.

## 3. Вывод по владению событиями

Целевой authoritative ownership следует разделить по классу факта, а не по физическому механизму записи:

| Класс факта | Authoritative record | Назначение |
|---|---|---|
| Изменение бизнес-агрегата: карта, коробка/цикл, заявка, отправление, custody | Общий append-only `DomainEvent` | Операционная история конкретного агрегата и интеграционные consumers |
| Административное изменение пользователя/справочника, security-sensitive action, correction | Отдельный append-only admin/security audit record | Контроль административных полномочий, расследование и аудит действий |
| Диагностика HTTP-запроса/технической попытки | Application/observability logs | Не является доказательством бизнес-факта и не заменяет ни один из двух журналов |

Следствия:
- `admin.*` в DomainEvent не должен оставаться единственным доказательством административного или security-sensitive действия.
- Для административной команды, которая меняет бизнес-агрегат, DomainEvent может фиксировать бизнес-факт, а admin/security audit — факт привилегированного вмешательства и before/after/reason. Эти записи не конкурируют: у них разные вопросы, на которые они отвечают.
- Для административной операции над справочником, не являющейся бизнес-событием агрегата, authoritative record — admin/security audit; искусственный DomainEvent создавать только ради заполнения общего журнала не требуется.
- Запись DomainEvent и соответствующей audit-записи для одной команды должна быть атомарна с изменением данных.
- Каждая пара записей связывается одинаковыми `command_id` и `correlation_id`; дополнительно допустимы ссылки на event/audit record IDs. Не копировать из одного журнала весь payload другого.
- Операционная история не должна использоваться как глобальный security audit, а admin/security audit не должен становиться альтернативной историей движения карт.

Это уточняет DB-SEC-03: сохраняется один общий тип DomainEvent; отдельная RequestEvent не вводится. Отдельный admin/security audit — не второй доменный event stream, а самостоятельный журнал привилегированных действий.

## 4. Целевой event envelope и append-only требования

Для новых/переведённых команд DomainEvent должен иметь:
- стабильный `event_id`;
- `event_type` из versioned allowlist;
- `event_version` (версия payload schema);
- `aggregate_type` и `aggregate_id`;
- `actor_user_id` с nullable только для явно разрешённых system/migration events;
- `command_id`;
- `correlation_id`;
- DB-generated `occurred_at`;
- минимизированный payload, валидируемый по `event_type + event_version`;
- parent/request reference только при реальной предметной связи.

Технические требования:
1. Изменение агрегата и соответствующий DomainEvent фиксируются одной транзакцией.
2. UPDATE/DELETE запрещены для runtime и защищены DB-level механизмом; коррекция — новое событие со ссылкой на исправляемое.
3. Не использовать свободный произвольный event type/payload от клиента; DB command формирует событие из проверенного результата.
4. Не помещать ФИО, номер карты и иные PII в payload без строго необходимой цели. Не копировать полный shipment manifest в события.
5. Consumers должны иметь собственную дедупликацию по event_id либо устойчивому consumer key и корректно обрабатывать повторную доставку.
6. Не менять семантику существующих event_type без версионированной миграции/адаптера.

## 5. Текущее состояние идемпотентности

В текущем ORM и просмотренных миграциях:
- нет модели/таблицы command ledger;
- нет уникального `command_id` в DomainEvent;
- нет input hash, статуса выполнения или сохранённого typed result;
- поле `request_id` не обеспечивает command idempotency и не должно переиспользоваться под эту цель.

Текущие workflow services выполняют ORM mutations и добавляют DomainEvent через AsyncSession. В просмотренных контрактах роутеров нет доказательства единого DB-level command key protocol. Следовательно, идемпотентность защищённых команд пока описана как требование, но не реализована в рассматриваемой PostgreSQL модели.

Локальные проверки состояния агрегата и row locks полезны для предотвращения повторного бизнес-перехода, но не заменяют идемпотентный replay: повтор запроса после потерянного HTTP-ответа должен вернуть сохранённый результат первоначальной команды.

## 6. Рекомендуемый command ledger contract

Рекомендуется отдельный `command_ledger` (концептуальное имя) с глобально уникальным `command_id` в пределах одной БД/среды.

Минимальные поля:
- `command_id UUID PRIMARY KEY`;
- `command_type` — стабильное имя команды;
- `command_version` — версия входного контракта;
- `actor_user_id`;
- `input_hash` — hash канонизированного, нормализованного входа;
- `correlation_id`;
- `result_code` — стабильный типизированный outcome;
- `result_version`;
- `result_payload JSONB` — ограниченный сериализованный результат, без избыточных PII;
- `completed_at`;
- при необходимости `created_at` и минимальные диагностические поля.

### 6.1 Протокол исполнения

В рамках одной DB-транзакции:
1. Проверить actor и допустимость command envelope.
2. Канонизировать вход по версии команды и вычислить hash на серверной стороне.
3. Попытаться зарегистрировать `command_id` с уникальным ограничением.
4. При конфликте уникальности дождаться исхода конкурентной транзакции и прочитать существующую запись:
   - тот же command_id, command_type/version, actor и input_hash, результат завершён — перейти к обязательным проверкам активности исходного actor и текущего права чтения из §6.1a; только после них вернуть сохранённый результат без повторного изменения данных;
   - тот же command_id с другим типом, actor или hash — вернуть `idempotency_key_reused`/conflict;
   - незавершённая запись не должна оставаться после штатного commit: ledger row и бизнес-изменения атомарны. Не вводить committed `in_progress` без отдельного lease/recovery протокола.
5. Если ключ новый — заблокировать корневой агрегат и зависимые строки в утверждённом lock order.
6. Проверить бизнес-предусловия, выполнить изменения и добавить DomainEvent/admin audit.
7. Сохранить типизированный результат в ledger.
8. Commit одной транзакцией.

Если транзакция падает до commit, вместе откатываются ledger insert, изменения агрегата, события и audit; клиент может безопасно повторить команду с тем же ключом. Для ошибок, которые являются ожидаемым бизнес-результатом (например, `not_found`, `forbidden`, `invalid_state`), нужно заранее решить, какие из них сохраняются как replayable outcomes. Сохранение детерминированных отрицательных ответов остаётся **открытым техническим решением**, а не утверждённым правилом: для каждого `result_code` требуется отдельная классификация (до регистрации команды / завершённый replayable outcome / rollback). Transient DB errors никогда не фиксируются как завершённый результат.

### 6.1a Авторизация при replay

Для точного повтора `command_id` порядок проверки и выдачи результата определён в UX-06-DB-COMMAND-CONTRACTS.md §1.4 и является обязательным:

- Сначала сверяются тип/версия команды, исходный actor и серверный hash входа. При несовпадении возвращается `idempotency_key_reused`/conflict без раскрытия сохранённого результата.
- Replay разрешён только исходному actor; его учётная запись должна оставаться активной.
- После этого проверяется текущее право actor читать все объекты и чувствительные поля, включённые в сохранённый result envelope. Историческое право на момент исполнения не заменяет текущую проверку.
- Отказ в чтении возвращается как безопасный `forbidden` либо недифференцирующий `not_found` по политике нераскрытия. Он не изменяет и не удаляет исходную ledger record и committed result.
- При успешных проверках возвращается точный сохранённый envelope. Бизнес-предусловия исходной команды не пересчитываются: изменение состояния после commit не должно превращать корректный replay в повторное исполнение или новый бизнес-отказ.
- Ошибка инфраструктуры при проверке/выдаче replay не сохраняется как новый завершённый outcome и не перезаписывает исходный результат.

Replay — это read/return path уже committed результата, а не новая бизнес-команда. Он не создаёт новых DomainEvent/AdminSecurityAudit записей.

### 6.2 Гонки и повтор

- Уникальный PK по command_id сериализует одинаковые ключи между конкурентными транзакциями.
- Два разных command_id на один агрегат сериализуются блокировкой агрегата и повторной проверкой состояния.
- Повтор команды после успешного commit возвращает первоначальный result, даже если агрегат с тех пор изменился.
- Повтор того же ключа с изменённым payload всегда конфликтует; нельзя молча трактовать его как новую команду.
- Для новой пользовательской попытки после бизнес-конфликта генерируется новый command_id.
- Входной hash должен включать все влияющие на семантику поля и версию контракта, но не нестабильные transport headers, время запроса или случайные trace IDs.
- Срок хранения ledger должен быть не короче периода, в течение которого клиент/оператор/очередь может повторить команду; для необратимых логистических и административных действий предпочтительно сохранять запись наравне с историей агрегата.

### 6.3 Результат команды

Предпочтителен стабильный result envelope с `result_code`, `result_version` и JSONB payload, валидируемый по контракту конкретной команды. Это проще эволюционировать, чем создавать PostgreSQL composite type на каждую команду, но требует строгой серверной валидации и versioning.

Внешние значения результата должны быть детерминированы и не раскрывать SQL/constraint internals. `success`, `already_applied`, `not_found`, `forbidden`, `invalid_state`, `conflict`, `validation_error` остаются семантическими исходами; повтор уже завершённой команды возвращает её исходный сохранённый результат, а не новый `already_applied`.

## 7. Безопасность command ledger

- Runtime не получает прямой INSERT/UPDATE/DELETE к ledger; только EXECUTE на защищённые DB-команды.
- Ledger доступен для чтения/изменения только владельцу и разрешённым DB-командам; runtime не должен иметь возможность подделать result.
- Actor проверяется по DB-SEC-01 внутри каждой команды; actor_id из envelope — атрибут application-authenticated actor, не независимое доказательство личности.
- Нельзя принимать input_hash от клиента как доверенный.
- Секреты, пароли, токены и полные медицинские данные в ledger не сохраняются.
- Для расследований связывать ledger, DomainEvent и admin/security audit по command_id/correlation_id, не предоставляя runtime прямой доступ к изменению журналов.

## 8. Решения и остаточные вопросы

Базовая архитектура command ledger, уникальность `command_id`, серверная проверка входа, сохранение и точный replay versioned result envelope, связь записей общими `command_id/correlation_id` и разделение authoritative DomainEvent/AdminSecurityAudit/ledger подтверждены последующими решениями и уточнениями ниже. Этот ранний список открытых вопросов является историческим и не должен трактоваться как повторное открытие уже утверждённых решений.

Текущие незакрытые технические вопросы перечислены в актуальном разделе «Still unresolved» ниже: canonical serialization/hash details, replayable deterministic negative outcomes, точные input/event/result validators и versions, client retry horizons, bounded cleanup/backup-restore safety, HTTP mapping и operational consumer/grant inventory. Срок обычного ledger retention утверждён отдельно: 90 календарных дней после завершения; irreversible logistics/admin command identities сохраняются бессрочно. Это не разрешает реализацию.

## 9. Граница

Изменения ограничены проектной документацией. ORM, SQL/DDL, миграции, routers, services, tests, workflows и deployment не менялись. PostgreSQL, pg_catalog, SQL, Alembic, pytest и CI не запускались. Реализация не разрешена.


## 10. Согласование с уточнённым command idempotency baseline (2026-11-18)

Подробное проектное предложение по command_id generation, canonical input hashing, correlation, concurrent replay, negative outcomes, result envelope и retention добавлено в раздел 11 UX-06-DB-COMMAND-CONTRACTS.md. Оно намеренно помечено как предложение, а не утверждённое решение. До SQL API остаются четыре вопроса владельца продукта: UUID v4/client lifecycle; canonicalization v1/SHA-256; сохранение детерминированных бизнес-отказов; tombstone/retention для необратимых команд.


## 11. Утверждённый срок хранения command ledger (2026-11-21)

Владелец продукта утвердил срок хранения записей идемпотентности обычных команд — 90 календарных дней после завершения команды; по истечении срока запись может быть очищена. Для необратимых логистических и административных операций command identity/tombstone сохраняется бессрочно. Очистка обычных ledger records не удаляет authoritative business records, DomainEvent или отдельные audit records. До реализации остаются проверка максимальных retry horizons фактических клиентов/jobs, проектирование ограниченной безопасной cleanup-процедуры и проверка взаимодействия с backup/restore. Решение о сроке принято; реализация/DDL не разрешены.


## 2026-11-22 — конкретизированы event compatibility gaps

Повторная сверка ORM, producers и внутренних readers выявила следующие конкретные переходные разрывы:

1. Текущий producer отправления пишет `shipment.created`, тогда как целевой P-09 catalog нормализует создание черновика как `shipment.draft_created`. Исторические строки не переименовывать; новый contract вводить с явным compatibility mapping.
2. Текущий shipment service всё ещё производит `shipment.exception_registered` и `shipment.exception_resolved`, а admin page читает `shipment.exception_resolved`. Решение D-03 исключает старый процесс «Инциденты» и эти типы из target catalog. Остаточный producer/reader — legacy code retirement gap, а не основание включать события в новый allowlist.
3. `PostgreSQLAdminService._audit_admin_change` пишет `admin.*` в общий DomainEvent; целевая модель требует отдельный AdminSecurityAudit. Перенос должен быть non-destructive: исторические admin.* остаются доступными как legacy evidence, новые действия пишутся в отдельный audit record атомарно с изменением.
4. ORM DomainEvent не имеет `event_version`, `command_id`, `correlation_id`; текущий payload JSONB сам по себе не образует версионированный контракт.
5. Подтверждённые внутренние readers: dashboard recent movements, dashboard object history, shipment detail, admin audit page и legacy exception-resolution detail. Последний исключается по D-03. Backup не является consumer; внешние consumers не входят в текущий product scope, но deployment inventory обязателен перед cutover.

До реализации остаются: закрытый allowlist target event types, целые версии payload schemas и validators, mapping legacy→target для чтения, политика показа payload по allowlist, перевод admin/security audit, retirement exception reader/producers, а также подтверждение фактических deployment readers/grants. Это документационная спецификация, не разрешение менять ORM/DDL/services.


## 2026-11-22 — AdminSecurityAudit: producer, payload и DB privilege reconciliation

Дополнен статический аудит по postgresql_admin_service.py, postgresql_admin_pages.py, models.py, migration 0002_workflow_guards.py, initial SQL и DB-SEC policy.

- Текущий _audit_admin_change() пишет admin.* в общий DomainEvent и выполняет flush; admin router затем выполняет session.commit(). Это даёт общую транзакционную границу текущей admin mutation и legacy event, но не отдельный AdminSecurityAudit authoritative record.
- Текущие payload включают username/full_name, branch IDs, courier full_name/phone и before/after snapshots. Они не соответствуют целевому минимальному typed allowlist. Нужны отдельные схемы по event type; не переносить существующие payload как есть.
- /admin/audit имеет server-side require_pg_role("admin"), но читает DomainEvent (admin.%). Target reader должен читать отдельный AdminSecurityAudit; admin-only доступ сохраняется, audit-feed export отсутствует.
- В ORM DomainEvent отсутствуют version/command/correlation поля. Миграция 0002 создаёт UPDATE/DELETE trigger append-only, но он не определяет INSERT privileges и не защищает от владельца/суперпользователя.
- Статический поиск всех Alembic revisions и draft initial SQL: исполняемых GRANT/REVOKE, CREATE ROLE/USER, ALTER ROLE, default privileges или SECURITY DEFINER bootstrap не найдено; встречается только SQL-комментарий о планируемом REVOKE. DB-SEC-01…04 требуют отдельные runtime/migrator/infrastructure principals и контролируемую DBA настройку. Фактические grants/owners не установлены.

Не переписывать исторические admin.* DomainEvent; target migration должна сохранить их как legacy evidence и перевести новые producers/readers без потери истории. Реализация не разрешена.


## 2026-11-22 — event and command versioning contract normalized (design proposal)

This pass separates decisions already approved from the remaining implementation design. It does not claim that the current ORM or database supports these fields.

### Required event envelope for new writes

For each new target DomainEvent and AdminSecurityAudit record, the target envelope must carry:
- immutable event identifier;
- closed `event_type` allowlist;
- integer `event_version` for that event type's payload schema;
- aggregate type and aggregate ID;
- actor ID and database-recorded occurrence timestamp;
- `command_id` and `correlation_id`;
- strictly validated, bounded, event-specific payload.

The event catalog currently defines normalized P-09 names and required semantic fields, but does not yet provide complete machine-readable schemas or integer versions. Proposed baseline: first production schema for each newly introduced normalized event starts at `event_version = 1`; increment only when the payload contract changes incompatibly. Additive optional fields are permitted only when validators and all readers explicitly support them; unknown event types, unsupported versions, unknown payload keys and over-limit payloads are rejected at write boundary and rendered safely by readers. This is a technical proposal, not an owner-approved version assignment or implementation authorization.

### Historical compatibility

- Do not rewrite historical DomainEvent rows to add guessed versions, command IDs or correlation IDs.
- Do not rename historical `shipment.created` rows to `shipment.draft_created`; preserve original type and payload.
- Read compatibility must be explicit and read-only: legacy event types may be translated to a normalized presentation only where the old payload proves the semantic mapping. Preserve source event ID/type and mark the presentation as legacy; never synthesize missing item receipts, custody transitions, command IDs or actor/time facts.
- `shipment.exception_registered` and `shipment.exception_resolved` remain excluded from target allowlist under D-03; retire their producer/reader with the legacy incident surface rather than mapping them into new events.
- Historical `admin.*` DomainEvent rows remain legacy evidence; new admin/security writes use separate AdminSecurityAudit.

### Command envelope and replay boundary

The approved public DB-command result envelope is already versioned by `result_version` (baseline 1). Keep it distinct from input `command_version` and event `event_version`. A command execution uses one command_id and correlation_id; every mandatory event/audit record emitted by that command carries the same pair. Bulk P-09 receipt remains one command with one item-level receipt event per card.

The ledger must store the exact committed result envelope and replay it without recomputing aggregate state. Replay still rechecks current actor activity and authorization to read any sensitive fields before returning the saved result. Ordinary command ledger records expire 90 calendar days after completion; irreversible logistics/admin command identities remain permanent. Neither cleanup nor tombstone retention may delete DomainEvent, AdminSecurityAudit or authoritative aggregate history.

### Still unresolved; do not silently decide in implementation

1. UUID generation/client lifecycle for command_id and correlation_id, including retry behavior across tabs, browser reloads and client timeouts.
2. Canonical input serialization and hash algorithm/version (the older audit proposal mentions SHA-256, but canonicalization details were not owner-approved).
3. Which deterministic negative outcomes are committed/replayable versus rolled back; transient infrastructure failures must never be stored as completed outcomes.
4. Complete per-event integer schema versions, JSON schema/validator definitions, payload size/depth limits and compatibility test fixtures.
5. Complete per-command input schema/version, canonical hash allowlist and stable result_payload schema/version.
6. Exact HTTP mapping from DB result_code to status/body, including conflict, forbidden, validation and idempotency-key reuse.
7. Maximum retry horizon of actual clients/jobs and safe bounded ledger cleanup/backup-restore interaction.

External event delivery, outbox, offsets and webhook consumers remain out of product scope. Before event schema change or purge, deployment-level jobs/readers and effective DB grants still require read-only operational confirmation.


## 2026-11-22 — envelope/payload boundary and command versioning proposal

The AdminSecurityAudit common envelope already defines actor, timestamp, command/correlation IDs, outcome, reason and target references as record-level metadata. These fields must not also be duplicated in event-specific JSONB payloads merely because older P-09 tables list them under “required payload”. Payload contains only additional allowlisted event facts. This avoids duplicate/conflicting actor and timestamp values.

Three version axes remain separate: command_version (input semantics), result_version (committed DB result payload), event_version (event-specific payload). The public command envelope currently does not explicitly include command_version; adding it as required integer with first contract version 1 is recorded as a technical proposal, not an owner-approved decision. Every event emitted by one command shares command_id/correlation_id, including one receipt event per card in bulk receipt.


## 2026-11-22 — current API error mapping source audit

Static review of PostgreSQL archive, Central Office, shipment and admin routers plus archive/shipment/admin workflow services shows no single typed public error contract equivalent to the proposed DB command result envelope.

Observed patterns:
- Admin JSON routes map LookupError to 404, ValueError to 422 or 409 depending on endpoint, and IntegrityError to 409 with endpoint-specific generic text.
- HTML workflow routes commonly catch ValueError and render str(exc) directly into page error state or redirect message; IntegrityError is handled inconsistently, with some handlers rendering a generic message and others relying on constraints.
- Shipment detail has explicit 403/404 HTTPException cases, while workflow business errors remain service-specific ValueError subclasses.
- Legacy incident/exception errors and pages remain in current shipment/archive routers even though D-03 excludes that workflow from the target.

Consequences for the target DB command adapter:
1. Define a closed machine-readable error_code set and command-family allowlists; do not expose raw exception strings, SQLSTATE, constraint names or SQL diagnostics.
2. Map DB result_code to stable HTTP status and response body in one API adapter; HTML UI maps the same error_code to localized safe text.
3. Preserve distinction between validation_error, forbidden, not_found/non-disclosure, invalid_state, conflict, idempotency_key_reused and transient infrastructure failure. Do not collapse every IntegrityError into the same business conflict.
4. A transient infrastructure/commit failure is not a completed ledger outcome and must not be replayed as a stored business result.
5. Remove legacy incident-specific error mapping with the D-03 surface; do not add it to target error allowlists.

This is static source inspection; no API requests/tests were executed and no handlers were changed.
