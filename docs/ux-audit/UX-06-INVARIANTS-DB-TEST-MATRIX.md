# UX-06 — матрица инвариантов, механизмов БД и проверок

**Статус:** проект технических требований; требует согласования перед реализацией.  
**Основа:** UX-06-storage-model-design.md, UX-06-TECHNICAL-DATA-MODEL.md, D-01…D-18, P-01…P-09.  
**Граница:** не изменяет приложение, ORM, миграции или тесты и не разрешает начинать реализацию.

## 1. Принципы

1. UI и прикладные проверки не заменяют ограничения PostgreSQL.
2. Локальные условия — CHECK; уникальность — UNIQUE/partial unique index; ссылки — FK.
3. Межтабличные инварианты — deferred constraint trigger либо эквивалентная проверка конечного состояния транзакции.
4. Составные операции атомарны; проверка и изменение выполняются в одной транзакции с единым порядком блокировок.
5. Нельзя доверять произвольному session GUC как доказательству разрешённой операции: прикладная DB role может попытаться выставить его самостоятельно. Нужен защищённый DB command boundary.
6. История append-only; исправление — отдельная корректирующая операция, не UPDATE старого факта.
7. Любая ошибка составной операции откатывает все её изменения.

## 2. Матрица инвариантов

| ID | Инвариант | Механизм PostgreSQL | Транзакционная граница | Негативная проверка |
|---|---|---|---|---|
| DB-01 | У карты максимум одно активное membership | Существующий partial UNIQUE по card_id при removed_at IS NULL | Изменение membership и связанных объектов | Второе активное membership той же карты отклоняется |
| DB-02 | У коробки максимум один открытый BoxCycle | Существующий partial UNIQUE по box_id при ended_at IS NULL | Создание/закрытие цикла | Второй открытый цикл отклоняется |
| DB-03 | У коробки максимум одно активное BoxPlacement | Существующий partial UNIQUE по box_id при removed_at IS NULL | Размещение/снятие | Второе активное размещение отклоняется |
| DB-04 | Активное membership ссылается на открытый цикл той же коробки; карта согласована с локацией/отправлением | Существующий deferred consistency trigger, расширить целевыми условиями | Изменение карты, коробки, цикла и membership — один commit | Membership в закрытый/чужой цикл или несовместимую локацию отклоняется на commit |
| DB-05 | Архивная карта без shipment/external custody имеет ровно одно состояние: membership ИЛИ request item extracted | Deferred trigger на cards, memberships, items и shipment pointers | Любая операция, меняющая эти связи | Карта без обоих состояний либо с обоими одновременно отклоняется |
| DB-06 | Карта не находится в двух активных заявках | Partial UNIQUE по item.card_id для активных статусов pending/extracted/in_shipment | Добавление/терминация позиции | Вторая активная заявка на карту отклоняется |
| DB-07 | Идентичность позиции и родителя неизменяема; история не удаляется | FK RESTRICT, UNIQUE по согласованной политике повторного добавления, no-delete trigger | Создание позиции/корректирующее событие | DELETE позиции или изменение request_id/card_id отклоняется |
| DB-08 | Статусы родителя и переходы ограничены | CHECK + BEFORE trigger переходов и terminal-state guard | Смена статуса | completed/cancelled нельзя вернуть в processing |
| DB-09 | Состав меняет только уполномоченная роль на соответствующей фазе | Trigger или закрытая DB procedure проверяет роль, старый статус и тип операции | Родитель и позиции заблокированы одной транзакцией | ЦО меняет состав после processing; archive редактирует submitted вместо старта; отказ |
| DB-10 | Начало обработки извлекает все карты заявки одновременно | Сервисная транзакция + row locks + deferred consistency trigger | Parent submitted→processing, все memberships, items и placements — один commit | Ошибка на последней карте оставляет заявку submitted и не извлекает ни одной карты |
| DB-11 | Если extraction делает коробку пустой, её placement закрывается в той же транзакции | Deferred trigger конечного состояния + защищённая операция extraction | Та же транзакция DB-10 | Извлечение последней карты без снятия placement отклоняется; ошибка снятия откатывает весь старт |
| DB-12 | Regroup на полке не снимает placement автоматически; пустая коробка может остаться на полке | Отдельная защищённая операция regroup, отличная от extraction | Весь набор исходных/целевых коробок и memberships — один commit | Regroup оставляет placement; прямой DML вне regroup отклоняется |
| DB-13 | Изменение membership при активном placement допускается только в разрешённых операциях | Заменить общий medarchive_guard_box_membership_mutation на контекстные правила extraction/regroup/return | Один commit соответствующей операции | Прямой INSERT/UPDATE/DELETE membership на размещённой коробке отклоняется |
| DB-14 | Возврат извлечённой карты создаёт новое membership, исходное сохраняется | FK на исходное/возвратное membership, append-only, deferred consistency trigger | Item, карта, коробка, цикл, membership и событие — один commit | removed_by_archive без возврата карты отклоняется |
| DB-15 | removed_by_archive требует actor/time/comment; not_found — actor/time, комментарий необязателен; parent cancellation — actor/time/reason | CHECK непустых обязательных полей + trigger неизменяемости терминальных метаданных | Фиксация результата позиции/родителя | Для not_found отсутствие комментария допустимо; отсутствующий actor/time, пустой обязательный комментарий removed_by_archive или изменение причины отклоняются |
| DB-16 | При отмене все незавершённые позиции становятся cancelled; not_found и removed_by_archive сохраняются | Закрытая cancel procedure + deferred consistency trigger | Возврат всех extracted, статусы items, parent и событие — один commit | Отмена с оставшейся extracted/pending позицией отклоняется |
| DB-17 | После handover отмена заявки запрещена | Trigger проверяет handed_over_at/status shipment; parent row lock сериализует cancel и handover | Cancel и handover конкурируют за блокировку родителя | Конкурентный тест: фиксируется ровно один исход; после handover cancel отклонён |
| DB-18 | У родителя максимум один shipment, маршрут archive→central_office | UNIQUE FK выбранной связи + route guard + deferred trigger | Создание shipment, manifest и связи с parent | Второй shipment или неверный маршрут отклоняется |
| DB-19 | Manifest содержит все и только отправляемые позиции | Deferred trigger сравнивает shipment_cards с extracted items родителя | Создание полного manifest и handover | Пропущенная карта, посторонняя карта или второй manifest отклоняются |
| DB-20 | Handover одновременно переводит все отправляемые items extracted→in_shipment и карты в shipment | Существующие shipment pointers/manifest guards + deferred trigger item/request/shipment | Shipment, manifest, pointers, items и событие — один commit | Ошибка на одной карте не оставляет остальные in_shipment |
| DB-21 | Приёмка только полная; локация меняется по полному manifest | Существующие shipment receipt guards + deferred связь с request items | Одна транзакция receipt | Частичная приёмка и прямое перемещение карты отклоняются |
| DB-22 | Parent completed только при терминальных items и received shipment, если он создан | Deferred trigger на parent/items/shipment | Финальная приёмка или завершение без отправления | completed при pending/extracted/in_shipment или неполученном shipment отклоняется |
| DB-23 | not_found — окончательный результат | Trigger переходов статусов и append-only метаданные | Фиксация item result | not_found→pending/extracted/in_shipment отклоняется |
| DB-24 | CardPlacement не используется для новых операций; старые записи остаются историей | После миграционного периода запрет INSERT/изменения, таблица read-only | Отдельный этап после preflight/backfill | Новая запись/изменение старой отклоняется, чтение разрешено по scope |
| DB-25 | Card, Box и события нельзя физически удалить/переписать | Существующие no-delete/append-only guards и FK RESTRICT | Все операции жизненного цикла | DELETE карты/коробки/event отклоняется |
| DB-26 | Локация меняется только через shipment workflow | Существующие object movement guards, composite FK, deferred shipment consistency | Handover/receipt | Прямое изменение current_location/current_shipment без shipment отклоняется |
| DB-27 | Маршрут и состав отправления соответствуют D-01/D-18 | Существующий route guard + DB trigger + server validation | Создание shipment и manifest | Неверный маршрут или запрещённый тип состава отклоняется |
| DB-28 | Admin correction требует причины и не переписывает историю | Отдельная привилегированная procedure, actor/reason, append-only audit event | Коррекция и событие — один commit | Коррекция без причины/события или обычной ролью отклоняется |

