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
| DB-05 | Карта в обычном физическом хранении архива без shipment/external custody имеет membership либо активную `extracted` позицию у processing P-09; терминальный `not_found` допустим только как сохранённый outcome завершённой/отменённой P-09 без membership/shipment и не считается активной позицией | Deferred trigger на cards, memberships, items и shipment pointers | Любая операция, меняющая эти связи | Карта без допустимого состояния либо с несовместимыми состояниями отклоняется |
| DB-06 | Карта зарезервирована максимум за одной незавершённой P-09 независимо от результата позиции; `removed_by_archive` и `not_found` не освобождают резервирование, пока parent не `completed`/`cancelled` | Partial UNIQUE по `item.card_id WHERE reservation_active = true`; защищённые команды поддерживают признак, deferred trigger сверяет его с parent status и draft tombstone | Создание/редактирование draft, фиксация item outcome, отмена/завершение parent | Вторая P-09 на ту же карту отклоняется даже при `removed_by_archive`/`not_found`; резервирование снимается атомарно только при draft tombstone или terminal parent |
| DB-07 | Идентичность позиции и родителя неизменяема; история не удаляется | FK RESTRICT, UNIQUE по согласованной политике повторного добавления, no-delete trigger | Создание позиции/корректирующее событие | DELETE позиции или изменение request_id/card_id отклоняется |
| DB-08 | Статусы родителя и переходы ограничены | CHECK + BEFORE trigger переходов и terminal-state guard | Смена статуса | completed/cancelled нельзя вернуть в processing |
| DB-09 | Состав меняет только уполномоченная роль на соответствующей фазе | Trigger или закрытая DB procedure проверяет роль, старый статус и тип операции | Родитель и позиции заблокированы одной транзакцией | ЦО меняет состав после processing; archive редактирует submitted вместо старта; отказ |
| DB-10 | Начало обработки извлекает все карты заявки одновременно | Сервисная транзакция + row locks + deferred consistency trigger | Parent submitted→processing, все memberships, items и placements — один commit | Ошибка на последней карте оставляет заявку submitted и не извлекает ни одной карты |
| DB-11 | Если extraction делает коробку пустой, её placement закрывается в той же транзакции | Deferred trigger конечного состояния + защищённая операция extraction | Та же транзакция DB-10 | Извлечение последней карты без снятия placement отклоняется; ошибка снятия откатывает весь старт |
| DB-12 | Regroup на полке не снимает placement автоматически; пустая коробка может остаться на полке | Отдельная защищённая операция regroup, отличная от extraction | Весь набор исходных/целевых коробок и memberships — один commit | Regroup оставляет placement; прямой DML вне regroup отклоняется |
| DB-13 | Изменение membership при активном placement допускается только в разрешённых операциях | Заменить общий medarchive_guard_box_membership_mutation на контекстные правила extraction/regroup/return | Один commit соответствующей операции | Прямой INSERT/UPDATE/DELETE membership на размещённой коробке отклоняется |
| DB-14 | Возврат извлечённой карты создаёт новое membership, исходное сохраняется | FK на исходное/возвратное membership, append-only, deferred consistency trigger | Item, карта, коробка, цикл, membership и событие — один commit | removed_by_archive без возврата карты отклоняется |
| DB-15 | Первичная фиксация `removed_by_archive` требует actor/time/comment; повторная фиксация после исправления требует actor/time, новый комментарий необязателен; `not_found` — actor/time, комментарий необязателен; parent cancellation — actor/time/reason | CHECK/command guard с учётом предыдущего результата + trigger неизменяемости истории | Фиксация результата позиции/родителя | При первой фиксации `removed_by_archive` отсутствие комментария отклоняется; при повторной фиксации после исправления отсутствие нового комментария допустимо; отсутствие actor/time или изменение причины отмены отклоняется |
| DB-16 | При отмене все незавершённые позиции становятся cancelled; not_found и removed_by_archive сохраняются | Закрытая cancel procedure + deferred consistency trigger | Возврат всех extracted, статусы items, parent и событие — один commit | Отмена с оставшейся extracted/pending позицией отклоняется |
| DB-17 | После handover отмена заявки запрещена | Trigger проверяет handed_over_at/status shipment; parent row lock сериализует cancel и handover | Cancel и handover конкурируют за блокировку родителя | Конкурентный тест: фиксируется ровно один исход; после handover cancel отклонён |
| DB-18 | У родителя максимум один shipment, маршрут archive→central_office | UNIQUE FK выбранной связи + route guard + deferred trigger | Создание shipment, manifest и связи с parent | Второй shipment или неверный маршрут отклоняется |
| DB-19 | Черновой manifest fulfillment содержит все и только карты со статусом extracted по P-09; после создания неизменяем | Deferred trigger сравнивает shipment_cards с extracted items родителя | Создание draft shipment, manifest snapshot и parent link — один commit | Пропущенная карта, посторонняя карта, изменение manifest или второй shipment отклоняются |
| DB-20 | Только отдельное подтверждение фактической передачи переводит весь draft manifest в shipment custody | Существующие shipment pointers/manifest guards + deferred trigger item/request/shipment | shipment draft→in_transit, все card pointers, items extracted→in_shipment и событие — один commit | Ошибка на одной карте не оставляет остальные in_shipment; создание draft само по себе не меняет custody |
| DB-21 | Приёмка любого shipment только полная; локация меняется атомарно по полному неизменяемому manifest. Для fulfillment archive → CO сотрудник подтверждает получение всего отправления без поштучной сверки карт | Shipment receipt guards + deferred связь с request items; actor/location/status guards | Одна транзакция receipt | Частичная приёмка и прямое перемещение карты отклоняются; отсутствие поштучной сверки не меняет manifest и не создаёт отдельные item outcomes |
| DB-22 | Parent completed только при терминальных items и received shipment, если он создан | Deferred trigger на parent/items/shipment | Финальная приёмка или завершение без отправления | completed при pending/extracted/in_shipment или неполученном shipment отклоняется |
| DB-23 | `not_found` можно исправлять до создания fulfillment-черновика; повторные циклы `not_found ↔ extracted` разрешены при фактическом подтверждении физического состояния | Trigger переходов статусов и append-only метаданные | Каждая фиксация/корректировка — отдельная транзакция | `not_found → extracted` без обнаружения карты в коробке отклоняется; после создания fulfillment-черновика исправление запрещено |
| DB-24 | CardPlacement не используется для новых операций; старые записи остаются историей | После миграционного периода запрет INSERT/изменения, таблица read-only | Отдельный этап после preflight/backfill | Новая запись/изменение старой отклоняется, чтение разрешено по scope |
| DB-25 | Card, Box и события нельзя физически удалить/переписать | Существующие no-delete/append-only guards и FK RESTRICT | Все операции жизненного цикла | DELETE карты/коробки/event отклоняется |
| DB-26 | Локация меняется только через shipment workflow | Существующие object movement guards, composite FK, deferred shipment consistency | Handover/receipt | Прямое изменение current_location/current_shipment без shipment отклоняется |
| DB-27 | Маршрут и состав отправления соответствуют D-01/D-18 | Существующий route guard + DB trigger + server validation | Создание shipment и manifest | Неверный маршрут или запрещённый тип состава отклоняется |
| DB-28 | Admin correction требует причины и не переписывает историю | Отдельная привилегированная procedure, actor/reason, append-only audit event | Коррекция и событие — один commit | Коррекция без причины/события или обычной ролью отклоняется |
| DB-33 | Авторство черновика можно переназначить только admin, только при деактивации текущего автора и до handover; новый автор активен и принадлежит той же отправляющей локации | Закрытая admin procedure, FK на user, проверка active/role/location, append-only event; запрет UPDATE исходного create event | Shipment row lock + проверка текущего автора + смена author pointer + audit event — один commit | Обычный пользователь, активный исходный автор, неактивный/чужой новый автор или shipment после handover — отказ; событие содержит admin/old/new/time; manifest и courier snapshot неизменны |

