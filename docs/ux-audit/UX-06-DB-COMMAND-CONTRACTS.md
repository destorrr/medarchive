# UX-06 — контракты защищённых DB-команд для заявок, отправлений и хранения

Дата: 2026-11-18  
Статус: проектная спецификация, не реализация  
Основа: принятые D-01…D-18, P-01…P-09 и DB-SEC-01…04; статическая инвентаризация текущего PostgreSQL кода.

## 1. Общие требования к каждой команде

Все команды выполняются одной транзакцией PostgreSQL. Runtime вызывает только разрешённые функции/процедуры; прямые INSERT/UPDATE/DELETE критичных таблиц после cutover запрещены.

### 1.1 Общий envelope

Каждая команда получает:
- `p_actor_user_id` — UUID пользователя, аутентифицированного application layer;
- `p_command_id` — UUID команды для идемпотентности;
- `p_correlation_id` — UUID бизнес-операции/сквозной трассировки;
- бизнес-параметры команды;
- при необходимости `p_reason` / `p_comment`.

Actor trust соответствует DB-SEC-01: БД проверяет, что пользователь существует, активен и имеет допустимую роль/branch scope; это не независимое криптографическое доказательство личности.

DB boundary обязан проверять:
1. actor существует и активен;
2. actor role и область полномочий соответствуют операции;
3. объект и связанные объекты доступны actor по серверному scope;
4. исходный статус и все предусловия;
5. уникальность `command_id` в пределах идемпотентного журнала;
6. бизнес-инварианты независимо от UI.

Возвращать типизированный результат: `success`, `already_applied`, `not_found`, `forbidden`, `invalid_state`, `conflict`, `validation_error`. Внутренние SQL детали не раскрывать пользователю.

### 1.2 Общая транзакционная последовательность

1. Проверить/зарегистрировать idempotency key.
2. Заблокировать корневой агрегат.
3. Заблокировать карты в сортированном порядке UUID.
4. Заблокировать коробки, циклы, membership и placements в стабильном порядке.
5. Заблокировать/проверить shipment и manifest, если затрагиваются.
6. Повторно проверить предусловия после получения блокировок.
7. Выполнить все изменения состояния.
8. Записать DomainEvent и необходимые admin/security audit entries.
9. Сохранить результат команды для безопасного повтора.
10. Commit; любая ошибка до commit откатывает все изменения.

Общий lock order: parent request → cards sorted → boxes/cycles/memberships/placements sorted → shipment/manifest. Для операций без заявки использовать соответствующий корневой агрегат (shipment, box/cycle, card или admin entity) и не нарушать единый порядок связанных ресурсов.

## 2. Команды родительской заявки ЦО

Целевая модель — `ArchiveRequest` как родительский aggregate и `ArchiveRequestItem` как отдельные позиции. Это проектная модель P-09; в текущем ORM она ещё не реализована.

### 2.1 `request_create_draft`

**Actor:** `central_office`.

**Вход:** набор уникальных `card_id`, `command_id`, `correlation_id`.

**Предусловия:**
- список непустой, без дубликатов;
- каждая карта существует, активна и доступна ЦО;
- карта не имеет конфликтующего активного запроса;
- карта находится на балансе ЦО в допустимом состоянии;
- все карты доступны в scope actor.

**Изменения:**
- создать один parent request со статусом `draft`;
- создать по одной позиции `pending` на карту;
- сохранить автора и timestamps.

**Физическое состояние:** не менять Card location, BoxCycle, membership или BoxPlacement.

**События:** `archive_request.created` с parent aggregate; событие состава/добавления item без копирования лишних PII.

### 2.2 `request_submit`

**Actor:** `central_office`, инициатор или пользователь ЦО с разрешённым scope.

**Вход:** `request_id`, idempotency metadata.

**Предусловия:** request `draft`, есть хотя бы одна активная позиция, позиции всё ещё соответствуют требованиям доступности.

**Изменения:** parent `draft → submitted`, зафиксировать submit actor/time.

**Физическое состояние:** никаких извлечений и перемещений карт.

**Событие:** `archive_request.submitted`.

### 2.3 `request_edit_submitted`

**Actor:** `central_office`.

**Вход:** `request_id`, списки add/remove card IDs, reason/comment при необходимости по продуктовой политике.

**Предусловия:**
- parent находится в `draft` или `submitted`;
- processing ещё не начался;
- удаляемые позиции принадлежат этому parent;
- добавляемые карты доступны ЦО, не дублируются и не конфликтуют с другими активными заявками.

