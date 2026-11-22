# UX-06 — целевая архитектура DB security, транзакций и событий

Дата: 2026-11-18  
Статус: проектное предложение для согласования; не является разрешением на реализацию  
База аудита: `fba1da5dfe3b49d1705a961a4abd7596f1ca174b`

## 1. Цель и границы

Документ переводит выводы статического аудита DBSEC-01…DBSEC-06 в конкретную целевую архитектуру для PostgreSQL. Цель — обеспечить least privilege, единые атомарные бизнес-команды и непротиворечивую историю операций при сохранении принятой модели ролей МедАрхива.

Проектируется прежде всего для критичных процессов:

- P-06: начало обработки заявки ЦО с атомарным извлечением карт;
- P-09: полная обработка всех позиций до единственного полного manifest и атомарного handover; item-level actual receipt, атомарный bulk receipt и custody архива до receipt ЦО; отмена всей заявки только до handover;
- D-02: индивидуальная приёмка каждой коробки обычного отправления;
- D-10/D-11: циклы коробок, членства и размещение коробок;
- P-07: корректирующие административные действия с обоснованием и отдельным аудитом.

Не проектируются здесь конкретные SQL-функции, DDL, миграции, имена всех endpoint или изменения ORM. Это следующий этап после согласования.

## 2. Модель DB principals

### 2.1 Целевые роли

| Principal | LOGIN | Назначение | Запреты |
|---|---:|---|---|
| `medarchive_owner` | нет | Владелец схемы, таблиц, sequences, triggers и доверенных command functions | Не используется приложением, Alembic CLI или интерактивными сотрудниками |
| `medarchive_migrator` | да | Контролируемый deployment principal для Alembic/DDL; может временно выполнять `SET ROLE medarchive_owner` в миграционном процессе | Не используется web workers; credentials хранятся отдельно |
| `medarchive_runtime` | да | Единственный DB principal web-приложения; SELECT и EXECUTE разрешённых функций | Не владелец объектов; нет DDL; нет произвольного DML критичных таблиц |
| `medarchive_readonly_ops` | нет, опционально | Групповая роль для read-only диагностического доступа операторов/аналитики, если он действительно понадобится | Не нужна runtime-системе; не создавать без сценария |
| `PUBLIC` | встроенная | Никаких привилегий на приватные схемы/функции/таблицы | Отозвать лишние default/public privileges |

`medarchive_owner` — NOLOGIN и не должен иметь пароль. `medarchive_migrator` получает membership на owner role только для управляемого миграционного процесса; миграционный runner должен явно переключать роль и не передавать эти credentials приложению. Runtime role не является членом owner/migrator и не может делать `SET ROLE` в них.

Системный Linux user `medarchive` и PostgreSQL role `medarchive_runtime` — разные идентичности. Совпадение имён не создаёт связи между ними.

### 2.2 Схемы и ownership

Рекомендуется выделить приватную схему, например `medarchive`, либо сохранить текущую схему после явной фиксации ownership. Во всех SECURITY DEFINER функциях:

- фиксированный безопасный `search_path` (лучше `pg_catalog` плюс явно квалифицированная схема; не доверять пользовательским схемам);
- все таблицы, типы и функции указываются с квалификацией схемы;
- владелец — `medarchive_owner`;
- `EXECUTE` отозван у `PUBLIC`, выдан только `medarchive_runtime` на конкретные команды;
- аргументы проверяются, динамический SQL исключается либо строго параметризуется;
- функции не возвращают произвольные чувствительные данные и не становятся универсальным SQL proxy.

Роль владельца не должна быть superuser. Runtime/migrator не должны иметь superuser. Пароли/сертификаты не хранятся в репозитории.

### 2.3 Матрица прав