| DB-32 | Каждое повторное исправление результата P-09 — отдельное append-only событие; прежние результаты/комментарии неизменяемы | Append-only DomainEvent + запрет UPDATE/DELETE истории | Событие и изменение item/placement — один commit | Попытка изменить/удалить старое событие отклоняется; повтор с тем же idempotency key не создаёт новое событие |

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
- размещение одной коробки на двух полках: один успех;
- деактивация/отзыв role-location membership против P-09 item command, редактирования draft и обычного/fulfillment handover: исход определяется блокировкой principal/membership, устаревшие полномочия не используются;
- деактивация текущего автора против shipment_reassign_draft_author: переназначение возможно только после commit деактивации; если handover завершился первым, переназначение запрещено по состоянию shipment;
- конкурентный replay успешной команды после деактивации: мутация не повторяется, чувствительный результат не раскрывается без актуального read permission.

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
- Одна карта в двух активных заявках, включая гонку двух параллельных созданий.
- Создание новой P-09 на карту с `removed_by_archive` при ещё `processing` родителе.
- Создание новой P-09 на карту с `not_found` при ещё `processing` родителе.
- Снятие резервирования до `completed`/`cancelled` либо сохранение резервирования после terminal parent.
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
- Переназначение автора активного черновика, не admin, неактивному/чужому сотруднику или после handover; проверка append-only события и неизменности manifest/courier snapshot.
- Бизнес-команда после уже завершившейся деактивации actor или отзыва его role/location membership.
- Конкурентная деактивация/отзыв membership против item outcome, cancel, draft edit, courier change и handover; проверка единого порядка principal/membership locks.
- Команда handover от автора, деактивация которого завершилась до получения блокировки; отказ без изменения shipment.
- Admin reassignment до commit деактивации текущего автора; отказ, без изменения author pointer.

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
| DB-31 | Исправления `not_found`/`removed_by_archive → extracted` и повторная фиксация `extracted → not_found` разрешены до fulfillment-черновика; физические действия и результат атомарны | `request_restore_item_to_extracted` и `request_mark_not_found`; блокировки parent/item/card/membership/box; append-only DomainEvent; guard `fulfillment_shipment_id IS NULL` | `not_found → extracted`: обнаружить карту в коробке и закрыть membership; `extracted → not_found`: карта не обнаружена, membership не создаётся; событие хранит прежний/новый результат, actor/time | Циклы `not_found → extracted → not_found` и `removed_by_archive → extracted` успешны при фактическом соответствии физического состояния; отсутствие карты при `→ extracted`, наличие fulfillment-ссылки, недостаточные права или конкурентное создание fulfillment первым отклоняют команду без частичных изменений |