## 3. Два разных сценария коробки на полке

### 3.1. Извлечение по заявке (P-06)

Предусловия: заявка submitted; actor archive; все карты активны, в архиве, имеют по одному membership; нет конфликтующей заявки/shipment.

Атомарный результат:
- все memberships закрыты;
- все позиции переходят в extracted;
- parent становится processing;
- коробки, из которых извлечены все карты, автоматически теряют активное placement;
- непустые коробки сохраняют placement;
- фиксируется событие с автором, временем и составом.

Ошибка любого шага откатывает всю операцию.

### 3.2. Перегруппировка (P-03 / D-11)

Предусловия: карты не участвуют в активной заявке/shipment; исходные и целевые коробки допустимы.

Атомарный результат:
- исходные memberships закрыты;
- новые циклы/memberships созданы;
- placement автоматически не снимается;
- коробка, ставшая пустой именно из-за regroup, может остаться на полке;
- UI показывает предупреждение, дальнейшее решение принимает пользователь.

### 3.3. Безопасное различение контекста

Нельзя полагаться только на SET app.operation='regroup': SQL-клиент под той же ролью может выставить параметр самостоятельно. Возможные варианты:
1. Закрытые DB procedures для start/cancel/remove/regroup/handover/receipt; прикладной DB role получает EXECUTE, прямой DML на критичные таблицы отзывается.
2. Отдельная защищённая схема/роль с SECURITY DEFINER, проверкой current_user и фиксированным search_path.
3. Если DML остаётся в SQLAlchemy, обеспечить эквивалентную защиту от прямого обхода через DB grants/role separation. Иначе session-параметр не является границей безопасности.