**Изменения:**
- добавить позиции `pending`;
- удалённые ЦО позиции исключить из текущего тела заявки;
- факт удаления сохранить в audit/event trail с actor/time;
- после изменения состава перепроверить непустоту и актуальность parent state.

**Физическое состояние:** не менять коробки, membership и placements.

**Гонка:** сериализовать по parent request. Если параллельно archive пытается начать processing, только одна операция должна пройти; edit после успешного перехода в processing возвращает `invalid_state`.

**События:** `archive_request.item_added`, `archive_request.item_removed_by_central_office`, при необходимости `archive_request.updated`.

### 2.4 `request_start_processing`

**Actor:** `archive`.

**Вход:** `request_id`, idempotency metadata.

**Предусловия:**
- parent `submitted`;
- состав не пуст;
- все активные pending items проверены;
- каждая карта имеет ровно одно ожидаемое активное membership и допустимое архивное положение;
- карты не включены в активное отправление и не имеют несовместимого custody/status;
- archive actor имеет доступ к соответствующему архиву.

**Атомарные изменения:**
1. Заблокировать parent request.
2. Заблокировать все карты и их текущие циклы/членства/размещения в стабильном порядке.
3. Повторно проверить весь состав.
4. Закрыть активные `BoxCardMembership` для всех подлежащих обработке карт.
5. Перевести позиции в `extracted`, сохранить source box/cycle references для истории и возврата.
6. Для каждой коробки, у которой извлечение удалило последнюю активную карту, закрыть `BoxPlacement`; сама коробка остаётся в архивной локации без полочного размещения (D-11/P-06).
7. Parent `submitted → processing`.
8. Записать событие начала обработки и события извлечения позиций.

**Запрет:** нельзя оставить parent в processing с частично извлечёнными картами. Ошибка любого шага откатывает всю команду.

**Идемпотентность:** повтор с тем же command_id возвращает сохранённый результат; другой command_id при уже processing возвращает `already_applied/invalid_state`, без повторного извлечения.

### 2.5 `request_remove_item`

**Actor:** `archive`.

**Вход:** `request_id`, `item_id`, обязательный непустой комментарий, idempotency metadata.

**Предусловия:** parent `processing`, item `extracted`, карта ещё не передана в shipment.

**Изменения:**
- вернуть карту в допустимое архивное хранение через помещение в коробку;
- создать/активировать membership в текущем или новом BoxCycle по правилам D-10;
- item → `removed_by_archive`, сохранить комментарий, actor/time;
- обновить состояние пустой коробки/placement по правилам операции возврата, не путать с regroup.

**Атомарность:** возврат карты, изменение item и событие — одна транзакция.

**Запрет:** нельзя исключать item после handover; нельзя удалить item без возврата карты.

### 2.6 `request_mark_not_found`

**Actor:** `archive`.

**Вход:** `request_id`, `item_id`, idempotency metadata. Пользовательский комментарий/обоснование необязателен.

**Предусловия:** parent `processing`, item ещё не имеет окончательного результата, карта не находится в активном shipment и не была передана.

**Изменения:** item → `not_found`, сохранить actor/time; необязательный комментарий может быть сохранён, если предоставлен. Физическое размещение не создавать и не менять, поскольку карта не обнаружена.

**Финальность:** `not_found` — окончательный результат позиции. Повторная потребность оформляется новой заявкой (D-04).

### 2.7 `request_cancel`

**Actor:** до processing — `central_office`; после processing, но до handover — `archive`.

**Вход:** `request_id`, обязательная причина, idempotency metadata.

**Предусловия:**
- parent `draft/submitted` для отмены ЦО; либо `processing` для отмены архивом;
- shipment handover ещё не состоялся;
- нет позиции в `in_shipment/completed`.

**До processing:** parent → `cancelled`; все незавершённые items → `cancelled`; физическое состояние не меняется.

**После processing:** в одной транзакции:
- заблокировать parent, все extracted cards и связанные коробки/cycles;
- вернуть все карты, остающиеся в ответственности архива и ещё не переданные, в коробки;
- перевести незавершённые items в `cancelled`;
- сохранить окончательные `not_found` и `removed_by_archive`;
- parent → `cancelled`;
- записать причину, actor и события.

Если хотя бы одна карта не может быть возвращена, вся отмена откатывается.