## DB-34…DB-38 — обычные отправления

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Негативный тест |
|---|---|---|---|---|
| DB-34 | Обычный shipment создаётся только в `draft`, с непустым коробочным manifest и активным курьером; у автора не более одного незавершённого draft | UNIQUE/partial index + закрытая command procedure + FK/active/scope checks | Создание shipment, manifest, courier FK/snapshot и create event — один commit | Пустой manifest, дубли коробок, чужая/занятая/пустая коробка, неактивный курьер или второй draft отклоняются |
| DB-35 | Manifest и courier можно менять только текущему автору и только в `draft` | Trigger/command allowlist проверяет actor, status и scope | Manifest/courier mutation и событие/результат — атомарно | Изменение другим сотрудником или после handover отклоняется; snapshot курьера соответствует FK |
| DB-36 | Handover обычного shipment выполняет только текущий активный автор; один раз | Закрытая procedure, row lock, active/role/location guard, unique handover event | Shipment, коробки/карты custody, snapshot manifest и handover event — один commit | Чужой/deactivated actor, второй handover, невалидный manifest или отсутствие courier отклоняются |
| DB-37 | Receipt обычного входящего shipment выполняет активный сотрудник archive; только полная приёмка | Route/scope/status guard, row lock, unique receipt event, deferred custody consistency | Весь shipment, коробки/карты custody, receipt snapshot/event — один commit | Частичная/повторная приёмка, неверная локация или shipment не `in_transit` отклоняются |
| DB-38 | После handover manifest/courier immutable; после receipt custody принадлежит archive; отмена только в draft автором | State transition trigger + custody guard + author guard | Cancel/handover/receipt сериализуются на shipment row | Изменение после передачи, отмена после передачи или повторная смена custody отклоняются |


| DB-39 | В обычном коробочном shipment карты перемещаются только как содержимое коробок; manifest состоит из `ShipmentBox`, memberships/BoxCycle сохраняются, `ShipmentCard` и P-09 item для них не создаются | Закрытые процедуры shipment handover/receipt + deferred consistency trigger по составу BoxCycle | Handover/receipt всего shipment и всех карт внутри коробок — один commit | Добавление отдельной карты в обычный manifest, изменение membership при handover либо приёмка только части содержимого отклоняются |
| DB-40 | В пути custody коробки и её содержимого принадлежит назначенному курьеру; фактическое местоположение не отслеживается; destination location фиксируется только при полной приёмке | Shipment status, courier FK/snapshot и защищённые location/custody guards | Handover или receipt — один commit | Прямое присвоение destination до receipt, изменение курьера после handover или самостоятельное изменение custody карты внутри коробки отклоняется |
| DB-41 | При receipt обычного коробочного shipment BoxCycle/memberships сохраняются; полочное размещение не создаётся автоматически | Deferred consistency trigger + отдельная команда box placement | Receipt не включает независимую операцию shelf placement | Receipt не должен закрывать цикл, менять членства или создавать placement без отдельного shelf assignment |
| DB-42 | Терминальный `not_found` item может сохраняться у завершённой/отменённой P-09 без membership и shipment; это не активная позиция и не физическое размещение карты | Request/item history + deferred card-state guard; активность заявки определяется отдельно от терминального результата item | Завершение/отмена P-09 сохраняет item outcome и карточный учётный статус согласованно | Удаление `not_found` истории, создание фиктивного membership или трактовка результата как активной обработки отклоняются |

