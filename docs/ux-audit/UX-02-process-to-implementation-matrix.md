# UX-02 — матрица процессов и реализации

Дата проверки: 2026-11-17  
Проверенная ветка: `docs/ux-audit-project`  
Проверенный HEAD: `d9c65c6353509601daa713baf2a093d1ddbab6f7`  
База сравнения: `docs/target-architecture-spec`

## 1. Назначение и границы

Документ сопоставляет фактические пользовательские процессы PostgreSQL-интерфейса с FastAPI endpoints, сервисным слоем и найденными тестами. Это аудит текущей реализации, а не утверждение целевых бизнес-правил.

Статусы:
- **Код + тест** — найден handler/service и релевантный интеграционный или ролевой тест.
- **Код** — реализация найдена, но подходящее тестовое подтверждение в проверенной выборке не установлено.
- **Частично** — проверена часть сценария; полный набор ролей, объектов или ошибок не покрыт.
- **Не найдено** — ожидаемый пользовательский шаг/обработчик в просмотренной поверхности не обнаружен; это не доказывает отсутствие реализации в иных компонентах.

Проверялись PostgreSQL routers, связанные workflow/services, основные шаблоны и следующие тестовые модули: `test_postgresql_interface_role_matrix.py`, `test_postgresql_full_logistics_cycle.py`, `test_postgresql_transfer_integration.py`, `test_postgresql_archive_fulfillment.py`, `test_postgresql_archive_integration.py`, `test_postgresql_central_custody.py`, `test_postgresql_admin_integration.py`, `test_postgresql_admin_role_matrix.py`, `test_postgresql_branch_search_history.py`, `test_postgresql_history.py`, `test_postgresql_dashboard.py`, `test_postgresql_packing_page_integration.py`.

Это статическая сверка исходников и чтение тестов. В рамках этого шага тесты и CI повторно не запускались.

## 2. Матрица: филиал

| ID | Шаг | Экран / HTTP | Сервис и серверные ограничения | Тестовое подтверждение | Оценка |
|---|---|---|---|---|---|
| BR-01 | Просмотр панели | GET `/dashboard` | PostgreSQL dashboard; branch scope по текущей активной локации | `test_postgresql_dashboard.py`, role matrix | Код + тест |
| BR-02 | Поиск карты | GET `/search`, `/search/results`; POST `/search` | Общий PostgreSQL search; branch scope по локации | `test_postgresql_branch_search_history.py` | Код + тест |
| BR-03 | Регистрация/проверка QR | GET `/packing`; POST `/packing/scan-card` | `PostgreSQLCardIntakeService.inspect_from_qr`: активный branch actor, привязанный филиал, активная локация; проверка номера и персональных данных, текущего баланса и custody | `test_postgresql_full_logistics_cycle.py`; admin integration включает конкуренцию QR intake с деактивацией филиала | Код + тест |
| BR-04 | Подтверждение регистрации и добавление в коробку | POST `/packing/confirm-card` | `register_from_qr` повторно проверяет actor/location и дубликаты; `add_card_to_box` повторно проверяет право на локацию, состояние коробки/карты, состав, размещение и активные отправления | `test_postgresql_full_logistics_cycle.py` | Код + тест |
| BR-05 | Создание коробки | POST `/packing/new-box` | `PostgreSQLShipmentWorkflow.create_box`; сервис повторно авторизует локацию и создаёт цикл состава | `test_postgresql_full_logistics_cycle.py` | Код + тест |
| BR-06 | Извлечение карты из коробки | POST `/packing/remove-card` | `remove_card_from_box`; проверка активной коробки/локации, отсутствия размещения и активного отправления, открытого цикла и членства | Сквозной тест полного цикла | Код + тест |
| BR-07 | Списание коробки | POST `/packing/write-off-box` | `write_off_box`; только активная пустая коробка в постоянной локации, вне отправления и размещения; сервисная авторизация | Admin integration покрывает деактивацию филиала после списания пустой коробки; отдельный полный POST role × object matrix не найден | Частично |
| BR-08 | Передача курьеру | GET `/transfer`; POST `/transfer/confirm` | `create_draft` → добавление коробки → `hand_over`; branch guard и сервисная проверка источника, получателя, курьера, состава, конфликта отправления | `test_postgresql_full_logistics_cycle.py`, `test_postgresql_transfer_integration.py` | Код + тест |
| BR-09 | Приёмка из ЦО | GET `/branch/receive`; POST `/branch/receive/confirm` | `receive`; branch guard, отправление адресовано активной локации филиала, полный манифест | Ролевые/сквозные тесты, включая полный цикл; отдельная матрица POST для всех чужих shipment IDs не подтверждена | Частично |
| BR-10 | Разрешение инцидента | POST `/branch/receive/resolve` | `resolve_exception`; обязательны подтверждение и непустая заметка; общий полный receive workflow | Сквозные тесты логистики/инцидентов в выборке; исчерпывающая матрица инициатора/получателя/чужой локации не подтверждена | Частично |

