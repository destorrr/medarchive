# UX-06 — целевая матрица PostgreSQL privileges и principals

Дата: 2026-11-18  
Статус: проектная спецификация; фактические grants не проверялись  
Основа: DB-SEC-01…04, UX-06-DB-SECURITY-TRANSACTION-ARCHITECTURE.md, UX-06-DB-DML-AND-PRINCIPALS-INVENTORY.md и UX-06-DB-COMMAND-CONTRACTS.md.

## 1. Назначение и границы

Документ определяет целевое распределение PostgreSQL ownership и privileges между владельцем объектов, миграционным процессом и runtime приложения. Это не описание текущей конфигурации PostgreSQL: подключения к целевой/тестовой БД, просмотра `pg_catalog` и проверки эффективных ACL в рамках этапа не было.

Цели:
- web workers используют только runtime credentials;
- runtime не получает членство в owner или migrator и не может переключиться в эти роли;
- критические workflow-переходы выполняются через узкий allowlist защищённых DB-команд;
- миграции и provisioning выполняются отдельным контролируемым процессом;
- чтение не становится глобальным только из-за наличия SELECT privilege: application authorization и объектный scope обязательны;
- инфраструктурные задачи (backup/restore, мониторинг, bootstrap) не маскируются под runtime.

Принципы least privilege, deny by default, отсутствие PUBLIC grants, явные schema/object grants и проверка эффективных прав после каждой миграции.

## 2. Роли и ответственность

| Principal | Тип | Назначение | Запреты |
|---|---|---|---|
| `medarchive_owner` | NOLOGIN | Владелец схем, таблиц, sequences, views, функций и триггеров приложения | Не используется приложением или человеком для интерактивного входа |
| `medarchive_migrator` | LOGIN | Только migration runner; выполняет DDL и контролируемый SET ROLE в owner | Не используется web workers, API, фоновой бизнес-обработкой |
| `medarchive_runtime` | LOGIN | Web/API и разрешённые runtime workers | Не владеет объектами, не имеет DDL, TRUNCATE, role administration, bypass RLS или прямого DML к критичным workflow-таблицам после cutover |

Роль owner — NOLOGIN. Мигратор получает возможность создавать/менять объекты от имени owner только через явно оформленное членство/механизм переключения роли. Такое членство не предоставляется runtime. Возможность SET ROLE должна быть проверена на реальных ACL; само наличие отдельного LOGIN principal не гарантирует корректного разделения.

Нельзя выдавать runtime членство в owner/migrator, superuser, CREATEDB, CREATEROLE, REPLICATION, BYPASSRLS или право менять параметры безопасности кластера.

## 3. Схемы и namespace

| Объект | owner | migrator | runtime |
|---|---|---|---|
| Схема приложения (например, `public`, если она остаётся целевой) | OWN | USAGE; CREATE только через контролируемый owner context | USAGE |
| Выделенная схема DB API (если будет введена) | OWN | DDL через owner context | USAGE |
| `pg_catalog` | PostgreSQL | системное чтение по умолчанию | только системные возможности, необходимые SQL |
| `information_schema` | PostgreSQL | чтение по необходимости | чтение только если нужно приложению |
| Любая иная схема | отдельный владелец | нет по умолчанию | нет по умолчанию |

Решение о сохранении `public` или введении отдельных схем `app`/`api` должно быть принято в DDL design. Нельзя предполагать, что перенос схем уже выполнен. Удалить CREATE из PUBLIC и проверить, что ни одна роль не может создавать объекты в search_path runtime. Для функций SECURITY DEFINER задать фиксированный безопасный search_path, не содержащий writable schemas.

## 4. Матрица по типам объектов

### 4.1 Таблицы