## DB-43 — резервирование карты на весь срок незавершённой P-09

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-43 | Каждая карта из актуального состава удерживается ровно одной P-09 до `completed`/`cancelled` родителя. Item outcome `removed_by_archive` или `not_found` не освобождает карту, пока родитель `processing` | `reservation_active` на item; partial UNIQUE по `card_id WHERE reservation_active`; закрытые процедуры и deferred trigger согласованности с parent/tombstone | Создание/редактирование draft и terminal transition parent выполняются с блокировкой parent/card/item; reservation flag и события меняются в одной транзакции | Вторая заявка на карту с любым item outcome незавершённой P-09 отклоняется; параллельное создание даёт ровно одного победителя; draft tombstone и terminal parent освобождают резервирование атомарно; рассогласованный flag отклоняется |

## DB-44 — полная приёмка fulfillment archive → central_office без поштучной сверки

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Проверка |
|---|---|---|---|---|
| DB-44 | Активный сотрудник ЦО с полномочиями подтверждает получение всего fulfillment-отправления целиком; сверка каждой карты с манифестом не выполняется. Частичная приёмка запрещена | Route/status/recipient-scope guard, row lock, unique receipt event, deferred consistency между shipment, картами и P-09 items | Shipment `in_transit → received`, все карты переходят в локацию ЦО, все позиции `in_shipment → completed`, receipt actor/time и завершение parent (если условие выполнено) — одна транзакция | Частичная приёмка отклоняется; повторное подтверждение не создаёт событие; receipt не требует item-by-item confirmations, не меняет состав manifest и не создаёт повторный snapshot; сотрудник не из целевой локации/без полномочий получает отказ |

## DB-45 — единый append-only источник событий и привязка истории P-09

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Проверка |
|---|---|---|---|---|
| DB-45 | Все бизнес-факты UX-06 сохраняются в `DomainEvent`; отдельный authoritative `RequestEvent` не создаётся. События P-09 и связанного fulfillment shipment имеют корректный `request_id`; event и изменение агрегата атомарны | Event type/version allowlist, FK/aggregate references, запрет UPDATE/DELETE, защищённые DB commands; проверка допустимости `request_id` для shipment archive → CO | Событие записывается в той же транзакции, что и изменение aggregate; связанные shipment events сохраняют собственный aggregate | Отсутствующее/чужое parent reference, неизвестный event type/version, изменение или удаление события отклоняются; повтор команды не создаёт дубль; проекция P-09 не раскрывает независимую историю карты или admin/security audit |


## DB-46 — сериализация полномочий с деактивацией и отзывом membership

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Проверка |
|---|---|---|---|---|
| DB-46 | Ни одна бизнес-команда не использует устаревшие active/role/location полномочия. Проверка actor, текущего автора черновика и целевого membership сериализуется с admin deactivation и изменением membership | Защищённая команда блокирует principal и используемые role/location membership строки FOR UPDATE в стабильном порядке UUID, затем повторно проверяет active/role/location; admin deactivation/revoke использует те же блокировки | Блокировки полномочий, агрегатные изменения, DomainEvent/admin audit и command ledger входят в одну транзакцию | Если бизнес-команда получила блокировку первой, она может завершиться до деактивации; если деактивация/отзыв commit-ились первыми, команда получает forbidden. Admin reassignment допустим только после фактической деактивации автора. |


## DB-47 — независимая авторизация чтения связанных объектов

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-47 | Доступ к shipment, его manifest и timeline не наследуется самостоятельными карточками/историей связанных карт и коробок. Каждая карточка, timeline и вложенный объект проверяются отдельно по актуальным роли, локации, custody, участию и состоянию | Единая серверная authorization policy для read endpoints; scoped queries до загрузки/сериализации PII; deny-by-default; read grants не должны позволять обходить policy прямым API/SQL-путём | Любое открытие по ID, переход из manifest/P-09/timeline, поиск, autocomplete, pagination, export и replay сохранённого command result | Участник shipment читает его полный manifest и timeline до/после receipt, но не получает только из этого права standalone card/box/timeline; branch после передачи карты в архив не читает её дальнейшую историю; неучастник не читает manifest; после receipt получатель открывает карту/коробку в своей текущей области; неизвестный и недоступный ID не раскрывают чувствительные данные |
| DB-47a | P-09 timeline и состав заявки не дают доступ к независимой истории/карточкам карт; доступ к P-09 после submitted ограничен сотрудниками той же локации ЦО, а до submitted — автором draft | Request-scoped projection по allowlist DomainEvent с проверкой location/author/status; отсутствие grants на независимые aggregate events по одному request_id | Очередь P-09, карточка, состав, timeline, переход по item/card link | Автор draft видит свой draft; другой сотрудник/локация ЦО не видит его до submitted; после submitted сотрудники той же локации читают P-09 и её timeline, но не получают самостоятельную карточку/историю карты без отдельного разрешения; независимые card/admin events не появляются в timeline |