## 3. Матрица: центральный офис

| ID | Шаг | Экран / HTTP | Сервис и серверные ограничения | Тестовое подтверждение | Оценка |
|---|---|---|---|---|---|
| CO-01 | Приёмка входящего отправления | GET `/central/receive`; POST `/central/receive/confirm` | `receive`; central guard, полный манифест и адресат | `test_postgresql_full_logistics_cycle.py`, `test_postgresql_transfer_integration.py` | Код + тест |
| CO-02 | Разрешение инцидента | POST `/central/receive/resolve` | `resolve_exception`; обязательное подтверждение и пояснение, затем полная приёмка | Тесты incident pages и сквозного цикла; полная POST × роли/объекты матрица не установлена | Частично |
| CO-03 | Создание/упаковка коробки | GET `/central/packing`; POST `/central/packing/new-box` | `create_box`; только карты на балансе ЦО, без активного отправления/коробки/размещения/внешнего хранения | `test_postgresql_central_custody.py`, сквозной цикл | Код + тест |
| CO-04 | Изменение состава коробки | POST `/central/packing/add-card`, `/remove-card` | `add_card_to_box`, `remove_card_from_box`; сервисная проверка принадлежности, активности, членства, custody и отправления | `test_postgresql_central_custody.py`; интеграционные проверки packing | Код + тест |
| CO-05 | Списание коробки | POST `/central/packing/write-off-box` | Общий `write_off_box`; пустая активная коробка, не в пути и не на полке | В проверенной выборке отдельное позитивное/негативное покрытие ЦО не установлено | Код |
| CO-06 | Отправка в филиал/архив | GET `/central/transfer`; POST `/central/transfer/confirm` | `create_draft`, `add_box`, `add_standalone_card`, `hand_over`; один адресат/курьер, не пустой состав, запрет недоступных карт | `test_postgresql_full_logistics_cycle.py`, `test_postgresql_central_custody.py` | Код + тест |
| CO-07 | Создание заявки в архив | GET `/requests/new`; POST `/requests/create` | `PostgreSQLArchiveWorkflow.create_requests`; только активные карты в архиве, без отправления/размещения/дубликата активной заявки | `test_postgresql_full_logistics_cycle.py`, `test_postgresql_archive_fulfillment.py` | Код + тест |
| CO-08 | Просмотр созданных заявок | Отдельный GET-экран списка заявок ЦО в просмотренных PostgreSQL routers не найден; GET `/requests/new` — экран создания заявки | Создание обслуживает `PostgreSQLArchiveWorkflow.create_requests`; архивная очередь доступна на GET `/requests` под `archive_only` | `test_postgresql_archive_fulfillment.py` проверяет жизненный цикл заявок, но не отдельный список заявок ЦО | Не найдено / вопрос UX |
| CO-09 | Внешнее хранение: передача | GET `/central/custody`; POST `/central/custody/mark` | `PostgreSQLCardCustodyWorkflow.mark_external`; только ЦО, карта на балансе ЦО, доступность и допустимый тип custody, организация обязательна | `test_postgresql_central_custody.py` проверяет типы, повторы, отказ других ролей и блокировку внутренних перемещений | Код + тест |
| CO-10 | Возврат из внешнего хранения | POST `/central/custody/return` | `return_to_central_office`; только карта в состоянии внешнего хранения; возврат восстанавливает обычную доступность | `test_postgresql_central_custody.py` | Код + тест |