**Гонка с handover:** request_cancel и request_handover сериализуются одной блокировкой parent. После успешного handover отмена невозможна.

### 2.8 `request_handover`

**Actor:** `archive`.

**Вход:** `request_id`, courier, manifest metadata, idempotency metadata.

**Предусловия:**
- parent `processing`;
- все позиции имеют проверенный результат: `extracted`, `removed_by_archive` или `not_found`;
- отсутствуют pending/unreviewed items;
- все extracted items, подлежащие отправке, доступны, не возвращены в коробку и не переданы;
- список отправляемых карт не пуст (если пуст, parent может быть завершён без shipment по P-09);
- ни одна карта не участвует в другом shipment.

**Атомарные изменения:**
- создать ровно один Shipment archive → central_office;
- добавить все оставшиеся к отправке карты в один manifest;
- одновременно перевести все соответствующие items `extracted → in_shipment`;
- выполнить единственный handover, перевести карты в shipment custody;
- связать shipment с parent request;
- parent остаётся `processing` до полной приёмки.

**Запрет:** partial shipment, несколько shipments на одну заявку и последовательные handover частей заявки запрещены.

### 2.9 Завершение parent request

Это часть `shipment_receive_all`, а не отдельное ручное изменение статуса.

Parent → `completed`, когда:
- все items имеют окончательный результат;
- если создан shipment, он полностью принят ЦО;
- все отправленные items → `completed`;
- ни одна позиция не остаётся pending/extracted/in_shipment.

Если все позиции получили `removed_by_archive` или `not_found`, заявка может завершиться без shipment.

## 3. Команда `shipment_receive_all`

**Actor:** только пользователь роли получателя на целевой локации (D-02).

**Вход:** `shipment_id`, полное подтверждение состава, подтверждение проверки и требуемые resolution metadata, idempotency key.

**Предусловия:**
- shipment находится в состоянии, допускающем полную приёмку;
- actor принадлежит получающей локации;
- манифест целостен;
- проверены все позиции; частичная приёмка запрещена;
- расхождения не приводят к молчаливой смене местоположения (D-02).

**Изменения:**
- атомарно принять весь manifest;
- обновить custody/location/status всех карт и коробок по маршруту;
- закрыть shipment;
- если shipment связан с archive request, все `in_shipment` items → `completed`, затем проверить условие завершения parent;
- записать события приёмки и завершения.

Если обнаружено расхождение, не выполнять обычную приёмку с автоматической корректировкой; использовать только предусмотренный согласованными правилами outcome/resolution, не вводя сущность incident (D-02/D-03).

## 4. Команды коробок и хранения

### 4.1 `box_create`

Создаёт физическую тару и начальный BoxCycle в указанной допустимой локации. Actor должен иметь право создавать коробки в этой локации. Человекочитаемый номер уникален. QR коробки не создаётся (D-17).

### 4.2 `box_add_card` / `box_remove_card`

Проверяют роль, location scope, статус коробки, активные shipments, membership карты и отсутствие конфликтующей операции. Membership добавляется/закрывается в рамках транзакции. Нельзя создать второе активное membership карты.

### 4.3 `box_repack`

**Actor:** операционная роль в разрешённой локации.

Операция перегруппировки:
- блокирует исходные и целевые коробки, циклы и все карты в стабильном порядке;
- закрывает исходные memberships;
- создаёт новый BoxCycle для каждой физической тары, которая получает новое заполнение;
- создаёт новые memberships;
- сохраняет историю старых циклов;
- не меняет полочное размещение только из-за перегруппировки;
- если коробка стала пустой в результате regroup, оставляет её на полке и выдаёт предупреждение (D-11/P-03).

Этот сценарий принципиально отличается от extraction по заявке: при extraction последней карты placement автоматически закрывается; при regroup пустая коробка остаётся размещённой.

### 4.4 `box_write_off`

Проверяет, что коробка пуста, не участвует в активном shipment и не имеет активного размещения на полке. Допускается только разрешённым ролям и в их scope (P-05). История физической тары/циклов не удаляется.

## 5. Карта и внешнее хранение

### 5.1 `card_register_manual` / `card_register_qr`

Actor — branch. Проверить уникальность номера/QR, допустимость payload, активность branch и отсутствие дубля. QR payload сохраняется в существующем формате (D-17). Создание карты и DomainEvent — атомарны.

### 5.2 `card_mark_external` / `card_return_external`