## DB-48 — минимизация PII на очередях и read-side projections

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-48 | Очереди, реестры, counters и in-app notifications не раскрывают patient PII; подробные данные доступны только в разрешённой карточке заявки/route-specific manifest. Поля строк очередей соответствуют UX-04/UX-06 §47 | Явные role-specific field projections; scoped query до pagination/count; notification payload allowlist; запрет wholesale serialization ORM/event payload | Branch shipment registry; CO/archive shipment queues; CO/archive P-09 queues; общий archive P-09 counter; in-app notification; search/autocomplete; pagination/filters/sort/export | В каждой строке очереди отсутствуют patient name/card number; archive P-09 row содержит только согласованный инициаторский ФИО; counter содержит только число submitted-заявок и не уменьшается при открытии; notification не содержит patient PII и не отправляется во внешние каналы; search/snippets/counts не раскрывают недоступные объекты; экспорт не существует без отдельного согласования и при будущем появлении наследует тот же projection/scope; разрешённый manifest/detail показывает только route-specific поля |


## DB-49 — состав самостоятельной карточки медицинской карты и отсутствие адреса

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-49 | Самостоятельная карточка карты содержит номер, ФИО пациента, дату рождения, телефон при наличии, текущий статус, учётную локацию, текущую коробку/логистический цикл при наличии и доступную пользователю историю движения. Адрес пациента не поступает в систему и отсутствует во всех read/write surfaces | Явная схема полей карточки; optional phone; запрет address в DTO/schema/index/search/export/log/notification; авторизация до выдачи patient PII | Card detail API/UI, история, поиск/autocomplete, манифесты, очереди, notifications, logs и будущие согласованные экспорты | Разрешённый пользователь видит утверждённые поля; пустой телефон не создаёт ошибку/фиктивное значение; пользователь без самостоятельного card access не получает DOB/phone через shipment/P-09; address отсутствует в модели входных данных, API, индексах и выдачах; D-08 и custody продолжают ограничивать историю |


## DB-50 — состав текущего логистического цикла в карточке коробки

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-50 | Самостоятельная карточка физической коробки показывает состав её текущего BoxCycle: номер медицинской карты и ФИО пациента для каждой карты. Состав не смешивает текущий и предыдущие циклы; доступ к коробке/циклу не предоставляет самостоятельное право на карточку или историю каждой карты | Отдельная авторизация Box и текущего BoxCycle; scoped query по текущему cycle ID; явная проекция только номера карты и ФИО; запрет наследования grants от shipment и ссылок на объекты | Box detail, current-cycle composition, переходы из shipment manifest, самостоятельные card detail/timeline | Уполномоченный пользователь видит карты текущего цикла; карты прошлых циклов не попадают в текущий состав; участник обычного shipment без самостоятельного box access не получает состав; пользователь с box access не получает автоматически самостоятельную карточку/историю карт; обычный branch/CO → archive shipment manifest остаётся списком коробок без patient PII |

## DB-51 — история предыдущих логистических циклов физической коробки

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-51 | Самостоятельная карточка физической коробки перечисляет её предыдущие BoxCycle; открытие конкретного цикла показывает только его исторический состав (номер карты и ФИО) и события этого цикла. Повторное заполнение создаёт новый цикл и не изменяет прошлые циклы | Неизменяемый идентификатор BoxCycle; исторические состав и события запрашиваются с обязательным cycle scope; отдельная серверная авторизация коробки/конкретного цикла до загрузки и сериализации PII; deny-by-default | Box detail, список предыдущих циклов, открытие старого цикла, historical composition, cycle timeline, переходы из shipment | Уполномоченный пользователь видит доступные ему циклы; состав и события старого цикла не меняются после повторного заполнения; при открытии одного цикла не смешиваются карты/события другого; знание ID или ссылки, участие в shipment и доступ к его манифесту не дают права на старый цикл; доступ к циклу не даёт самостоятельного доступа к карточкам/истории карт; обычный branch/CO → archive манифест не раскрывает содержимое коробок |