## 4. Матрица: архив

| ID | Шаг | Экран / HTTP | Сервис и серверные ограничения | Тестовое подтверждение | Оценка |
|---|---|---|---|---|---|
| AR-01 | Приёмка из филиала/ЦО | GET `/receive`; POST `/receive/confirm` | `receive`; archive guard и полный манифест | `test_postgresql_full_logistics_cycle.py`, archive integration | Код + тест |
| AR-02 | Размещение коробки | GET `/placement`; POST `/placement/box` | `PostgreSQLArchiveWorkflow.place_box`; активная коробка на балансе архива, активная полка, нет текущего размещения | `test_postgresql_full_logistics_cycle.py`, `test_postgresql_archive_integration.py` | Код + тест |
| AR-03 | Размещение отдельной карты | POST `/placement/card` | `place_card`; карта на балансе архива, не в коробке/отправлении/другом размещении, активная полка | `test_postgresql_archive_integration.py` | Код + тест |
| AR-04 | Снятие коробки/карты с полки | POST `/placement/remove-box`, `/placement/remove-card` | `remove_box_from_shelf`, `remove_card_from_shelf`; закрывается запись placement, учётная локация остаётся архивом | Archive integration; сквозной цикл подтверждает дальнейшее движение после снятия | Код + тест |
| AR-05 | Внутренняя упаковка и извлечение | POST `/placement/new-internal-box`, `/placement/add-card`, `/placement/remove-card-from-box` | Общий shipment workflow; archive actor, принадлежность архиву, исключение размещения/активного отправления | Полный набор комбинаций действий и foreign objects отдельно не подтверждён | Частично |
| AR-06 | Очередь заявок ЦО | GET `/requests` | Архивная очередь заявок и их текущих статусов | `test_postgresql_archive_fulfillment.py`, archive integration | Код + тест |
| AR-07 | Отметка «не найдена» | POST `/requests/not-found` | `mark_not_found`; archive actor, статус new, карта остаётся в архиве, пояснение обязательно | `test_postgresql_archive_fulfillment.py` проверяет сценарий | Код + тест |
| AR-08 | Извлечение карты из коробки по заявке | POST `/requests/unpack-card` | `remove_card_from_box`; сервис проверяет активный цикл/состав и права архива | Сквозной логистический цикл | Код + тест |
| AR-09 | Исполнение заявок и отправка карт в ЦО | POST `/requests/fulfill` | Создание отправления archive → central_office, только отдельные карты; атомарная привязка заявок к shipment | `test_postgresql_archive_fulfillment.py`, полный цикл | Код + тест |
| AR-10 | Приёмка/разрешение инцидента | POST `/receive/resolve` | Общий workflow полного receive; обязательное пояснение | Тесты инцидентов/логистики; исчерпывающая матрица не подтверждена | Частично |

## 5. Матрица: admin и viewer