| Объект/операция | owner | migrator | runtime |
|---|---|---|---|
| DDL, ALTER, CREATE/DROP | владелец | через контролируемый SET ROLE | запрещено |
| SELECT справочников и разрешённых реестров | да | да | только необходимые таблицы/views |
| SELECT карт/коробок/заявок/отправлений | да | да | да, но дополнительно ограничивается application scope |
| INSERT/UPDATE/DELETE критичных workflow-таблиц | владелец/команды | миграционно при backfill | запрещено напрямую |
| Выполнение бизнес-команд | да | да для миграционных проверок, не штатных операций | только явный allowlist EXECUTE |
| Запись DomainEvent | через команду | миграционный backfill | только через команду |
| UPDATE/DELETE DomainEvent | запрещено обычному runtime | только особая миграционная процедура при необходимости, с сохранением истории | запрещено |
| Чтение административного/безопасностного аудита | по необходимости | по необходимости | только если это предусмотрено endpoint scope; не широкое право |

Важно: обычные ORM INSERT/UPDATE критичных таблиц перестанут работать после отзыва прямого DML. До такого переключения соответствующие use cases должны быть переведены на DB commands. Не вводить grants «на время» в production без сроков и плана отзыва.

### 2.4 Миграции и backfill

Alembic должен получать отдельный `MEDARCHIVE_MIGRATION_DATABASE_URL`; runtime — `MEDARCHIVE_DATABASE_URL`. Миграции должны проверять, что подключение соответствует ожидаемому migration principal, но не полагаться только на имя из переменной окружения: контроль дополняется проверкой `current_user`/membership и deployment pipeline.

Первичное создание principals и базы выполняется отдельным bootstrap-процессом DBA/infra, не Alembic revision, потому что создание ролей требует более широких cluster privileges. Alembic revisions создают/меняют объекты в принадлежащей owner схеме после переключения на owner role.

Для миграций данных, которым нужны права записи, migrator действует через контролируемую роль владельца и отдельные maintenance процедуры. Эти права не должны попадать в runtime.

## 3. DB command boundary

### 3.1 Принцип

Для критичных агрегатов БД предоставляет конечный набор бизнес-команд, а не универсальный CRUD. Runtime может читать данные согласно grants, но изменение жизненного цикла выполняет только конкретная функция/команда.

Рекомендуемые логические команды (имена условные):

| Команда | Основные проверки и атомарные изменения |
|---|---|
| `request_submit` | Проверить draft, scope ЦО, набор карт, отсутствие конфликтующих заявок; зафиксировать submitted без extraction |
| `request_edit_submitted` | Только до processing; изменить состав; удалить ЦО-позиции из текущего состава, добавить audit/domain event |
| `request_start_processing` | Заблокировать родителя и все затронутые объекты; submitted→processing; извлечь все карты; закрыть membership; снять placement только у опустевших коробок; записать события |
| `request_remove_item` | Только archive после processing; обязательный comment; вернуть карту в коробку; сохранить видимый результат removed_by_archive |
| `request_mark_not_found` | Только archive после processing; окончательный результат not_found; не менять местоположение карты как найденной |
| `request_cancel` | Проверить допустимую стадию и actor policy; до handover вернуть все извлечённые карты; cancelled для незавершённых позиций; сохранить окончательные результаты; одна транзакция |
| `request_handover` | Проверить завершённость проверки всех позиций; создать одно shipment; перевести все оставшиеся карты extracted→in_shipment; единый handover |
| `shipment_receive_box` | Приёмка одной коробки из manifest; атомарно перевести custody этой коробки архиву; shipment остаётся открытым до обработки всех коробок и расхождений |
| `box_repack` | Создать новый BoxCycle, закрыть старые membership; сохранить историю; отдельно применить правило D-11 для placement |
| `admin_correct` | Только allowlist корректировок, обязательное основание, append-only admin audit и DomainEvent/correction link |

Не каждая команда обязана быть одной PostgreSQL function. Допустима команда, реализованная приложением в одной транзакции, если runtime principal имеет необходимые права только на эти конкретные изменения. Но для операций, где нужен DB-level запрет обхода workflow, предпочтительнее закрытые функции с минимальным набором аргументов и прав.

### 3.2 Проверки внутри команд

DB command должна повторно проверять инварианты, не полагаясь на UI или предварительный SELECT в приложении:

