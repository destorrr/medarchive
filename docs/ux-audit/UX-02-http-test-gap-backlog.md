# UX-02 — backlog недостающих HTTP-тестов

**База проверки:** HEAD `b985bc6fc897b701bc95902d1d19b43978f6abd8`  
**Ветка:** `docs/ux-audit-project`  
**Тип проверки:** статическое чтение handlers и тестовых модулей; тесты не запускались.

Документ фиксирует только пробелы прямого HTTP-проверочного слоя. Сервисные и DB-тесты учитываются как отдельная опора, но не засчитываются как проверка HTTP guard, разбора формы, статуса ответа и обработки исключения в handler.

## 1. Критерии классификации

- **Есть** — найден прямой HTTP-вызов нужного route и assertion результата.
- **Частично** — HTTP-вызов есть, но покрывает только часть поведения (например, только успешный сценарий).
- **Service-only** — доменное правило проверяется вызовом workflow/service без HTTP handler.
- **Не найдено в проверенной выборке** — прямой HTTP-вызов не обнаружен в просмотренных тестовых модулях. Это не абсолютное утверждение об отсутствии теста во всём репозитории.
- **Негативный role test с UUID** — отказ проверяется, но если UUID случайный, это не доказывает отказ на существующем чужом объекте.

## 2. Сводка по запрошенным маршрутам

| Маршрут | Прямая HTTP-опора на HEAD | Что уже подтверждено | Остаток |
|---|---|---|---|
| `POST /central/packing/remove-card` | Не найдено в просмотренных тестах | Handler вызывает shipment workflow и ограничен ролью central_office | Позитивный вызов, чужие card/box, состояние коробки, role denial |
| `POST /central/packing/write-off-box` | Не найдено | Service/domain ограничения списания коробки описаны в аудитах | Пустая/непустая коробка, размещение, отправление, чужая локация, роли |
| `POST /placement/add-card` | Не найдено | Service workflow проверяет состав и владение объектами | Добавление карты в свою внутреннюю коробку, чужая карта/коробка, размещённая/зарезервированная коробка |
| `POST /placement/write-off-box` | Не найдено | Сервисные ограничения списания | Успешное списание допустимой коробки и отказ по состояниям/чужой локации |
| `POST /requests/unpack-card` | Не найдено | Аналогичное извлечение из коробки покрыто другими service/HTTP путями не полностью | HTTP-обработка успешного извлечения, размещённой коробки, чужих объектов и невалидного состава |
| `POST /requests/not-found` | Не найдено | `test_postgresql_archive_fulfillment.py` проверяет workflow-level обязательность пояснения, статус и событие | Прямой HTTP happy path, пустое пояснение, чужая/закрытая заявка, role denial |
| `POST /requests/fulfill` | Есть | `test_postgresql_archive_fulfillment.py`: HTTP отказ на непригодном наборе, успешная отправка и завершение после приёмки; `test_postgresql_full_logistics_cycle.py`: сквозной позитивный путь; archive integration проверяет viewer denial на случайном UUID | Существующий foreign request/card, неархивная роль на существующем объекте, повторное исполнение/конкуренция, invalid courier |
| `POST /transfer/confirm` | Есть | `test_postgresql_full_logistics_cycle.py` проверяет успешную передачу branch → archive; transfer integration проверяет в основном GET role/scope | Прямой отказ не-branch ролям, существующая чужая коробка, неподходящий destination/courier, повторная передача и конфликт состояния |
| `POST /receive/confirm` | Есть | Archive receipt проверяется в full logistics cycle; viewer denial есть в archive integration, но с случайным shipment UUID | Существующее отправление на другую локацию, неправильный получатель, неполный/изменённый манифест через HTTP, повторная приёмка |
| `POST /central/receive/confirm` | Есть | Central receipt проверяется в full logistics cycle и archive fulfillment | Не-ЦО роли, существующее чужое/не входящее отправление, повторная приёмка и конфликт состояния |
| `POST /receive/resolve` | Есть | `test_postgresql_incident_pages.py`: неправильный получатель (403), пустая заметка, успешное разрешение, повторное разрешение; также проверены manifest/state failures на уровне workflow | Существующий чужой объект для всех ролей, отказ central_office/admin/viewer на archive route, недопустимый статус и гонка через HTTP |
| `POST /central/receive/resolve` | Есть | Incident test проверяет успешное разрешение на стороне ЦО и отказ неправильному получателю на общем resolve path | Не-ЦО роли на этом конкретном route, пустая заметка, повторное разрешение, существующее отправление чужой локации |