| Класс таблиц | Примеры/состав | owner | migrator | runtime после cutover |
|---|---|---|---|---|
| Критичные workflow aggregates | cards, boxes, box_cycles, box_card_memberships, box_placements, shipments, shipment_cards, shipment_boxes, archive_requests, archive_request_items | OWN + полный контроль | DDL через owner context | SELECT по необходимости; прямые INSERT/UPDATE/DELETE запрещены |
| История и события | domain_events, admin/security audit, command ledger | OWN + полный контроль | DDL через owner context | SELECT только разрешённых представлений/запросов; INSERT только через защищённые команды; UPDATE/DELETE/TRUNCATE запрещены |
| Пользователи и ACL | users, branches, locations, role/scope tables | OWN + полный контроль | DDL через owner context | SELECT минимальных полей для auth/scope; изменения только через admin DB commands |
| Справочники и курьеры | couriers, archive_shelves и другие справочники | OWN + полный контроль | DDL через owner context | SELECT; DML только через ограниченные admin commands |
| Login throttle | auth_login_throttles | OWN + полный контроль | DDL через owner context | ограниченный INSERT/UPDATE/DELETE только если выбран прямой runtime path; предпочтительно отдельная SECURITY DEFINER команда |
| Системные/служебные таблицы | migration metadata, command result, outbox/consumer offsets при наличии | OWN + полный контроль | мигратор владеет только migration metadata либо действует через owner context | только конкретные операции, требуемые runtime worker; никаких широких grants |
| Legacy/неиспользуемые таблицы | CardPlacement и иные legacy structures до миграции | OWN + полный контроль | DDL через owner context | временные права только для ещё действующих workflow; после cutover отозвать по подтверждённому inventory |

Для критичных таблиц запрет прямого runtime DML распространяется на все пути, включая вспомогательные endpoints, background jobs, scripts и admin workflows. Нельзя оставить скрытый обход command boundary через ORM flush, raw SQL, bulk update или каскадный write.

SELECT на базовых таблицах не означает, что runtime может отдавать любые строки пользователю. Scope должен проверяться в application layer и, если отдельно спроектировано и протестировано, дополнительно RLS. Не выдавать широкие SELECT на PII-таблицы без подтверждённой необходимости.

### 4.2 Sequences и identity

- Owner владеет sequences/identity objects.
- Migrator меняет их только в рамках миграций.
- Runtime получает только минимальные `USAGE`/`SELECT`/`UPDATE` права на конкретные sequences, если текущий способ генерации PK действительно требует их напрямую.
- Не выдавать `ALL ON ALL SEQUENCES` без проверки фактической потребности.
- При генерации ключей внутри DB command functions прямой доступ runtime к sequence может быть не нужен.
- Проверить default privileges отдельно: новые sequences не должны автоматически становиться доступными runtime шире целевой матрицы.

### 4.3 Functions и procedures

| Функции | owner | migrator | runtime |
|---|---|---|---|
| Защищённые workflow commands | OWN | CREATE/ALTER через owner context | EXECUTE только на allowlist |
| Внутренние helper functions | OWN | CREATE/ALTER через owner context | EXECUTE не выдаётся, если вызов не нужен напрямую |
| Admin/security commands | OWN | CREATE/ALTER через owner context | EXECUTE только на отдельный admin allowlist с проверкой actor/role |
| Read functions/views API | OWN | CREATE/ALTER через owner context | EXECUTE/SELECT только нужных read contracts |
| Migration/bootstrap helpers | OWN или отдельный infra owner | только migration/bootstrap principal | нет |
| Диагностические/maintenance functions | отдельный owner | контролируемый operator | нет, если нет отдельного обоснования |

У всех функций по умолчанию отозвать EXECUTE у PUBLIC, затем выдать адресные grants. Для SECURITY DEFINER:
- владелец функции — NOLOGIN owner;
- фиксированный безопасный search_path;
- schema-qualified обращения к объектам;
- минимальная поверхность SQL и строгая валидация входов;
- запрет динамического SQL из пользовательских параметров;
- явные проверки actor, role, scope, состояния агрегата и idempotency;
- тесты на вызов напрямую, обход ACL, подмену actor UUID и небезопасные object names.

SECURITY DEFINER не является самостоятельным доказательством личности. По DB-SEC-01 actor остаётся application-authenticated; DB command проверяет активность пользователя, роль и область доступа, но не подтверждает криптографически, кто прошёл аутентификацию.

### 4.4 Views и materialized views

- Owner владеет views и определяет security semantics.
- Migrator создаёт/меняет их через owner context.
- Runtime получает SELECT только на явно предназначенные read views.
- Views не должны непреднамеренно раскрывать PII или обходить объектный scope.
- Для security-sensitive views проверить поведение owner/invoker rights и RLS; не полагаться на view как на ACL без теста.
- Materialized view refresh — отдельная maintenance операция; runtime не получает REFRESH privilege без обоснования.