- текущий статус родителя и позиции;
- разрешённый actor class/role и допустимую область действия;
- принадлежность карт заявке и отсутствие активного конфликтующего членства/отправления;
- состав текущего BoxCycle и отсутствие дублирующих активных memberships;
- целостность маршрута D-18 и состава отправления D-01;
- запрет отмены после handover;
- индивидуальную приёмку коробки по D-02;
- обязательные причины и комментарии;
- все условия D-11 для placement, включая различие extraction и regroup;
- все ограничения P-09 по полному manifest, единому handover, item-level receipt, атомарному bulk receipt и запрету отмены после handover;

Проверка роли в DB command не заменяет application ACL и не должна давать runtime доступ к чужим данным. В частности, application scope остаётся обязательным при чтении; функция должна получать минимальный контекст и проверять бизнес-предусловия.

## 4. Actor provenance: что именно гарантируем

### 4.1 Рекомендуемый baseline

Для первой целевой версии принять честную модель **application-authenticated actor**:

1. HTTP authentication проверяет подписанную сессию и актуальность пользователя.
2. Application layer проверяет роль и scope.
3. Use case передаёт в DB command actor UUID, command ID и correlation ID.
4. DB command проверяет, что пользователь существует и активен, что его роль соответствует типу команды, и записывает actor UUID в DomainEvent.
5. Runtime role не может напрямую вставить/переписать DomainEvent или выполнить критичный DML в обход command boundary.

Это создаёт защищённый audit trail против обычного SQL доступа runtime principal, но не защищает от полного компрометирования web-приложения, которое вправе вызвать команды и передать actor UUID. Документация и интерфейс администрирования не должны называть actor UUID криптографически подтверждённым БД.

### 4.2 Дополнительный уровень (отдельное решение)

Если threat model требует доказательства actor даже при компрометации runtime приложения, нужен отдельный доверенный issuer:

- identity broker выдаёт короткоживущий подписанный assertion с subject, role/scope, audience, command, expiry, nonce;
- signing key доступен только broker, не web worker и не DB;
- DB command проверяет подпись публичным ключом, срок, audience, command binding и nonce/replay;
- использованный nonce сохраняется атомарно с командой.

Это заметно усложняет эксплуатацию, ротацию ключей и доступность. Не включать в baseline без формального требования безопасности и отдельного threat model. Не использовать общий HMAC secret, доступный web process, как независимое доказательство.

### 4.3 Поля события

Для каждого события фиксировать:

- `actor_user_id` — пользователь, от имени которого приложение аутентифицировало команду; nullable только для системных/миграционных событий с явным actor type;
- `actor_type` — `user`, `system`, `migration`, `admin_correction`;
- `actor_role_snapshot` — роль на момент команды, если она нужна для аудита; не использовать как текущую ACL;
- `command_id` — идентификатор конкретной бизнес-команды/idempotency key;
- `correlation_id` — сквозная цепочка HTTP/use case/DB;
- `causation_event_id` — предыдущее событие, вызвавшее текущее, если применимо;
- `request_id`, `request_item_id`, `shipment_id` — явные ссылки/идентификаторы связанных агрегатов, где применимо;
- `occurred_at` — серверное время БД;
- `payload` — версионированная структура события.

Не полагаться на один свободный JSON payload для критичных полей, которые нужны для ссылочной целостности, поиска или аудита.

## 5. Транзакционная архитектура и Unit of Work

### 5.1 Владение транзакцией

Единицей транзакции становится application use case / Unit of Work:

- HTTP router валидирует форму и вызывает один use case;
- use case открывает одну DB transaction;
- workflow/domain logic не вызывает commit и не открывает независимые транзакции;
- DB command выполняется в той же транзакции;
- успешный use case завершает commit; исключение приводит к rollback;
- HTTP response формируется только после успешного commit.

Запрещены промежуточные commit между изменением статуса заявки, изменением membership/placement, shipment manifest и DomainEvent.

### 5.2 Общие правила блокировок

Для предотвращения гонок:

1. Всегда первой блокируется родительская ArchiveRequest для команд её жизненного цикла.
2. Далее карты блокируются по стабильному возрастанию UUID.
3. Затем коробки, BoxCycle и membership/placement — в стабильном порядке ID.
4. Для handover дополнительно блокируется/создаётся Shipment и строки manifest в стабильном порядке.
5. Все конкурирующие операции по заявке используют тот же lock order.
6. Уникальные ограничения и FK остаются последней линией защиты; предварительные SELECT не считаются защитой от гонки.
7. Для повторяемых HTTP-команд использовать idempotency/command_id и возвращать исходный результат уже применённой команды, а не выполнять второй раз.

Точный lock order нужно подтвердить по фактическим FK/триггерам и deadlock tests при реализации. Если команда затрагивает несколько заявок/родителей, порядок блокировки родительских UUID — по возрастанию.

### 5.3 Атомарные транзакционные сценарии

**Start processing (P-06):**
- lock request;
- проверить submitted и весь состав;
- lock cards/boxes/cycles/placements;
- закрыть все memberships;
- определить пустые коробки и закрыть только их BoxPlacement согласно extraction rule;
- установить extracted state/ответственность архива;
- request→processing;
- записать событие команды и событий агрегатов;
- commit только после всех успешных шагов.

**Cancel до handover (P-09):**
- lock request и все активные позиции/карты/коробки;
- запретить, если handover уже состоялся;
- вернуть все извлечённые, ещё не переданные карты в коробки;
- незавершённые позиции→cancelled, окончательные not_found/removed_by_archive сохранить;
- parent→cancelled, reason обязателен;
- события и возвраты в одной транзакции.

**P-09 full shipment handover / item-level actual receipt:**
- архив завершает обработку всех позиций до создания единственного shipment; все и только позиции `extracted` входят в полный manifest;
- физическая выдача не передаёт учётную ответственность ЦО: до фактического подтверждения получения она остаётся у архива;
- ЦО подтверждает фактическое получение отдельной карты; несколько фактически полученных карт допускается подтвердить одной атомарной bulk-командой;
- bulk-команда блокирует выбранные позиции/карты в стабильном порядке, повторно проверяет полномочия и фактическую допустимость receipt, пишет actor/time и item-level DomainEvent для каждой карты;
- если одна позиция не допускает receipt, весь bulk receipt откатывается;
- parent завершается, когда все карты полного manifest получили receipt, а остальные позиции имеют окончательные результаты `not_found`/`removed_by_archive`; если отправлять нечего, parent завершается без shipment;
- создание единственного shipment и handover полного manifest — отдельные атомарные команды; item receipt/bulk receipt фиксируют только фактически поступившие карты; после handover отмена заявки запрещена.

## 6. DomainEvent и административный аудит

### 6.1 Решение по модели

Сохранить DomainEvent как общий append-only журнал доменных событий. Не создавать RequestEvent в baseline: отдельная таблица не решает сама по себе проблемы прав, транзакций или provenance.

Текущие поля DomainEvent: `event_type`, `aggregate_type`, `aggregate_id`, `actor_user_id`, `occurred_at`, `payload`, `correction_of_event_id`, `request_id`. Перед миграцией проверить фактические consumers и совместимость старых payload.

### 6.2 Typed event contract

Использовать единый канонический каталог `UX-06-ADMIN-SECURITY-EVENT-CATALOG.md`; не создавать конкурирующий список event strings в этом документе. Для P-09 нормализованы:

- `shipment.draft_created` — создание единственного shipment с полным manifest после обработки всех позиций;
- `archive_request.cancelled` и `shipment.cancelled` — отмена всей заявки и её draft shipment до handover;
- `shipment.handed_over` — атомарная передача полного manifest;
- `archive_request.item_marked_not_found`, `archive_request.item_removed_by_archive`, `archive_request.item_result_corrected` — результаты обработки и их исправления до shipment draft;
- `archive_request.item_received_by_central_office` — отдельное событие на каждую фактически полученную карту, в том числе при атомарном bulk receipt;
- `archive_request.completed` — завершение parent после terminal outcomes всех позиций и receipt всех отправленных карт.

