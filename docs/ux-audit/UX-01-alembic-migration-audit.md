# UX-01 — сверка Alembic-миграций и ORM-модели PostgreSQL

Дата: 2026-11-17  
Ветка: `docs/ux-audit-project`  
Статус: статический аудит файлов миграций и ORM; фактическая БД в этом блоке не подключалась.

## 1. Цепочка миграций

В `migrations/versions/` обнаружены следующие revision IDs и связи:

| Revision ID | Файл | Down revision |
|---|---|---|
| `0001_initial` | `0001_initial_postgresql_schema.py` | — |
| `0002_workflow_guards` | `0002_workflow_guards.py` | `0001_initial` |
| `0003_card_placements` | `0003_card_placements.py` | `0002_workflow_guards` |
| `0004_receipt_manifest_guard` | `0004_receipt_manifest_guard.py` | `0003_card_placements` |
| `0005_card_external_custody` | `0005_card_external_custody.py` | `0004_receipt_manifest_guard` |
| `0006_external_custody_at_other` | `0006_external_custody_at_other.py` | `0005_card_external_custody` |
| `0007_transition_meta` | `0007_transition_metadata_immutable.py` | `0006_external_custody_at_other` |
| `0008_shipment_integrity` | `0008_shipment_integrity.py` | `0007_transition_meta` |
| `0009_external_org_custody` | `0007_external_organization_custody.py` | `0008_shipment_integrity` |
| `0010_archive_fulfill` | `0010_archive_fulfillment_handover.py` | `0009_external_org_custody` |
| `0011_login_throttles` | `0011_login_throttles.py` | `0010_archive_fulfill` |

По прочитанным revision/down_revision IDs цепочка выглядит линейной; файл `0007_external_organization_custody.py` содержит revision ID `0009_external_org_custody`, то есть имя файла не отражает его revision number. Это не ошибка Alembic само по себе, но затрудняет ручную навигацию и аудит.

## 2. Существенные совпадения модели и миграций

- Начальная миграция создаёт базовые таблицы и ключевые CHECK/unique constraints для ролей, локаций, карт, коробок, циклов, членства, отправлений, заявок и размещения коробок.
- `0002_workflow_guards` добавляет связь исполнения заявки с отправлением и PostgreSQL trigger guards для маршрутов/переходов.
- `0003_card_placements` добавляет отдельные размещения карт на полках, partial unique index одного активного размещения и trigger проверки.
- `0004_receipt_manifest_guard` добавляет trigger проверки полного манифеста при приёмке.
- `0005_card_external_custody` добавляет внешнее состояние карты, поля организации/комментария/даты и CHECK согласованности; также вводит guards, исключающие внешние карты из упаковки и движения.
- `0006_external_custody_at_other` временно меняет значение `at_external_organization` на `at_other`; последующая revision `0009_external_org_custody` возвращает значение `at_external_organization`, соответствующее текущей ORM-модели и сервису.
- `0007_transition_meta` и `0008_shipment_integrity` добавляют неизменяемость фактов переходов и защиту указателей текущего отправления.
- `0010_archive_fulfill` обновляет trigger неизменяемости для атрибуции исполнения архивной заявки на handover.
- `0011_login_throttles` создаёт таблицу счётчиков ограничения попыток входа с CHECK положительного числа попыток.

По прочитанным фрагментам конечное значение внешнего статуса согласовано: ORM и сервис используют `at_external_organization`; промежуточное `at_other` мигрируется обратно в revision `0009_external_org_custody`.

## 3. Важные ограничения аудита

### 3.1 Alembic не сравнивает metadata с ORM автоматически

В `migrations/env.py` указано `target_metadata = None`. Следовательно, Alembic autogenerate не получает ORM metadata для автоматического diff. Миграции являются ручным источником схемы; проверка соответствия моделей должна быть отдельной процедурой/тестом.

### 3.2 Начальная миграция помечена как draft

Внутри `0001_initial_postgresql_schema.py` SQL-комментарий говорит: `target-schema draft` и `not a production migration`, хотя это первая исполняемая Alembic revision. Это создаёт неоднозначность статуса: является ли миграция утверждённым bootstrap целевой схемы или лишь проектным артефактом. Требуется уточнить и устранить противоречие до production rollout.

### 3.3 Нельзя подтвердить фактическое состояние БД только по файлам