Actor — central_office. Проверить, что карта на балансе ЦО, не в shipment, не в коробке/активном membership и не списана. Внешнее хранение не является shipment route (D-05/D-18). Сохранить type, organization, comment и timestamps; событие записать в той же транзакции.

## 6. Административные DB-команды

Разрешённый набор следует ограничить конкретными use cases:
- `admin_create/update/deactivate_branch`;
- `admin_create/update/deactivate_user`;
- `admin_create/update/deactivate_courier`;
- `admin_create/update/deactivate_shelf`;
- `admin_correct` — только отдельный allowlist корректирующих операций с обязательным reason.

Для всех команд:
- actor role = admin;
- запрет самодеактивации и деактивации последнего active admin;
- deactivate вместо физического удаления;
- сохранение ссылок и истории;
- блокировка конкурирующих изменений;
- before/after, reason, actor, time и correlation записываются в отдельный admin/security audit;
- не позволять произвольные table/column names или произвольный SQL через generic correction API.

Текущая запись `admin.*` в DomainEvent не является достаточной целевой реализацией отдельного security/admin audit.

## 7. Общий DomainEvent contract

Обязательные envelope fields:
- `event_id`;
- `event_type`;
- `event_version`;
- `aggregate_type`, `aggregate_id`;
- `actor_user_id` (nullable только для явно определённых system/migration events);
- `command_id`, `correlation_id`;
- `occurred_at` (DB timestamp);
- `payload` с версионированной схемой;
- `request_id` / parent reference при применимости.

Требования:
- event_type входит в каталог/allowlist;
- payload валидируется по версии схемы;
- PII минимизируется; ФИО и номера карт не копируются в event payload без необходимости;
- события и изменения агрегатов записываются в одной транзакции;
- append-only запрет UPDATE/DELETE сохраняется;
- consumers должны быть идемпотентными и поддерживать переходные версии событий;
- admin/security audit не смешивается с операционной историей DomainEvent.

## 8. Идемпотентность и ошибки

- У каждой изменяющей команды есть `command_id`.
- Повтор того же command_id с теми же параметрами возвращает первоначальный результат.
- Повтор с тем же command_id и другими параметрами — конфликт.
- Команда, уже применённая другим command_id, не должна повторно менять состояние; возвращается корректный `already_applied` или `invalid_state`.
- Ошибки валидации не оставляют частичных изменений.
- Техническая ошибка/serialization failure допускает повтор всей транзакции только при наличии безопасного idempotency key.

## 9. Открытые технические вопросы перед SQL API

1. Какая точная схема idempotency storage нужна: command ledger с hash входа и сериализованным результатом или unique command_id в event envelope плюс lookup агрегата?
2. Должны ли DB-команды возвращать JSONB result или фиксированные composite types? Предпочтение — стабильный typed result, если он не усложняет versioning.
3. Как обеспечить actor scope в БД: проверка users.role/branch_id внутри команды плюс явно передаваемый target scope; не полагаться на GUC как доказательство.
4. Как реализовать отдельный admin/security audit и его read permissions?
5. Какие существующие event consumers требуют compatibility adapter?

Эти вопросы относятся к техническому контракту и могут быть закрыты статическим аудитом и проектированием без изменения кода.

## 10. Граница

Документ описывает ожидаемое поведение будущих команд. SQL functions/procedures, grants, новые таблицы, ORM, миграции, tests и deployment не создавались и не менялись. PostgreSQL, Alembic, pytest и CI не запускались.


## 11. Утверждённый idempotency contract (2026-11-18)

Этот раздел конкретизирует пункты разделов 8–9. Все четыре положения подтверждены владельцем продукта 2026-11-18 как обязательный технический стандарт МедАрхива. Это утверждение проектного контракта, не разрешение на реализацию.

### 11.1 Генерация command_id и повтор

- Для пользовательской команды клиент создаёт UUID v4 один раз при подтверждении действия и сохраняет его до получения терминального ответа. Сетевой retry, повтор отправки после timeout и восстановление запроса используют тот же command_id.
- Любое новое намерение пользователя, изменённый состав/параметры или новая попытка после полученного бизнес-отказа получает новый command_id.
- Для внутренних system jobs command_id создаёт доверенный server-side caller до первого вызова и сохраняет его при retry.
- command_id глобально уникален в пределах одной базы/среды; не включать actor_id в пространство уникальности вместо проверки совпадения владельца команды.

### 11.2 Canonical input и hash

