# UX-06 — аудит DB roles, транзакционных границ и provenance актёра

Дата проверки: 2026-11-18  
Ветка: `docs/ux-audit-project`  
Проверенный HEAD до документации: `ee2af01085da7ece7eac5e9a20fd1112e60f8dd9`

## 1. Объём и достоверность

Выполнен статический аудит PostgreSQL adapter, конфигурации приложения, FastAPI dependencies и PostgreSQL routers, workflow services, ORM DomainEvent, Alembic environment и revisions, а также документации PostgreSQL deployment.

Проверены, в частности:

- `src/medarchive/adapters/postgresql/database.py`, `src/medarchive/config.py`, `src/medarchive/dependencies.py`;
- `src/medarchive/services/postgresql_archive_workflow.py`, `src/medarchive/services/postgresql_shipment_workflow.py`;
- PostgreSQL archive/shipment/branch/central-office routers;
- `src/medarchive/adapters/postgresql/models.py`;
- `migrations/env.py` и все текущие Alembic revisions от `0001` до `0011`;
- `docs/development/postgresql.md`.

Это чтение исходников и миграций в GitHub, а не проверка работающего окружения. Подключения к PostgreSQL, чтения системных каталогов, проверки effective grants, запуска миграций, SQL, pytest или CI не выполнялось. Поэтому фактические роли, владельцы объектов и права в развёрнутой БД остаются неизвестными.

## 2. DB roles и grants: фактическое состояние репозитория

### 2.1 Подключение приложения

`Config.database_url` читает `MEDARCHIVE_DATABASE_URL`; значение по умолчанию для разработки — `postgresql+asyncpg://medarchive:medarchive@localhost:5432/medarchive`.

`init_dependencies()` создаёт PostgreSQL Database с этим URL. PostgreSQL adapter формирует один async engine и один session factory; в коде не обнаружено раздельных application runtime и migration credentials.

Документация разработки приводит ту же локальную учётную запись и рекомендует менять URL для других окружений. Она предупреждает, что локальные credentials нельзя использовать в production. Конкретные production DB roles/grants и способ их provisioning в репозитории не описаны.

### 2.2 Подключение Alembic

`migrations/env.py` также читает `MEDARCHIVE_DATABASE_URL` и использует тот же default URL. Следовательно, при стандартной конфигурации приложение и Alembic используют одну и ту же учётную запись. Раздельные credentials возможны только за счёт внешнего окружения/обёртки, но это не оформлено как контракт репозитория.

### 2.3 Права и миграции

В проверенных Alembic revisions `0001`–`0011` не обнаружены исполняемые `CREATE ROLE`, `GRANT`, `REVOKE`, `SET ROLE` или функции `SECURITY DEFINER`. В initial schema есть комментарий о желательности revoke UPDATE/DELETE для DomainEvent, однако сам комментарий не создаёт привилегий.

DomainEvent защищён триггером `medarchive_domain_events_append_only`, который отклоняет UPDATE и DELETE. Это защита на уровне триггера, а не разграничение SQL privileges. Она не заменяет отдельную роль приложения с минимальными правами и не ограничивает произвольный INSERT в журнал.

**Вывод:** по репозиторию нельзя подтвердить, что production runtime использует отдельную ограниченную роль, а миграции — отдельную привилегированную. Нельзя также утверждать, что runtime не является владельцем таблиц или не имеет DDL/широких DML прав. Это требует отдельной проверки конкретного окружения.

### 2.4 Deployment templates

Проверены также `docker-compose.yml`, `scripts/install.sh` и `scripts/medarchive.service`:

- Docker Compose поднимает локальный PostgreSQL с одной ролью/пользователем `medarchive`; это dev-конфигурация с простым паролем, не production role model.
- Installer создаёт системного Linux-пользователя `medarchive` и шаблон `/etc/medarchive/medarchive.env`, но не создаёт PostgreSQL roles, базы, grants или owner role. Он оставляет `MEDARCHIVE_DATABASE_URL` как placeholder `USER:PASSWORD@HOST` и просит администратора обеспечить подключение.
- systemd запускает web-приложение от Linux-пользователя `medarchive` и читает единый env-файл. Этот Linux account не является автоматически отдельной PostgreSQL role.

Это подтверждает, что provisioning PostgreSQL privileges и разделение runtime/migration identities должны быть определены операционной процедурой либо добавлены в будущий deployment contract. В текущем installer нет такого provisioning.

## 3. SQLAlchemy sessions и транзакционные границы

### 3.1 Adapter и dependency

PostgreSQL Database создаёт engine и `async_sessionmaker` с `autoflush=False`, `expire_on_commit=False`. Метод `Database.transaction()` предоставляет контекст `session.begin()`, который автоматически commit/rollback по результату блока.

Однако `get_pg_session()` в FastAPI dependency открывает только session и передаёт её обработчику. Комментарий прямо говорит, что границами транзакций владеют бизнес-сервисы, но фактические router handlers обычно сами вызывают `session.commit()` и `session.rollback()`.

### 3.2 Workflow services