В этом блоке не запускались `alembic upgrade head`, `alembic check`, introspection PostgreSQL или интеграционные тесты. Статическая цепочка файлов не доказывает, что целевая БД применена до head, что нет ручного schema drift или что все trigger functions присутствуют в существующем окружении.

### 3.4 Требуется точная сверка ORM и DDL

Нужно выполнить автоматизированную или вручную проверяемую сверку:
- всех колонок, nullable/default/type;
- FK, on-delete и deferrable/initially deferred;
- CHECK constraints и их точных выражений;
- partial unique indexes и обычных индексов;
- триггеров/функций, которые отсутствуют в ORM metadata;
- объектов, создаваемых вне ORM-моделей (например, `auth_login_throttles`).

Отдельно проверить, что constraints из миграций не потеряны в последующих revisions при изменении выражений.

## 4. Вопросы и действия перед production

1. Подтвердить статус `0001_initial`: утверждённая bootstrap-миграция или draft, который нельзя применять в production?
2. Добавить воспроизводимый CI-шаг миграций на пустую PostgreSQL 16 и проверку итогового schema state.
3. Добавить тест/скрипт сравнения фактической схемы PostgreSQL с ожидаемыми моделями и ручными объектами (triggers/functions/indexes).
4. Проверить путь upgrade с нуля и upgrade с предыдущей поддерживаемой версии, включая переход статуса `at_other` → `at_external_organization`.
5. Рассмотреть переименование файла revision `0009_external_org_custody` для соответствия ID, только если переименование безопасно для уже опубликованной истории и не меняет revision ID.
6. Проверить наличие отдельной миграции/механизма для всех новых ORM полей и индексов; не полагаться на совпадение имён.

## 5. Границы вывода

Это статическая сверка исходников миграций и ORM-модели. Она не является подтверждением успешного применения миграций к реальной базе. Код приложения и миграции не менялись.


## 2026-11-22 — повторная проверка полного Alembic-графа и initial revision

Повторно прочитаны все 11 файлов в `migrations/versions/`, их `revision`/ `down_revision`, `migrations/env.py`, `alembic.ini`, а также `db/postgresql/001_initial_schema.sql` и полный хвост `0001_initial_postgresql_schema.py`.

Подтверждено:
- Граф миграций линейный, с единственным root `0001_initial` и единственной вершиной файловой истории `0011_login_throttles`. Два файла с префиксом `0007` не образуют fork: `0007_transition_metadata_immutable.py` имеет revision ID `0007_transition_meta`; файл `0007_external_organization_custody.py` имеет revision ID `0009_external_org_custody` и зависит от `0008_shipment_integrity`.
- Цепочка: `0001_initial → 0002_workflow_guards → 0003_card_placements → 0004_receipt_manifest_guard → 0005_card_external_custody → 0006_external_custody_at_other → 0007_transition_meta → 0008_shipment_integrity → 0009_external_org_custody → 0010_archive_fulfill → 0011_login_throttles`.
- `0011_login_throttles` — файловая head-ревизия на этой ветке. Фактическое значение `alembic_version` в работающей БД неизвестно и этим аудитом не подтверждено.
- `0001_initial_postgresql_schema.py` содержит SQL, замороженный непосредственно в revision; upgrade удаляет строки BEGIN/COMMIT и исполняет statements по отдельности. Текст внутри замороженного SQL всё ещё называет схему draft/not production migration. Тот же статус указан в отдельном `db/postgresql/001_initial_schema.sql`. Следовательно, это внутреннее противоречие статуса bootstrap revision, а не основание считать SQL-файл независимым фактическим DDL.
- `migrations/env.py` оставляет `target_metadata = None`; автоматическое сравнение ORM metadata с БД через Alembic autogenerate не настроено.

Вывод: прежнее предположение о возможной развилке снято — граф линейный. Открытыми остаются подтверждение статуса bootstrap revision, реального DB head, drift и точная сверка ORM/DDL/triggers/indexes/grants. Переименование файла revision 0009 не требуется для устранения графовой проблемы и не должно предприниматься без отдельной оценки опубликованной истории.

Не выполнялись подключение к PostgreSQL, `alembic history/current/check/upgrade`, DDL, миграции или тесты. Это read-only анализ Git-файлов, а не DB preflight.