| ID | Роль | Шаг | HTTP / сервис | Тестовое подтверждение | Оценка |
|---|---|---|---|---|---|
| AD-01 | admin | Управление филиалами | HTML `/admin/branches` GET/POST edit/status; API `/api/admin/branches` GET/POST/PUT; `PostgreSQLAdminService` | `test_postgresql_admin_integration.py` покрывает деактивацию, остатки, активных сотрудников, повторную активацию и уникальность | Код + тест |
| AD-02 | admin | Управление пользователями и ролями | HTML `/admin/users` GET/POST/edit; API `/api/admin/users` GET/POST/PUT | Admin integration покрывает role/branch consistency, self-demotion, self-disable, last-admin и активацию | Код + тест |
| AD-03 | admin | Управление курьерами | HTML `/admin/couriers` GET/POST/status | Admin integration проверяет блокировку деактивации при незавершённом отправлении | Код + тест |
| AD-04 | admin | Управление секциями/полками | HTML `/admin/sections` GET/POST/edit/status | Admin integration проверяет активные размещения карт/коробок и операции CRUD | Код + тест |
| AD-05 | admin | Просмотр аудита | GET `/admin/audit` | Read-only фильтрация `admin.%`; тест фильтров и append-only событий | Код + тест |
| AD-06 | admin | Глобальные инциденты | GET `/admin/incidents`, `/admin/incidents/{shipment_id}` | Admin integration проверяет список и карточку инцидента | Код + тест |
| AD-07 | admin | Запрет доступа другим ролям | Admin HTML и API GET/POST/PUT | `test_postgresql_admin_role_matrix.py` перебирает все четыре не-admin роли и семейства мутаций | Код + тест |
| VW-01 | viewer | Панель | GET `/dashboard` | Dashboard и interface role matrix | Код + тест |
| VW-02 | viewer | Поиск | GET `/search`, `/search/results`; POST `/search` | Interface role matrix; отдельная политика видимости данных не утверждена | Частично |
| VW-03 | viewer | История | GET `/history` | `test_postgresql_history.py`; role matrix | Код + тест |
| VW-04 | viewer | Запрет операционных действий | Ролевые guards на branch/central/archive handlers | Interface role matrix проверяет доступ к страницам; полная POST-матрица для каждого операционного endpoint и чужого объекта не установлена | Частично |

## 6. Сквозные ограничения и проверенные несоответствия

### 6.1 Маршруты и манифест

Сервис `PostgreSQLShipmentWorkflow.create_draft` допускает пять пар типов локаций:
- branch → central_office;
- branch → archive;
- central_office → branch;
- central_office → archive;
- archive → central_office.

Архив → ЦО запрещает коробки и допускает отдельные карты. `add_box` и `hand_over` повторно проверяют это ограничение. Для отправки коробки проверяется непустой текущий цикл состава; карта внутри коробки не должна дублироваться как отдельная позиция. Сервис повторно проверяет локацию, активность, текущую отправку, размещение и конфликт с другим активным манифестом.

### 6.2 Приёмка и инциденты

Проверенные формы принимают `shipment_id` и boolean-подтверждение всего отправления, а не отдельные позиции. Разрешение инцидента требует непустого пояснения и завершается общим полным receive workflow. Частичный приём, частичный отказ или частичное разрешение в просмотренных handlers не обнаружены. Это описание реализации, не вывод о правильном регламенте.

### 6.3 Область видимости отправлений и персональные данные

`/shipments` и `/shipments/{shipment_id}` используют `shipment_reader`; запросы ограничиваются отправлениями, где текущая локация является отправителем или получателем. В отличие от этого, admin incident pages глобальны и показывают подробности инцидента. Это ранее зафиксированное различие доступа остаётся открытым вопросом политики персональных данных.

### 6.4 Панель и история

Dashboard для branch/central_office/archive считает данные в контексте текущей локации и связанных отправлений. Для admin/viewer используется глобальная агрегированная статистика; поток событий также глобален для этих ролей. История реализована отдельным общим endpoint; точные правила видимости должны рассматриваться совместно с решением D-06…D-09, а не как согласованная политика.

### 6.5 Отмена отправления

В модели статусов присутствует `cancelled`, однако в просмотренных основных PostgreSQL пользовательских routers не обнаружен handler отмены draft/in_transit shipment. Отмена как пользовательский процесс не подтверждена. Требуется отдельная проверка всех ссылок/шаблонов и сервисных методов, затем решение владельца продукта.

## 7. Тестовое покрытие: что подтверждено и чего не доказывает

Подтверждено чтением тестов:
- Сквозной HTTP-тест проводит карту по маршрутам филиал → архив, архив → ЦО, ЦО → архив, включая регистрацию QR, коробки, заявки, приёмку и размещение.
- Отдельные тесты проверяют доступ к страницам и навигации по пяти ролям.
- Для административных HTML/API операций есть широкая проверка запрета четырём не-admin ролям.
- Административные интеграционные тесты покрывают существенные ограничения деактивации, назначения ролей/филиалов, последнего администратора, курьеров и полок.
- Внешнее хранение покрыто позитивными и негативными сценариями, включая запрет внутренних перемещений и восстановление доступности после возврата.
- История филиала и история архива/ЦО проверяются на location scope.