`PostgreSQLArchiveWorkflow` и `PostgreSQLShipmentWorkflow` принимают уже созданный `AsyncSession`, выполняют проверки, блокировки `SELECT ... FOR UPDATE`, изменения и `flush()`. Они не выполняют самостоятельный commit. Докстринг shipment workflow также говорит, что вызывающий код владеет транзакцией.

Таким образом, одна операция может быть атомарной, если все её изменения, проверки и события действительно выполняются в одном session transaction, а обработчик коммитит только после успешного завершения всего workflow. При исключении обработчик должен откатить эту транзакцию. Это соответствует наблюдаемому шаблону многих POST handlers.

### 3.3 Существенный архитектурный разрыв

Есть два конкурирующих описания ownership:

- adapter предоставляет явный `Database.transaction()` как рекомендуемую единицу работы;
- HTTP handlers фактически управляют commit/rollback напрямую, а service слой лишь использует session.

Это не обязательно приводит к частичной записи сейчас, но делает атомарность зависимой от дисциплины каждого route handler. При добавлении групповой заявки, массового extraction, отмены с возвратом нескольких карт и единого handover легко получить пропущенный commit/rollback, промежуточный flush или дополнительную операцию вне общей транзакции.

**Рекомендация:** для целевого workflow определить один явный application use-case boundary, который владеет транзакцией целиком. Предпочтительно сервисный unit-of-work через `Database.transaction()` или эквивалентный транзакционный контекст. Router должен валидировать HTTP-ввод, вызвать use case и преобразовать результат в HTTP-ответ, но не собирать вручную несколько независимых commit. Workflow не должен сам открывать вложенную/отдельную транзакцию и не должен коммитить до завершения всех связанных действий.

Для операций P-06/P-09 одна транзакция должна включать как минимум блокировку родительской заявки и затронутых карт/коробок, переход статуса заявки, все изменения membership/placement, создание/изменение shipment manifest, handover metadata и DomainEvent. Невозможность любого шага должна приводить к rollback всей операции. Конкретный lock order и сериализация по родительской заявке должны быть частью отдельного технического дизайна и тестов.

## 4. Actor identity и происхождение аудита

### 4.1 Текущая цепочка

PostgreSQL principal разрешается в `get_pg_current_user()`: session cookie проверяется через AuthService, UUID пользователя извлекается из payload, пользователь читается из PostgreSQL и проверяется на активность и credential version. Role dependencies ограничивают доступ к endpoint на уровне приложения.

Workflow получает `actor_id` от router. Методы workflow дополнительно загружают пользователя и проверяют активность/роль (например, `_user(actor_id, "archive")`, `_active_user(actor_id)`). При записи события этот UUID помещается в `DomainEvent.actor_user_id`.

Модель DomainEvent содержит FK на users, тип события, aggregate, timestamp, payload и correction reference. Триггер защищает существующие строки от UPDATE/DELETE.

### 4.2 Что это гарантирует и чего не гарантирует

При нормальном HTTP-вызове цепочка даёт полезную application-level проверку: пользователь прошёл проверку сессии, роль проверена dependency и workflow, а событие связывается с тем же actor UUID.

Но БД не получает криптографически или иным независимым способом подтверждённую личность человека. `actor_id` — параметр, переданный доверенным приложением. Если runtime DB principal может выполнять прямые INSERT/UPDATE в таблицы, он потенциально может подставить другой допустимый UUID в DomainEvent или изменить бизнес-данные в обход workflow. FK гарантирует существование пользователя, но не то, что именно этот пользователь аутентифицировался и имел право на данную операцию.

Append-only trigger не устраняет проблему происхождения INSERT. В проверенных migrations нет проверки actor provenance внутри БД и нет защищённого контекста команды.

### 4.3 Нельзя считать обычный session GUC доказательством

Передача actor UUID или operation context через `SET LOCAL` / `set_config()` сама по себе не решает задачу, если та же runtime DB role может произвольно устанавливать этот параметр. Пользовательский GUC может быть полезен как контекст корреляции/диагностики, но не должен считаться авторизационным доказательством.

### 4.4 Варианты целевой модели доверия

**Вариант A — ограниченная runtime DB role + закрытые DB command functions.**

- Runtime role не владеет таблицами и не получает произвольные INSERT/UPDATE/DELETE на критические таблицы.
- Изменения выполняются только через узкий набор проверенных DB-команд (например, функции с `SECURITY DEFINER`, принадлежащие отдельному владельцу), с фиксированным `search_path`, квалифицированными именами объектов, проверкой аргументов и запретом PUBLIC EXECUTE.
- Отдельная migration/owner role управляет DDL и не используется web-процессом.
- Функции атомарно проверяют состояния и выполняют связанные записи/события.

Этот вариант хорошо защищает целостность от произвольного SQL, доступного runtime role, но **не доказывает личность человека**, если функция просто принимает actor UUID от приложения. Actor в таком случае остаётся application-attested.

**Вариант B — независимое подтверждение actor.**

Если требуется, чтобы сама БД могла проверить, кто именно инициировал операцию, нужен доверенный источник actor assertion, недоступный для произвольной подделки обычным runtime-кодом/DB session. Возможные архитектуры: отдельный доверенный identity broker, подписанное краткоживущее утверждение с ключом проверки, доступным DB-командам, но закрытым от runtime DB role, либо отдельные DB principals на субъектов (обычно непрактично для пользовательской web-системы).