### 4.5 Triggers и ownership

- Trigger functions и triggers принадлежат owner и изменяются только миграциями.
- Runtime не получает права создавать, менять или отключать triggers.
- Append-only защита DomainEvent и admin/security audit должна оставаться эффективной при runtime role.
- Проверить, что runtime не может отключить trigger, сменить replication role, выполнить TRUNCATE или вызвать эквивалентный обход.
- Для миграций, изменяющих trigger, предусмотреть контроль целостности и повторное включение до допуска runtime traffic.

## 5. Объектная матрица runtime: разрешённое и запрещённое

### 5.1 Разрешённые операции после cutover

1. CONNECT к конкретной БД.
2. USAGE на нужных схемах.
3. SELECT минимально необходимых данных для чтения и формирования ответов с application-level scope.
4. EXECUTE только на опубликованных DB command/read functions.
5. Ограниченные операции login throttle — отдельный узкий grant или функция, не общий DML на схему.
6. Запись операционных событий — только в составе защищённой команды и той же транзакции.
7. Чтение command result для повторной выдачи результата идемпотентной команды — через предусмотренный контракт.

### 5.2 Запрещённые операции

- INSERT/UPDATE/DELETE к критичным workflow tables напрямую;
- UPDATE/DELETE/TRUNCATE DomainEvent и admin/security audit;
- DDL любого вида;
- CREATE в application/API schemas;
- ALTER/DROP объектов;
- владение таблицами, sequences, views или functions;
- SET ROLE в owner/migrator;
- создание ролей и изменение grants;
- отключение triggers;
- изменение системных параметров безопасности;
- прямой доступ к migration metadata, если он не нужен runtime;
- произвольный EXECUTE на внутренних helper functions;
- доступ к данным вне пользовательского/организационного scope.

## 6. Специальные технические пути

### 6.1 Login throttle

Текущий inventory обнаружил raw SQL INSERT/UPSERT/DELETE в `auth_login_throttles`. Это не основание выдавать runtime широкие права на таблицу.

Предпочтительный вариант: SECURITY DEFINER functions для атомарного increment/check/reset с фиксированным search_path и EXECUTE только runtime. Альтернатива — точечные INSERT/UPDATE/DELETE grants на одну таблицу, если доказано, что безопасно ограничить операции и их поля. Выбор должен учитывать гонки, очистку старых записей и denial-of-service сценарии. Не включать throttle table в общий workflow DML allowlist.

### 6.2 First-admin bootstrap

Текущий CLI использует обычный effective DB URL и UserRepository. Целевой bootstrap — отдельная одноразовая/операторская процедура с собственными credentials и ограниченным запуском:
- не использовать runtime URL;
- не передавать migrator credentials web workers;
- не оставлять постоянный универсальный bootstrap secret;
- проверять отсутствие активного admin и атомарно создавать первого;
- логировать запуск и результат без записи секретов/паролей;
- после bootstrap закрывать или отключать bootstrap path.
Точный principal и механизм остаются инфраструктурным решением; это не новая роль операционного пользователя.

### 6.3 Alembic и миграции

- Alembic использует отдельный migrator URL.
- DDL выполняется под owner context; runtime URL не должен иметь DDL.
- Migration runner запускается отдельно от web workers и не наследует runtime secret.
- Не допускать одновременного запуска несовместимых migration runners.
- Перед каждой миграцией проверять backup/restore readiness и совместимость с текущим runtime.
- Не отзывать старые права до завершения перехода всех write paths.
- После миграции проверять владельцев объектов, ACL, default privileges, PUBLIC grants, функции и sequences.

### 6.4 Backup, restore, monitoring и maintenance

Это отдельные инфраструктурные полномочия, не часть runtime/migrator бизнес-ролей:
- backup principal получает только необходимые права чтения/backup согласно выбранной стратегии;
- restore выполняется контролируемой infra процедурой с временным повышением прав;
- мониторинг получает минимальные статистические и health permissions, без чтения PII и workflow DML;
- maintenance/cleanup jobs получают отдельные адресные полномочия и не используют runtime credentials, если выполняют операции вне публичного command API.
Фактический перечень зависит от deployment platform и способа backup/restore; не выдавать эти grants заранее.