Hash вычисляется только сервером/защищённой DB-командой после разбора входа по конкретной command schema. Клиентский hash не принимается.

Рекомендуемый алгоритм:
1. Валидировать command_type и command_version по allowlist.
2. Построить semantic input object только из полей, влияющих на результат команды. Transport headers, request timestamp, retry count, trace/span IDs и correlation_id не включать.
3. Применить schema-specific normalization: UUID привести к lowercase canonical form; даты/время — к UTC и единому формату; числовые значения — к однозначному представлению; применить defaults до hashing; определить для каждого поля, различаются ли absent и null.
4. Для object keys использовать стабильную сортировку; строки сериализовать в UTF-8 с фиксированными правилами escaping. Unicode normalization и trim не применять глобально: только если это прямо определено семантикой конкретного поля.
5. Массивы сохранять в исходном порядке, если порядок семантически значим; сортировать и дедуплицировать только для schema-полей, определённых как mathematical set (например, набор card IDs в команде, где порядок не влияет на manifest).
6. Вычислить SHA-256 от domain-separated bytes, включающих canonicalization version, command_type, command_version, actor_user_id и canonical semantic input. Пример логической структуры: medarchive-command\ncanon-v1\n<command_type>\n<command_version>\n<actor_uuid>\n<canonical-json>.

Отдельно хранить canonicalization_version. При изменении алгоритма не переинтерпретировать старые hashes; повтор существующего command_id сравнивается по сохранённой версии canonicalization.

### 11.3 Корреляция

- command_id — ключ идемпотентности одной команды.
- correlation_id — идентификатор более широкой бизнес-операции/цепочки команд; он может охватывать несколько command_id.
- correlation_id не влияет на input_hash. При replay возвращаются исходные command_id/correlation_id и сохранённый результат; новое trace ID не меняет семантику команды.
- request_id остаётся transport/request reference и не заменяет command_id или correlation_id.

### 11.4 Транзакционный протокол и конкурентный retry

- Ledger row, aggregate mutations, DomainEvent, admin/security audit и typed result commit-ятся одной транзакцией.
- Уникальный PK по command_id; конкурентная регистрация выполняется через INSERT ... ON CONFLICT DO NOTHING либо эквивалентный механизм, не оставляющий транзакцию в aborted state после ожидаемого конфликта.
- Конкурентный запрос с тем же command_id ждёт завершения первой транзакции. После её commit он читает ledger: совпавший actor/type/version/hash возвращает сохранённый результат; несовпадение — idempotency_key_reused без выполнения команды.
- Незавершённый ledger row не должен быть видим как committed in_progress. Временное состояние допустимо только внутри незавершённой транзакции; при crash/rollback исчезают и ledger row, и все бизнес-эффекты.
- После нахождения существующей записи повторно не выполнять мутацию. Перед выдачей сохранённого результата повторно проверить, что actor существует и активен; если результат содержит чувствительные данные — дополнительно проверить актуальное право прочитать эти данные. Повтор не должен раскрывать результат деактивированному/лишённому полномочий пользователю.

### 11.5 Какие отрицательные результаты сохранять

Рекомендуемое разделение:

| Ситуация | Записывать завершённую ledger запись? | Поведение retry |
|---|---|---|
| Невалидный envelope, неизвестный command type/version, отсутствующий/неактивный actor | Нет | Повторная проверка; security/diagnostic log отдельно по политике |
| Отказ аутентификации/авторизации или scope (forbidden) | Нет | Повторная проверка актуальных полномочий; не кэшировать отказ как бизнес-результат |
| Детерминированный бизнес-результат после успешной авторизации: not_found, invalid_state, conflict, validation_error | Да | Тот же ключ возвращает тот же отказ; новое намерение/повтор после изменения состояния — новый command_id |
| Успешная команда / подтверждённый already_applied в рамках конкретного idempotency contract | Да | Возвращается исходный сохранённый result envelope |
| Deadlock/serialization failure, timeout, connection loss до подтверждённого commit, internal error | Нет, транзакция откатывается | Повторить всю команду с тем же command_id; если commit мог завершиться, сначала проверить ledger |

Не следует сохранять transient infrastructure errors как терминальный результат. Для отказа авторизации отдельно сохранять минимальный security audit факт, но не копировать его в бизнес DomainEvent.

### 11.6 Result envelope и API mapping

Сохранять стабильные поля: result_code, result_version, result_payload, completed_at. Payload ограничен схемой конкретной команды и не содержит лишних PII/секретов. В envelope также возвращать command_id, correlation_id и ссылки на созданные aggregate/event IDs, если они нужны клиенту.