Подписание токена тем же секретом, который доступен обычному web-процессу, не даёт независимой защиты от компрометации этого процесса. Конкретный механизм требует threat model и отдельного решения; нельзя объявлять простой GUC или переданный UUID надёжной аутентификацией на уровне БД.

**Предварительная рекомендация:** сначала обязательно реализовать разделение runtime/migration/owner roles и закрытый DB command boundary для критичных операций. Actor provenance документировать честно как application-authenticated, если не принят отдельный независимый механизм. Не блокировать проектирование транзакций ожиданием сложной криптографической схемы, но и не приписывать БД гарантий, которых нет.

## 5. DomainEvent или отдельный RequestEvent

Текущий DomainEvent — универсальная append-only таблица событий агрегатов. События создаются из workflow с `aggregate_type`, `aggregate_id`, `actor_user_id` и payload; отдельной сущности RequestEvent в текущей модели не обнаружено.

Для целевой групповой заявки важны два разных слоя:

1. **Операционные события агрегата заявки** — создание, редактирование состава, начало обработки, исключение позиции, not_found, отмена, handover и завершение. Их можно хранить в общей DomainEvent при условии строгой схемы типов/payload, стабильной связи с родительской заявкой и позицией, append-only политики и фильтрации по scope.
2. **Административный/безопасностный аудит** — входы, изменения пользователей/справочников и корректирующие действия admin по P-07. Это отдельный контур доступа и назначения; его не следует смешивать с пользовательской операционной историей заявки.

**Предварительная рекомендация:** не вводить RequestEvent только ради групповой заявки. Сохранить DomainEvent как единый технический event store, но определить типизированный контракт событий и payload, обязательные ссылки на request/item/shipment, actor semantics, correlation/causation identifiers и ограничения доступа. Отдельную RequestEvent рассматривать только если появится измеримое требование к независимой retention, отдельным DB grants, высокой нагрузке/индексации или специализированному жизненному циклу. Решение должно быть подтверждено проверкой текущих потребителей DomainEvent и требований P-08/P-16.

## 6. Риски и решения до реализации

| ID | Наблюдение | Риск | Требуемое действие |
|---|---|---|---|
| DBSEC-01 | Runtime и Alembic используют один env var и одинаковый default URL | Нельзя подтвердить least privilege и separation of duties | Утвердить runtime, migration и object-owner roles |
| DBSEC-02 | В revisions нет явных grants/revokes/role provisioning | Права зависят от внешнего provisioning и владельца объектов | Подготовить role/grant matrix и deployment contract |
| DBSEC-03 | Append-only DomainEvent реализован триггером, не privilege boundary | Runtime SQL principal потенциально может вставить ложное событие | Закрыть прямую запись и оставить командный API |
| DBSEC-04 | Actor UUID передаётся workflow приложением | БД не может независимо подтвердить человека | Зафиксировать threat model и уровень доверия; при необходимости отдельный signed identity mechanism |
| DBSEC-05 | HTTP handlers управляют commit/rollback напрямую | Атомарность зависит от каждого endpoint | Перенести владение транзакцией в единый use-case/UoW boundary |
| DBSEC-06 | DomainEvent универсален, RequestEvent отсутствует | Неструктурированный payload может осложнить аудит заявок | Описать typed event contract; не вводить таблицу без обоснования |

До реализации групповых заявок P-09 необходимо дополнительно определить:

- точный перечень SQL privileges runtime role по таблицам/sequence/functions;
- owner role и migration role, запрет использования owner credentials в web-процессе;
- какие таблицы и переходы доступны только через DB command functions;
- способ проверки роли/области пользователя в команде и честное описание actor provenance;
- единый transaction boundary и lock order для start-processing, cancel, exclude-item, handover и receive;
- контракт событий заявки/позиции и связь с административным аудитом.

## 7. Что не проверено

- Effective roles, memberships, grants, ownership, default privileges и SECURITY DEFINER в работающей PostgreSQL.
- Фактическая production-конфигурация переменных окружения и секретов.
- Поведение PostgreSQL при конкурентных запросах и rollback.
- Исполнимость текущих миграций и наличие drift между установленной БД и Git.
- Результаты pytest/CI и прямые SQL негативные тесты.

Эти пункты требуют отдельного доступа к тестовой/production-like БД и отдельного согласования запуска. Настоящий аудит не подключался к БД и не запускал тесты.

## 8. Рекомендуемая последовательность

1. Согласовать целевую матрицу DB roles/grants и модель доверия actor.
2. Спроектировать DB command boundary и транзакционный Unit of Work для P-06/P-09.
3. Описать typed DomainEvent contract для request/item/shipment и разделение с admin/security audit.
4. После утверждения UX-06 подготовить отдельный additive-first migration design и план проверок.
5. Только после отдельного разрешения переходить к реализации, миграциям и тестам.

**Статус:** статический аудит завершён; рекомендации являются проектным предложением и не разрешают изменение приложения или БД.