Не доказано просмотренной выборкой:
- Полная матрица всех POST endpoints × все пять ролей × собственный/чужой объект × состояния объекта.
- Полное позитивное разрешение каждой роли на каждый предназначенный ей POST endpoint.
- Все конкурентные гонки по каждому workflow (помимо отдельных сценариев, например QR intake и административные изменения).
- Пользовательская отмена отправления и её допустимые состояния.
- Частичная приёмка и обработка каждого вида расхождения.
- Ручная проверка экранов на реальных рабочих местах и устройствах.
- Запуск тестов/CI в рамках настоящего аудиторского прохода.

## 8. Реестр обнаруженных пробелов для дальнейшей работы

| ID | Пробел / вопрос | Приоритет | Следующее действие |
|---|---|---|---|
| GAP-UX02-01 | Нет полной POST × 5 roles × ownership × state матрицы | Высокий | Построить endpoint inventory и сверить существующие позитивные/негативные тесты |
| GAP-UX02-02 | Нет найденного пользовательского handler отмены shipment при наличии статуса cancelled | Высокий | Проверить все вызовы/сервисы/шаблоны; запросить решение о правилах отмены |
| GAP-UX02-03 | Не подтверждены все cross-location object guards на уровне каждого endpoint | Высокий | Выборочно проследить каждый handler до service и добавить список тестовых пробелов |
| GAP-UX02-04 | Глобальная видимость dashboard/history/incidents для admin/viewer | Высокий | Вернуться к решениям D-06…D-09 и утвердить политику доступа к персональным данным |
| GAP-UX02-05 | Приёмка только полная; нет partial receive | Высокий | Согласовать допустимые расхождения, ответственность и статусы до проектирования UI |
| GAP-UX02-06 | Архивная очередь и размещение требуют перехода для извлечения карты из коробки | Средний | Зафиксировать UX-переход и проверить возможность контекстного действия после согласования процесса |
| GAP-UX02-07 | Не выполнена ручная проверка сценариев на устройствах сотрудников | Средний | Составить сценарии usability-проверки после согласования процесса |
| GAP-UX02-08 | Статическая проверка; тесты/CI не запускались в этом проходе | Средний | Выполнить отдельную проверку в разрешённой CI/локальной среде, не меняя продуктовый код |

## 9. Источники

Основные исходники:
- `src/medarchive/routers/postgresql_branch.py`
- `src/medarchive/routers/postgresql_packing.py`
- `src/medarchive/routers/postgresql_transfer.py`
- `src/medarchive/routers/postgresql_shipments.py`
- `src/medarchive/routers/postgresql_central_office.py`
- `src/medarchive/routers/postgresql_archive.py`
- `src/medarchive/routers/postgresql_admin.py`
- `src/medarchive/routers/postgresql_admin_pages.py`
- `src/medarchive/routers/postgresql_dashboard.py`
- `src/medarchive/services/postgresql_shipment_workflow.py`
- `src/medarchive/services/postgresql_archive_workflow.py`
- `src/medarchive/services/postgresql_card_intake.py`
- `src/medarchive/services/postgresql_card_custody.py`
- `src/medarchive/services/postgresql_admin_service.py`

Основные тесты перечислены в разделе 1; дополнительно см. тесты из разделов 2–5.

## 10. Статус и следующий шаг

Матрица основных пользовательских потоков пяти ролей составлена по проверенным PostgreSQL handlers/services и тестовым модулям. Она выявила конкретные пробелы, но не является полной проверкой безопасности всех endpoints.

Следующий шаг UX-02: завершить endpoint-level ACL/ownership inventory — для каждого POST/PUT/PATCH/DELETE сопоставить guard, сервисную проверку actor/location/object, тест позитивного доступа и тест отказа для каждой неразрешённой роли/чужого объекта. Отдельно проверить shipment cancellation и не заявлять о покрытии, пока не подтверждены фактические тесты.

Код приложения, тесты, миграции, workflow, SQLite-конфигурация и базовая ветка в рамках этого этапа не менялись. Целевая UX-модель и реализация остаются заблокированы до согласования бизнес-процессов.