## 7. RLS и application authorization

DB grants и RLS решают разные задачи:
- grants ограничивают доступ principal к типам операций и объектам;
- application authorization определяет, какие данные и действия доступны конкретному authenticated actor;
- RLS может стать дополнительным защитным слоем, но только после отдельного дизайна session/actor context и защиты от подделки runtime principal.

При DB-SEC-01 передаваемый actor UUID сам по себе не является доверенным независимым credential. GUC, устанавливаемый runtime, нельзя использовать как доказательство actor identity или scope, если та же runtime роль может его произвольно менять. Не включать RLS как «защиту от runtime» без модели, которую runtime не может обойти.

Для каждого read path и command function проверить:
- actor active;
- допустимую роль;
- организационный/branch scope;
- ownership отправления/заявки;
- отсутствие доступа к чужим манифестам;
- особые правила admin по D-07;
- сохранение P-08 для участников конкретного отправления;
- запрет исходному branch видеть дальнейшую историю карты по D-08.

## 8. Default privileges и PUBLIC

Обязательные настройки/проверки:
- отозвать ненужные CREATE на общих schemas у PUBLIC;
- отозвать EXECUTE на новых functions у PUBLIC и выдавать адресно;
- не полагаться на default privileges без явной проверки владельца, который создаёт объект;
- задать default privileges для owner отдельно по tables, sequences и functions;
- убедиться, что migrator создаёт объекты именно под owner, иначе default privileges owner не применятся;
- проверять ACL как на существующих, так и на вновь созданных объектах;
- контролировать `search_path` ролей и функций;
- отсутствие неожиданных grants через PUBLIC, inherited roles и role membership.

## 9. Переходная матрица и rollout

Запрет runtime DML нельзя включать одномоментно, пока хотя бы один действующий ORM/raw SQL path зависит от него.

| Фаза | Runtime DML | DB commands | Условие перехода |
|---|---|---|---|
| 0. Baseline | Текущие фактические права, пока не подтверждены | Нет/частично | Инвентаризация всех DML paths, background jobs, scripts и CLI |
| 1. Principals | Временные права прежних workflow; без owner/migrator membership | Пилотные команды | Раздельные credentials, owner ownership, мигратор и runtime проверены |
| 2. Dual path | DML только на ещё не переведённые таблицы/сценарии | Новые команды для переведённых use cases | Для каждого path есть feature flag/route ownership, аудит вызовов и rollback |
| 3. Cutover per aggregate | Отозван DML на агрегатах, полностью переведённых на commands | Полный command path агрегата | Негативные DB tests доказывают отказ прямого DML; concurrency/idempotency tests успешны |
| 4. Final hardening | Нет прямого DML на всех критичных workflow tables | Только allowlist commands | Полный inventory закрыт, grants проверены, runtime regression suite успешен |

Временные DML grants должны быть адресными по таблицам и сценариям, иметь владельца, срок/условие удаления и отслеживаться в migration checklist. Не создавать бессрочную роль `legacy_runtime_writer` без явного плана удаления.

Rollback — не возврат к общему superuser/owner URL. Он должен быть заранее описан как контролируемое переключение версии приложения/command path и ограниченных ACL, с сохранением аудита и идемпотентности.

## 10. Проверки перед production cutover

### Роли и credentials
- [ ] owner — NOLOGIN, без пароля;
- [ ] migrator — отдельный LOGIN, credentials отсутствуют в web worker environment;
- [ ] runtime — отдельный LOGIN;
- [ ] runtime не является членом owner/migrator и не может SET ROLE в них;
- [ ] ни одна из ролей не имеет необоснованных superuser/CREATEDB/CREATEROLE/BYPASSRLS/REPLICATION;
- [ ] секреты раздельно хранятся и ротируются.

### Ownership и ACL
- [ ] владельцы всех application objects — ожидаемый owner;
- [ ] нет неожиданных PUBLIC grants;
- [ ] schemas, tables, sequences, functions, views и triggers проверены;
- [ ] default privileges соответствуют owner и новым объектам;
- [ ] SECURITY DEFINER functions имеют безопасный search_path;
- [ ] runtime EXECUTE только на allowlist;
- [ ] runtime не имеет DDL, TRUNCATE и прямого DML к cutover aggregates.