Actor/time/command/correlation metadata принадлежат общему event envelope, а не дублируются в JSONB payload. Integer `event_version` и machine-readable validators остаются техническим gate; окончательные версии нельзя выводить из суффиксов `.v1` в старых примерах. Событие должно иметь один стабильный aggregate owner; одно бизнес-действие может записывать несколько событий, но все они создаются в одной транзакции с общей `command_id`/`correlation_id`. Не дублировать чувствительные данные пациента в payload, если достаточно ссылок на объект и минимального снимка факта.
### 6.3 Разделение с admin/security audit

DomainEvent — операционная история карты/коробки/заявки/отправления, доступная участникам объекта по D-08/P-08/P-16 и соответствующему scope.

Административный/безопасностный аудит — отдельный контур доступа для входов, управления пользователями/справочниками и корректировок admin. Нельзя выводить его в обычную историю движения. Если существующая таблица `audit_logs` уже выполняет эту функцию, сохранить её отдельность и проверить права/immutability; не создавать дублирующую сущность до инвентаризации её текущих consumers.

## 7. Idempotency, ошибки и наблюдаемость

- Каждая изменяющая команда получает command_id; повтор с тем же ключом и тем же actor/command возвращает сохранённый результат.
- Повтор с тем же ключом, но другим payload, отклоняется как конфликт.
- Не логировать ФИО, номера карт и содержимое манифеста в application logs, SQL logs или exception messages без обоснованной необходимости.
- DB error наружу преобразуется в безопасный бизнес-ответ; внутренние детали схемы/SQL не раскрываются.
- Deadlock/serialization failure допускает ограниченный retry всей транзакции с тем же command_id, но не отдельных SQL statements посреди операции.
- Метрики наблюдаемости: command name, duration, outcome, retry count, correlation_id; не включать PII.
- События не должны публиковаться во внешние системы до commit. Для будущих интеграций использовать transactional outbox, если появится реальная потребность.

## 8. Переход без нарушения текущей работы

Рекомендуемый additive-first rollout:

1. Инвентаризировать реальные PostgreSQL owners, roles, grants и default privileges в целевом окружении; без этого нельзя безопасно отзывать права.
2. Создать owner/migrator/runtime principals и проверить подключение каждого в отдельной среде.
3. Добавить новые DB commands и event contract, не отзывая существующий DML в production до перевода всех соответствующих workflows.
4. Перевести по одному use case: start-processing, item outcomes, whole-request cancel before handover, one full-manifest handover, then item/bulk actual receipt.
5. Перевести router handlers на единый Unit of Work.
6. Прогнать позитивные, негативные, конкурентные и rollback тесты на PostgreSQL.
7. Сверить старые и новые события/историю, выполнить preflight и backfill только при доказанной необходимости.
8. Отозвать прямые DML privileges runtime на критичные таблицы; отдельно проверить, что приложение и health checks работают с минимальным набором прав.
9. Только после этого объявить DB command boundary обязательной.

Не переключать весь runtime на restricted grants до того, как все legacy CRUD paths, фоновые задачи, cleanup scripts, backup/restore и health checks будут проверены. Backup principal — отдельная инфраструктурная идентичность, не runtime и не migrator.

## 9. Критерии готовности будущей реализации

- Runtime не владеет объектами и не имеет DDL/superuser.
- Migration credentials отличаются от runtime и не доступны web workers.
- PUBLIC не имеет доступа к приватным таблицам/командам.
- Критические бизнес-таблицы не изменяются runtime напрямую после завершения перехода.
- Каждая P-06/P-09/D-02 операция — одна атомарная транзакция с единым lock order.
- Гонки cancel vs handover не создают двойного исхода.
- Невозможность возврата любой карты откатывает всю отмену.
- Ошибка extraction любой карты откатывает всю обработку заявки.
- Handover не может отправить неполный состав.
- Item/bulk receipt accepts only cards actually received by CO; bulk receipt is atomic for the selected set and emits one item receipt event per card.
- DomainEvent append-only и не может быть произвольно вставлен runtime в обход команды.
- Actor provenance в аудите честно обозначен как application-authenticated до внедрения независимого issuer.
- Операционная история и административный/безопасностный аудит разделены.
- Тесты подтверждают отсутствие прямых DML прав и невозможность вызвать команды за пределами allowlist.

## 10. Решения владельца продукта (2026-11-18)