HTTP mapping остаётся responsibility API adapter, не DB command: успешный original/replay result сохраняет один semantic outcome; DB не возвращает SQLSTATE, имена constraints или внутренний текст исключения. Для idempotency_key_reused использовать явный conflict outcome.

### 11.7 Retention и защита от повторного исполнения после очистки

- Для необратимых логистических и административных команд command identity/tombstone хранить не меньше срока authoritative history; предпочтительный baseline — не удалять ключи необратимых действий, а минимизировать payload результата.
- Для остальных команд срок ledger должен превышать максимальный retry horizon всех клиентов, jobs и интеграций. До определения этого horizon TTL не утверждать.
- Если result payload очищается по retention, сохранять tombstone с command_id, actor, command_type/version, input_hash, canonicalization_version, завершённым result_code и ссылками на authoritative records. Удаление строки целиком вновь разрешило бы повторное исполнение старого ключа и потому запрещено без доказанного срока невозможности replay.
- Точный retention schedule, архивирование и purge policy остаются открытыми до определения legal/operational retention и backup lifecycle.

### 11.8 Утверждённый технический стандарт (2026-11-18)

Владелец продукта подтвердил все четыре положения как обязательный технический стандарт:

1. **Lifecycle ключа:** клиент создаёт UUID v4 один раз при подтверждении пользовательского действия и сохраняет его до терминального ответа. Все сетевые повторы, timeout retries и восстановление запроса используют тот же `command_id`. Новое пользовательское намерение или изменённые параметры получают новый ключ.
2. **Canonicalization и hash:** canonical input строится только на сервере по конкретной command schema; используется schema-specific normalization и SHA-256. В записи сохраняется `canonicalization_version`; старые hashes не переинтерпретируются при изменении алгоритма.
3. **Replay policy:** успешные результаты и детерминированные бизнес-отказы после успешной аутентификации/авторизации сохраняются и возвращаются при повторе того же ключа. Authentication/authorization failures и transient infrastructure errors не сохраняются как завершённые результаты команды.
4. **Retention/tombstone:** ключи необратимых логистических и административных операций не удаляются. При очистке result payload сохраняется tombstone с идентификатором команды, actor, типом/версией команды, input hash, версией canonicalization, result code и ссылками на authoritative records. TTL прочих команд определяется после inventory максимальных retry horizons клиентов, jobs и интеграций.

Дополнительные обязательные ограничения:
- `command_id` глобально уникален в пределах базы/среды; совпадение ключа с другим actor/type/version/hash приводит к `idempotency_key_reused`, без повторного исполнения.
- `correlation_id` не входит в input hash и связывает несколько команд одной бизнес-операции.
- Ledger, изменения агрегатов, DomainEvent, необходимые admin/security audit записи и результат команды фиксируются одной транзакцией.
- Replay не повторяет мутацию; перед возвратом результата повторно проверяются активность actor и актуальное право читать содержащиеся в результате чувствительные данные.
- Детерминированные бизнес-отказы сохраняются только после успешной авторизации. Ошибки envelope/identity/access и временные инфраструктурные ошибки не превращаются в терминальные бизнес-результаты.

Согласование не утверждает конкретные календарные сроки retention для обычных команд: они будут определены после инвентаризации retry horizons и требований к хранению.


### 11.9 Authoritative event and audit ownership (technical contract)

The two append-only stores have distinct authority; they are not duplicate copies of one event stream:

- **DomainEvent** is authoritative for the business fact and aggregate state transition, including a correction to card, custody, BoxCycle, BoxPlacement, shipment or archive-request domain state.
- **AdminSecurityAudit** is authoritative for the privileged/security action: authenticated admin actor, action/command, mandatory reason, authorization/outcome, target aggregate, and references to the resulting DomainEvent and command/correlation IDs.
- A P-07 command that changes a business aggregate writes one typed DomainEvent and one linked AdminSecurityAudit record in the same transaction. The audit record does not duplicate the full business before/after payload; it contains only allowlisted security context and references. DomainEvent does not become the global admin/security feed.
- Pure admin CRUD and authentication/authorization/security events write AdminSecurityAudit only; they do not create a business DomainEvent unless a future explicitly approved domain event is required for a separate aggregate.
- If either required write fails, the entire privileged mutation rolls back. No success response may be returned for a partially audited correction.