Рекомендация: целевой контракт — закрытый набор доменных команд, а не свободный CRUD. Выбор способа зависит от фактических DB grants и стратегии подключения приложения.

## 4. Порядок блокировок и конкурентность

Операции с заявкой:
1. Parent request — FOR UPDATE.
2. Cards по UUID в стабильном порядке.
3. Boxes по UUID в стабильном порядке.
4. BoxCycles по UUID.
5. Memberships и placements по PK.
6. Shipment и manifest rows.
7. Append-only events.

Regroup без заявки: cards → boxes → cycles → memberships/placements → event. Уникальные индексы — последняя защита от гонки; предварительный SELECT без блокировки не гарантирует отсутствие конфликта.

Обязательные конкурентные тесты с независимыми DB connections:
- два параллельных старта одной заявки: один успех, второй без повторного извлечения;
- cancel против handover: ровно один исход;
- две заявки на одну карту: одна успешна;
- две упаковки одной карты: одно membership;
- параллельный regroup одной карты в разные коробки: одно итоговое membership;
- повторная приёмка: второй вызов отклонён;
- размещение одной коробки на двух полках: один успех.

## 5. Каталог негативных тестов

### Хранение и коробки
- Дублирующее активное membership.
- Membership в закрытый или чужой цикл.
- Несовпадение локации карты и коробки.
- Два активных placement одной коробки.
- Обычное размещение пустой коробки.
- Extraction последней карты без закрытия placement.
- Regroup на полке с сохранением placement, в том числе для пустой коробки.
- Прямой DML membership при placement вне разрешённой операции.
- Упаковка карты с активной заявкой/shipment.
- Списание коробки с картами, placement или shipment.

### Заявки
- Одна карта в двух активных заявках.
- ЦО меняет состав после processing.
- Archive меняет submitted без начала обработки.
- Начало обработки не ролью archive.
- Ошибка на одной карте при batch extraction — полный rollback.
- Исключение без комментария.
- not_found без actor/time и попытка повторного открытия.
- Отмена без причины.
- Отмена processing без возврата всех extracted.
- Отмена после handover.
- completed при незавершённой позиции.
- Удаление позиции или изменение её card_id/request_id.

### Отправления
- Два fulfillment shipment на одного parent.
- Неверный маршрут.
- Handover при незавершённой проверке позиций.
- Handover не всех extracted карт.
- Посторонняя карта в manifest.
- Повторный handover.
- Частичная приёмка.
- Приёмка с несовпадающим manifest.
- Прямое изменение current_location/current_shipment.
- Parent completed до receipt.

### Роли и аудит
- branch/central_office выполняет архивную операцию.
- archive редактирует submitted вместо start-processing.
- Неучастник читает manifest.
- Admin correction без причины/audit event.
- Изменение/удаление старого DomainEvent, membership или placement.

Для deferred triggers отдельно проверить ошибку на COMMIT, а не только на flush. Тесты обхода должны выполнять прямой SQL под реальной прикладной DB role.

## 6. Preflight и backfill проверки

До включения ограничений сохранить отчёт:
- карты по current_location/current_shipment;
- активные memberships, CardPlacements и BoxPlacements;
- пересечения CardPlacement и BoxCardMembership;
- архивные карты без membership/extracted item/external custody;
- несколько активных связей одной карты;
- пустые коробки с активным placement;
- открытые циклы без membership и memberships в закрытых циклах;
- старые ArchiveRequest по статусам и связям с shipment/handover;
- manifest shipment_cards против состава заявок;
- отсутствующие actor IDs и некорректные временные последовательности.