Все четыре архитектурных решения согласованы и зафиксированы в `PRODUCT-DECISIONS-DB-SECURITY.md`:

1. **Actor trust — принято:** baseline `application-authenticated actor`, без отдельного identity broker. DB проверяет active/role и сохраняет actor; это не независимое криптографическое подтверждение личности.
2. **DB command boundary — принято:** критические переходы выполняются через защищённые DB-команды; после поэтапного перевода workflows runtime не имеет прямого DML к критичным таблицам.
3. **DomainEvent — принято:** общий append-only `DomainEvent`, типизированные версионируемые события; отдельная `RequestEvent` не вводится.
4. **DB principals — принято:** `medarchive_owner` NOLOGIN, `medarchive_migrator` LOGIN и `medarchive_runtime` LOGIN с разными credentials и connection URLs.

Эти решения утверждают архитектурное направление, но не разрешают реализацию. Код, DDL, миграции, тесты и deployment configuration остаются вне объёма UX-06 проектной документации до отдельного разрешения.

## 11. Следующий этап проектирования

Следующий технический шаг — спецификация контрактов DB-команд и сопоставление их с текущими ORM-моделями, таблицами, workflow services и consumers событий. Затем уточняются grants matrix, миграционный rollout и preflight. Перед проектированием каждого контракта проверять актуальные исходники read-only; концептуальные имена команд и событий не считать существующими API.

## 2026-11-22 — superseding transaction contract for P-09 and bulk box operations

This addendum supersedes the older partial-issue/remainder-cancellation wording in §§3–5 and §9. It retains item-level receipt, but shipment creation and handover use one complete manifest.

### P-09 item-level operations

The parent archive request contains multiple card items. Archive must finish processing every item before creating the single fulfillment shipment; the full manifest includes all and only `extracted` items. Partial issue, multiple shipments per parent and processing remaining items after handover are prohibited. A `not_found` item requires a closed-list reason code and optional comment; it does not write off the card or invent a new location. `removed_by_archive` is also a final processing outcome and is not converted to `cancelled` by remainder cancellation, because no such post-handover remainder operation exists. Before handover, only archive may cancel the whole request; after handover cancellation is prohibited. Item-level actual CO receipt and archive custody until each receipt remain distinct facts.

The database command boundary must provide:
- a command that creates the one shipment only after every request item has a final processing outcome, with all and only `extracted` items in its immutable manifest;
- an item-level actual-receipt command for CO;
- an atomic bulk actual-receipt command for a selected set of cards that have in fact arrived;
- an item-level not_found command with mandatory reason code;
- a whole-request cancellation command available only before handover, with mandatory reason; cancellation after handover is prohibited.

The full manifest is handed over in one atomic operation. Custody remains archive-owned until CO confirms actual receipt of each corresponding card. Courier physical possession does not itself transfer accountable custody to CO. The shipment has one handover but can have partial item-level receipt.

Bulk receipt locks selected request items and cards in stable UUID order, re-checks state and actor scope under lock, validates that every selected card has actually arrived, writes each item's receipt and actor/time, appends corresponding DomainEvents, and commits all selected items together. Any invalid selected item aborts the entire bulk operation. A repeated command_id returns the original result; a different payload under the same command_id is rejected.

Parent request completes only when every item has a final outcome: each `extracted` item has actual CO receipt, while `not_found` and `removed_by_archive` remain final processing outcomes. If no item is `extracted`, the parent completes without creating a shipment. An item awaiting receipt keeps the parent in `processing`; no item is issued later and no remainder cancellation exists.

### Ordinary box receipt and bulk placement

Ordinary branch→archive and central_office→archive box receipt does not count cards inside boxes and does not inspect seals or packaging. Placement is a separate command. Bulk placement of received boxes onto one active shelf is atomic. Bulk movement from one or more source shelves to one target shelf is atomic; a single invalid box aborts the whole batch. Each box receives its own placement history event with actor/time. Bulk extraction of cards from multiple boxes is prohibited.

The exact SQL function names, item status enum names, event schema versions, lock ordering details and API error envelope remain technical design work. They must preserve the full-manifest handover and item-level receipt boundaries.