### Поведение
- [ ] прямой INSERT/UPDATE/DELETE в каждой критичной таблице от runtime завершается permission denied;
- [ ] разрешённые команды работают от runtime;
- [ ] недопустимые actor/role/scope/state отвергаются;
- [ ] повтор команды с тем же command_id возвращает тот же результат;
- [ ] повтор с иным payload конфликтует;
- [ ] параллельные cancel/handover и edit/start_processing сериализуются;
- [ ] append-only события нельзя изменить/удалить runtime;
- [ ] login throttle не требует лишних grants;
- [ ] bootstrap, backup/restore и maintenance не используют runtime credentials.

Все пункты должны быть подтверждены SQL/интеграционными негативными тестами на реальной конфигурации ролей. Статическое чтение migration files недостаточно.

## 11. Открытые технические решения

1. Оставить `public` или ввести отдельные application/API schemas.
2. Нужен ли RLS как дополнительный слой и как будет защищён actor context от подделки runtime.
3. Реализовать login throttle через SECURITY DEFINER functions или узкие table grants.
4. Выбрать точный bootstrap principal/процедуру первого admin.
5. Определить отдельные backup/restore/monitoring principals под целевую инфраструктуру.
6. Уточнить список read views/functions и минимальный набор SELECT по таблицам с PII.
7. Определить точный момент отзыва временных DML grants по каждому агрегату и механизм rollback.

Эти вопросы не меняют утверждённые DB-SEC-01…04; это уточнения физической схемы, deployment и rollout. Их следует закрыть до DDL/grants implementation.

## 12. Граница и следующий этап

Матрица — проектное целевое состояние, не фактическая проверка PostgreSQL. SQL GRANT/REVOKE, роли, схемы, функции, миграции, application code, tests и deployment не создавались и не менялись. PostgreSQL, Alembic, pytest и CI не запускались.

Следующий read-only этап UX-06: свести все выявленные технические разрывы и открытые решения в единый implementation/readiness plan с зависимостями, последовательностью миграции, критериями допуска и отдельными точками согласования.

## 12. Read authorization и PII — обязательный cross-layer контракт

PostgreSQL grants ограничивают runtime principal на уровне схем/объектов/операций, но сами по себе не дают конкретному сотруднику права видеть любую строку. Каждая read operation дополнительно применяет серверную авторизацию по активному actor, роли, организационному scope, custody/состоянию объекта, участию в shipment/request и отдельным правам на чувствительные поля. Прямой доступ по UUID/URL не расширяет эти права. Не перечисленное явно чтение запрещено.

Обязательные правила:
- `SELECT` выдаётся только для реально необходимых read paths и минимального набора полей; предпочтительны адресные read views/functions, если они дают доказуемую безопасную границу. Широкий SELECT на базовые PII tables не должен становиться публичным API.
- Проверка actor/scope выполняется до сериализации PII; поиск, autocomplete, filters, sorting, pagination, total counts, dashboard aggregates, badges и notification projections обязаны применять тот же scope до выборки/агрегации.
- Неизвестный и недоступный объект не должны различаться по ответу способом, раскрывающим наличие защищённого объекта.
- Участие в отправлении даёт доступ только к его манифесту/timeline в рамках D-09/P-08, а не к самостоятельной карточке карты, коробки или всей истории объекта.
- История карты для исходного филиала прекращается после передачи в архив (D-08); admin global read действует в пределах D-07, но не даёт обычных операционных прав.
- DOB и phone доступны только в самостоятельной карточке карты при отдельном разрешении; не включать их в очереди, манифесты, notifications, counts или обычные результаты поиска.
- Адрес пациента не хранится и не передаётся ни в одном слое.
- Самостоятельный экспорт не входит в текущий утверждённый scope; если будет отдельно одобрен, он не может обходить те же field/scope predicates.

Перед production cutover подготовить таблицу `role × object × field set × operation × scope × read surface` и негативную матрицу для прямого API/URL, чужих ID, фильтров, autocomplete, пагинации, counts, dashboards и выгрузок. Проверить реальные effective grants, views/functions, inherited roles, PUBLIC/default privileges и поведение на целевой PostgreSQL. Статическая проверка UI или ORM query недостаточна. Это проектный контракт; реализация, DDL и grants не разрешены настоящим документом.