## 3. Точечный backlog

### P1 — отсутствующие прямые HTTP happy paths

| ID | Endpoint | Setup и роль | Действие и ожидаемая проверка |
|---|---|---|---|
| HTTP-01 | `/central/packing/remove-card` | Central Office; активная собственная коробка с картой в открытом цикле | POST удаляет membership, карта становится доступной отдельно, история состава сохраняется, 303 |
| HTTP-02 | `/central/packing/write-off-box` | Central Office; собственная пустая активная коробка вне отправления и полки | POST списывает коробку; статус/событие/redirect соответствуют handler |
| HTTP-03 | `/placement/add-card` | Archive; собственная активная внутренняя коробка и свободная карта архива | POST добавляет карту в открытый цикл коробки; membership и событие сохранены, 303 |
| HTTP-04 | `/placement/write-off-box` | Archive; собственная пустая активная коробка вне размещения/отправления | POST списывает коробку; состояние и аудит проверены |
| HTTP-05 | `/requests/unpack-card` | Archive; карта в собственной коробке, коробка не размещена и не отправлена | POST извлекает карту; membership закрыт, история сохранена, 303 |
| HTTP-06 | `/requests/not-found` | Archive; активная заявка ЦО на карту, которую не удалось найти | POST с пояснением закрывает заявку как not_found и создаёт audit event, 303 |

### P1 — ownership и состояние объекта через HTTP

| ID | Endpoint family | Fixture | Ожидаемое поведение |
|---|---|---|---|
| HTTP-07 | Central packing remove/write-off | Существующая коробка и карта другой локации; отдельно коробка с активным shipment/placement; непустая коробка для write-off | Handler не меняет данные; отображает контролируемую ошибку; сервис/БД остаются согласованными |
| HTTP-08 | Archive add-card/unpack/write-off | Существующие чужие card/box; размещённая коробка; коробка в отправлении; непустая коробка для write-off | Отказ на реальных чужих объектах и недопустимых состояниях; никаких частичных изменений |
| HTTP-09 | Archive not-found | Существующая заявка другой карты/неактивный или уже закрытый request; повторный POST | Отказ без повторного события/изменения завершённой заявки |
| HTTP-10 | Archive fulfill | Существующая карта не в архиве, карта в коробке/на полке/в отправлении, неактивный курьер, уже исполненная заявка | HTTP handler показывает ошибку; shipment и request pointers не создаются частично |

### P1 — role denial на конкретных POST handlers

| ID | Endpoint | Роли для отказа | Примечание |
|---|---|---|---|
| HTTP-11 | Central packing remove/write-off | branch, archive, admin, viewer | Использовать существующие валидные central fixtures, чтобы отказ был именно от guard |
| HTTP-12 | Archive placement add/write-off; requests unpack/not-found/fulfill | branch, central_office, admin, viewer | Не подменять роль случайным UUID; подтверждать 403 до мутации |
| HTTP-13 | Branch transfer confirm | central_office, archive, admin, viewer | Проверить конкретный POST, а не только GET страницы передачи |
| HTTP-14 | Archive receive confirm/resolve | branch, central_office, admin, viewer | Для неразрешённых ролей использовать существующее входящее archive shipment |
| HTTP-15 | Central receive confirm/resolve | branch, archive, admin, viewer | Для неразрешённых ролей использовать существующее входящее central shipment |

### P2 — приёмка, разрешение инцидента и устойчивость