Backfill должен быть идемпотентным, пакетным, с контрольными точками, не удалять старые строки и выдавать список конфликтов для ручного разрешения. Нельзя автоматически угадывать местоположение карты.

## 7. Очерёдность реализации после отдельного разрешения

1. P0 — preflight SQL и baseline данных.
2. P1 — parent/items, backfill и ограничения.
3. P2 — инвариант архивного хранения, защищённые extraction/return/regroup, CardPlacement read-only.
4. P3 — start/cancel/remove и единый shipment/handover/receipt.
5. P4 — серверные права, API/UI, счётчики и контекстная история.
6. P5 — unit/integration, прямой SQL, конкурентность, миграция на копии production-like данных, rollback/restore.

Нельзя начинать с UI или заменять тесты до утверждения DB-контракта.

## 8. Технические вопросы для следующего аудита

1. Какие DB roles/grants используются приложением и миграциями? Это определяет безопасный механизм закрытых workflow-команд.
2. Как устроены SQLAlchemy sessions/transactions в archive/shipment workflows? Нужно проверить реальный UoW и порядок блокировок.
3. Как формируется actor identity для DB audit, чтобы trigger проверял полномочия без доверия подделываемому параметру?
4. Достаточно ли DomainEvent с aggregate_type/aggregate_id и типизированным payload для контекстной истории, или нужна отдельная RequestEvent таблица?

Это технические вопросы; согласованные бизнес-правила повторно не открываются.

## 9. Границы достоверности

Матрица сопоставлена со статически прочитанными ORM и миграциями: partial unique indexes, shipment composite FKs, deferred shipment consistency guards, archive request guard, box membership mutation guard, placement guard, append-only DomainEvent и object movement guards. Это целевой контракт, а не утверждение, что он уже реализован. PostgreSQL, pytest, Alembic и CI не запускались.

| DB-29 | При атомарном старте все позиции P-09 получают `extracted` как автоматический результат «Найдена, готова к передаче»; архив отдельно и независимо фиксирует только исключения `not_found`/`removed_by_archive` | Start-processing transaction + закрытые item-level procedures для `request_remove_item` и `request_mark_not_found`, row locks и append-only DomainEvent | Старт атомарно извлекает весь состав и задаёт `extracted`; каждая команда исключения меняет одну позицию и её событие в отдельной транзакции; parent остаётся `processing` | После старта все позиции отображаются как готовые к передаче без отдельного подтверждения «Найдена»; исключение одной позиции сразу видно; сбой другой команды не откатывает ранее committed exception; пакетное сохранение отсутствует |
| DB-30 | Обработка P-09 не закрепляется за сотрудником, который начал её; продолжить может любой активный сотрудник архива с доступом к заявке | Authorization guards для start-processing и item outcome commands; actor attribution в DomainEvent и item resolution fields | Проверка роли, активности и доступа к конкретной P-09 выполняется для фактического actor каждой команды; `processing_started_by` не используется как owner/ACL; `resolved_by`/DomainEvent actor отражает исполнителя конкретного действия | Второй активный сотрудник архива с доступом может зафиксировать исключение незавершённой позиции; сотрудник без доступа или деактивированный пользователь получает отказ; история сохраняет фактического исполнителя каждого действия |
| DB-31 | Ошибочный `not_found` или `removed_by_archive` можно исправить на `extracted` до создания fulfillment-черновика; физическое повторное извлечение и изменение результата атомарны, исходное событие неизменно | Закрытая процедура `request_restore_item_to_extracted`, блокировки parent/item/card/membership/box, append-only DomainEvent, guard `fulfillment_shipment_id IS NULL` | Карта обнаружена в архивной коробке; membership закрыто, размещение коробки согласовано, item возвращён в `extracted`, событие хранит прежний/новый результат, actor/time и ссылку на membership в одной транзакции. Команда сериализуется с созданием fulfillment-черновика и отменой parent | Оба перехода (`not_found → extracted`, `removed_by_archive → extracted`) успешны до создания fulfillment-черновика при фактическом наличии карты в коробке; исходное событие/исходный комментарий остаются; отдельный комментарий исправления не требуется; при наличии fulfillment-ссылки, отсутствии карты, неактивном membership, недостаточных правах или конкурентном создании fulfillment первым команда отклоняется без частичных изменений |
