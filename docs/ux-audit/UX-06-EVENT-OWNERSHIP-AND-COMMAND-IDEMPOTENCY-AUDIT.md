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
   - тот же command_id, command_type/version, actor и input_hash, результат завершён — вернуть сохранённый результат без повторного изменения данных;
   - тот же command_id с другим типом, actor или hash — вернуть `idempotency_key_reused`/conflict;
   - незавершённая запись не должна оставаться после штатного commit: ledger row и бизнес-изменения атомарны. Не вводить committed `in_progress` без отдельного lease/recovery протокола.
5. Если ключ новый — заблокировать корневой агрегат и зависимые строки в утверждённом lock order.
6. Проверить бизнес-предусловия, выполнить изменения и добавить DomainEvent/admin audit.
7. Сохранить типизированный результат в ledger.
8. Commit одной транзакцией.

Если транзакция падает до commit, вместе откатываются ledger insert, изменения агрегата, события и audit; клиент может безопасно повторить команду с тем же ключом. Для ошибок, которые являются ожидаемым бизнес-результатом (например, `not_found`, `forbidden`, `invalid_state`), нужно заранее решить, какие из них сохраняются как replayable outcomes. Предпочтительно сохранять детерминированные ответы после успешной проверки actor/envelope; transient DB errors не сохранять как завершённый результат.

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

Техническая рекомендация аудита: принять command ledger с уникальным command_id, server-side canonical input hash и versioned result payload; DomainEvent и admin/security audit связывать общими command_id/correlation_id, но оставить разными authoritative records по назначению.

Это рекомендация в рамках согласованной архитектуры DB-SEC-02/03, а не разрешение на реализацию. До SQL API нужно ещё определить:
- точный canonicalization/hash contract для каждого типа команды;
- какие детерминированные negative outcomes сохраняются в ledger;
- retention/архивирование ledger и допустимость удаления;
- точный каталог event_type/version и миграционный адаптер для уже существующих consumers;
- полный inventory всех внутренних и внешних consumers;
- конкретную схему admin/security audit и её read permissions.

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