| ID | Endpoint | Сценарий | Ожидаемая проверка |
|---|---|---|---|
| HTTP-16 | Archive/Central receive confirm | Shipment существует, но адресован другой локации | 4xx/контролируемая ошибка; location и shipment status не меняются |
| HTTP-17 | Archive/Central receive confirm | Повторный receive уже принятого shipment | Повторная операция отклонена; нет второго события приёмки |
| HTTP-18 | Archive/Central receive confirm | Неверный флаг подтверждения или повреждённый/неполный manifest state | Handler не принимает отправление; все элементы остаются в транзитном состоянии |
| HTTP-19 | Archive/Central receive resolve | Пустая заметка, не-incident shipment, повторный resolve | Ошибка без изменения состояния/дублирования события |
| HTTP-20 | Archive/Central receive resolve | Существующий shipment, где actor не является получателем | 403; shipment остаётся exception |
| HTTP-21 | Branch transfer confirm | Существующая чужая коробка, неактивный курьер, неподходящий destination, коробка уже отправлена/размещена | Контролируемый отказ; shipment не создаётся либо откатывается полностью |
| HTTP-22 | Все перечисленные mutations | Два конкурентных POST на один request/box/shipment | Только один переход успешен; нет двойного membership, shipment или audit event |

## 4. Уже существующие тесты, которые нельзя дублировать без причины

- `test_postgresql_full_logistics_cycle.py::test_card_moves_branch_archive_central_archive_through_http_routes` — позитивные HTTP-пути branch transfer, archive receive, archive fulfillment, central receive, central transfer, archive receive и placement.
- `test_postgresql_archive_fulfillment.py::test_archive_request_is_fulfilled_by_unboxed_shipment_and_completed_on_central_receipt` — HTTP fulfillment, отказ по неподходящим данным и central receive; not-found в этом тесте вызывается непосредственно через workflow, не через HTTP.
- `test_postgresql_archive_integration.py::test_postgresql_archive_individual_card_shelf_placement_is_audited_and_reusable` — HTTP placement на полку, duplicate placement, remove-card с полки и повторное размещение. Это не endpoint `/placement/add-card` (добавление в коробку).
- `test_postgresql_archive_integration.py::test_postgresql_archive_pages_render_with_archive_role` — viewer denial для отдельных POST, но часть запросов использует случайные UUID.
- `test_postgresql_incident_pages.py::test_admin_incident_pages_show_manifest_and_enforce_admin_role` — HTTP resolve: неправильный получатель, пустая заметка, успешное разрешение и повторная попытка; центральный happy path.
- `test_postgresql_packing_integration.py::test_postgresql_branch_packing_registers_qr_and_preserves_membership_history` — branch packing endpoints; не доказывает наличие аналогичного HTTP coverage для central packing.
- `test_postgresql_transfer_integration.py::test_postgresql_branch_transfer_page_is_scoped_to_branch` — главным образом GET role/page checks; не заменяет POST role-denial tests.

## 5. Порядок закрытия backlog

1. Сначала HTTP-01…HTTP-06: закрыть прямые позитивные пути, которых не найдено.
2. Затем HTTP-07…HTTP-10: проверить реальные чужие объекты и запрещённые состояния.
3. Затем HTTP-11…HTTP-15: проверить role guards на конкретных POST routes.
4. После этого HTTP-16…HTTP-22: укрепить приёмку, инциденты и конкурентные переходы.

Backlog является аудитным предложением, а не разрешением реализовывать тесты. До согласования бизнес-процессов новые тесты и продуктовый код не добавляются.

## 6. Ограничения

- Проверка статическая, привязана к указанному HEAD.
- В этом проходе не запускались pytest, PostgreSQL или GitHub Actions.
- Формулировка «не найдено» означает отсутствие прямого вызова в конкретно просмотренных тестовых модулях, а не доказательство отсутствия теста во всём репозитории.
- Негативный тест с случайным UUID не считается проверкой ownership существующего чужого объекта.
