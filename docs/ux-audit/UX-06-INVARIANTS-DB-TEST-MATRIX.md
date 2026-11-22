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
| DB-16 | При полной отмене P-09 до любой выдачи все незавершённые позиции становятся cancelled; not_found и removed_by_archive сохраняются. После частичной выдачи применяется DB-17 только к невыданному остатку | Закрытая cancel procedure + deferred consistency trigger | Возврат невыданных extracted, статусы items, parent и событие — один commit | Отмена с оставшейся невыданной extracted/pending позицией отклоняется; уже выданные позиции не отменяются и не затрагиваются |
| DB-17 | После частичной выдачи P-09 отменяются только невыданные позиции; выданные продолжают lifecycle до фактического receipt ЦО | Parent/item guards и блокировки позиций; custody guard не позволяет отменить выданную карту | Cancel remaining + parent/item updates — одна транзакция; выданные items не меняются | Нельзя отменить выданную позицию; при ошибке одной невыданной позиции вся отмена остатка откатывается |
| DB-18 | Один P-09 parent может иметь несколько fulfillment batches; каждая batch относится ровно к одному parent | FK request_id + route guard + deferred trigger; one-to-many parent/batch | Создание batch, manifest и связи с parent — один commit | Batch без parent, с чужим маршрутом или картами другого parent отклоняется |
| DB-19 | Manifest каждой fulfillment batch содержит только выбранные ready-to-issue items одного parent; после создания draft неизменяем | Deferred trigger сверяет ShipmentCard, request_id и item.fulfillment_batch_id | Создание batch, manifest snapshot и item links — один commit | Карта другого parent, повторное назначение item, изменение manifest после создания или попытка включить все parent items автоматически отклоняются |
| DB-20 | Handover batch фиксирует физическую выдачу только выбранных items; custody карт остаётся у архива до item receipt ЦО | Shipment/item state guards + deferred consistency trigger; courier — перевозчик, не владелец учётной custody | Batch draft→in_transit, selected items→issued_pending_receipt и события — один commit | Не выбранные items не меняются; custody не переходит курьеру/ЦО; повторная выдача и чужие items отклоняются |
| DB-21 | P-09 receipt выполняется по карте; выбранный набор фактически поступивших карт можно подтвердить атомарно. Обычный box shipment принимается по одной коробке | Item-level receipt command + bulk all-or-nothing; отдельная box receipt command; actor/location/status guards | P-09 item/bulk receipt — одна транзакция на выбранный набор; box receipt — одна коробка за транзакцию | Нельзя подтвердить не поступившую карту; ошибка одной карты откатывает bulk; receipt одной коробки не принимает остальные |
| DB-22 | Parent completed только когда все request items терминальны: получены ЦО, not_found либо отменены как невыданный остаток | Deferred trigger на parent/items/batches; выданные без receipt items остаются nonterminal | Последний item outcome/receipt/cancel remaining и parent completion — одна транзакция | completed при pending/extracted/issued_pending_receipt отклоняется |
| DB-23 | `not_found` можно исправлять до создания fulfillment-черновика; повторные циклы `not_found ↔ extracted` разрешены при фактическом подтверждении физического состояния | Trigger переходов статусов и append-only метаданные | Каждая фиксация/корректировка — отдельная транзакция | `not_found → extracted` без обнаружения карты в коробке отклоняется; после создания fulfillment-черновика исправление запрещено |

| DB-71 | Статус fulfillment batch является проекцией receipt её manifest items: draft, in_transit, partially_received, received, cancelled | Deferred batch/item consistency guard; receipt counter по неизменяемому manifest | Batch state и item receipt updates — один commit | received при хотя бы одном не полученном item, partially_received без receipt, handover отменённой batch — отказ |
| DB-72 | Каждый item-level receipt создаёт собственное событие и custody/location transfer только для соответствующей карты | Item receipt command + DomainEvent append-only + custody guard | Для bulk receipt каждый item update, custody transfer и его событие входят в одну общую транзакцию | Пропущенное событие, двойной receipt или перенос custody не подтверждённой карты отклоняется; ошибка одной позиции откатывает весь bulk |
| DB-73 | Handover одной batch не блокирует отмену невыданного остатка parent; parent остаётся processing пока выданные items ждут receipt | Parent/item locks + serialized cancel-remaining and batch-handover commands | Отмена выбранного остатка и batch handover сериализуются на parent; уже выданные items вне отменяемого набора | Отмена handed-over item запрещена; гонка не может одновременно включить один item в handover и cancelled remainder |
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
- Попытка отменить уже выданную позицию P-09; отмена выданной позиции должна быть отклонена. Проверять отмену невыданного остатка отдельно.
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
| DB-31 | Исправления `not_found`/`removed_by_archive → extracted` и повторная фиксация `extracted → not_found` разрешены до fulfillment-черновика; физические действия и результат атомарны | `request_restore_item_to_extracted` и `request_mark_not_found`; блокировки parent/item/card/membership/box; append-only DomainEvent; guard item не назначен в fulfillment batch | `not_found → extracted`: обнаружить карту в коробке и закрыть membership; `extracted → not_found`: карта не обнаружена, membership не создаётся; событие хранит прежний/новый результат, actor/time | Циклы `not_found → extracted → not_found` и `removed_by_archive → extracted` успешны при фактическом соответствии физического состояния; отсутствие карты при `→ extracted`, наличие fulfillment-ссылки, недостаточные права или конкурентное создание fulfillment первым отклоняют команду без частичных изменений |



## DB-34…DB-38 — обычные отправления

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Негативный тест |
|---|---|---|---|---|
| DB-34 | Обычный shipment создаётся только в `draft`, с непустым коробочным manifest и активным курьером; у автора не более одного незавершённого draft | UNIQUE/partial index + закрытая command procedure + FK/active/scope checks | Создание shipment, manifest, courier FK/snapshot и create event — один commit | Пустой manifest, дубли коробок, чужая/занятая/пустая коробка, неактивный курьер или второй draft отклоняются |
| DB-35 | Manifest и courier можно менять только текущему автору и только в `draft` | Trigger/command allowlist проверяет actor, status и scope | Manifest/courier mutation и событие/результат — атомарно | Изменение другим сотрудником или после handover отклоняется; snapshot курьера соответствует FK |
| DB-36 | Handover обычного shipment выполняет только текущий активный автор; один раз | Закрытая procedure, row lock, active/role/location guard, unique handover event | Shipment, коробки/карты custody, snapshot manifest и handover event — один commit | Чужой/deactivated actor, второй handover, невалидный manifest или отсутствие courier отклоняются |
| DB-37 | Receipt обычного входящего shipment выполняет активный сотрудник archive по одной коробке | Route/scope/status guard, row lock, unique box receipt event, deferred custody consistency | Одна коробка, её custody, actor/time и DomainEvent — один commit; shipment остаётся открытым до всех receipts и расхождений | Повторная приёмка одной коробки, чужая коробка, неверная локация или shipment не `in_transit` отклоняются |
| DB-38 | Для обычного box shipment после handover manifest/courier immutable; receipt каждой коробки переводит её custody архиву; отмена shipment допускается только в draft автором | State transition trigger + box custody guard + author guard | Cancel/handover и каждый box receipt сериализуются на shipment/box rows | Изменение manifest/courier после передачи, отмена после передачи, повторная приёмка коробки или повторная смена custody отклоняются. Не применять этот запрет к отмене невыданного остатка P-09 |


| DB-39 | В обычном коробочном shipment карты перемещаются только как содержимое коробок; manifest состоит из `ShipmentBox`, memberships/BoxCycle сохраняются, `ShipmentCard` и P-09 item для них не создаются | Закрытые процедуры shipment handover/box receipt + deferred consistency trigger по составу BoxCycle | Handover всех коробок — один commit; receipt каждой коробки и custody её содержимого — отдельный commit | Добавление отдельной карты в обычный manifest, изменение membership при handover либо частичная приёмка содержимого одной коробки отклоняются |
| DB-40 | Для обычных коробочных маршрутов в пути custody коробки и её содержимого принадлежит назначенному курьеру; фактическое местоположение не отслеживается; destination фиксируется по каждой коробке при receipt | Shipment status, courier FK/snapshot и защищённые location/custody guards | Handover всех коробок — один commit; каждый box receipt — отдельный commit | Прямое присвоение destination до receipt, изменение курьера после handover или самостоятельное изменение custody карты внутри коробки отклоняется. Этот инвариант не применяется к P-09, где custody карты остаётся у архива до receipt ЦО |
| DB-41 | При receipt обычного коробочного shipment BoxCycle/memberships сохраняются; полочное размещение не создаётся автоматически | Deferred consistency trigger + отдельная команда box placement | Receipt не включает независимую операцию shelf placement | Receipt не должен закрывать цикл, менять членства или создавать placement без отдельного shelf assignment |
| DB-42 | Терминальный `not_found` item может сохраняться у завершённой/отменённой P-09 без membership и shipment; это не активная позиция и не физическое размещение карты | Request/item history + deferred card-state guard; активность заявки определяется отдельно от терминального результата item | Завершение/отмена P-09 сохраняет item outcome и карточный учётный статус согласованно | Удаление `not_found` истории, создание фиктивного membership или трактовка результата как активной обработки отклоняются |

## DB-43 — резервирование карты на весь срок незавершённой P-09

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-43 | Каждая карта из актуального состава удерживается ровно одной P-09 до `completed`/`cancelled` родителя. Item outcome `removed_by_archive` или `not_found` не освобождает карту, пока родитель `processing` | `reservation_active` на item; partial UNIQUE по `card_id WHERE reservation_active`; закрытые процедуры и deferred trigger согласованности с parent/tombstone | Создание/редактирование draft и terminal transition parent выполняются с блокировкой parent/card/item; reservation flag и события меняются в одной транзакции | Вторая заявка на карту с любым item outcome незавершённой P-09 отклоняется; параллельное создание даёт ровно одного победителя; draft tombstone и terminal parent освобождают резервирование атомарно; рассогласованный flag отклоняется |

## DB-44 — полная приёмка fulfillment archive → central_office без поштучной сверки

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Проверка |
|---|---|---|---|---|
| DB-44 | ЦО подтверждает фактическое получение одной реально поступившей карты P-09; несколько поступивших карт допускается подтвердить атомарно bulk-командой. Custody остаётся у архива до item receipt | Route/status/recipient-scope guard, row locks, unique item receipt event, deferred consistency между request item, card и custody | Для выбранной карты/набора item receipt, actor/time, custody archive→CO и DomainEvent; bulk набор — одна транзакция | Не поступившая физически карта не подтверждается; одна недопустимая позиция откатывает весь bulk; повторное подтверждение не создаёт событие; прочие позиции заявки не меняются |

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

## DB-52 — минимальные поля списка предыдущих циклов коробки

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-52 | Сводная строка предыдущего BoxCycle содержит дату/время начала, дату/время завершения при завершённом цикле, количество карт и состояние. Номера карт и ФИО пациентов доступны только в отдельно открытом цикле при самостоятельном разрешении на исторический состав | Отдельная allowlist-проекция списка циклов без card identifiers и patient names; cycle detail использует отдельную авторизацию и отдельную проекцию; незавершённый цикл не получает фиктивное время завершения | Box detail, список BoxCycle, cycle detail, поиск/фильтры/счётчики списка при их наличии | Список содержит только четыре согласованные группы полей; незавершённый цикл не показывает дату завершения; список не раскрывает номер карты или ФИО ни в UI, API, поисковых фрагментах, счётчиках или сериализованном payload; уполномоченный пользователь видит исторический состав только после открытия цикла; отсутствие права не обходится прямым ID/API |

Дополнительное требование к DB-52: список BoxCycle возвращается в обратном хронологическом порядке по времени начала (DESC), самый новый цикл располагается первым. Проверка должна подтверждать порядок при нескольких циклах, включая одинаковую дату при различном времени начала.


## DB-53 — пустое состояние списка предыдущих циклов коробки

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-53 | Если у физической коробки нет предыдущих BoxCycle, список пуст и UI показывает нейтральное сообщение «Предыдущих логистических циклов нет»; фиктивные циклы и значения не создаются | Пустая коллекция циклов сериализуется как пустой список; empty-state является UI-состоянием, а не синтетической записью доменной модели | Box detail, список предыдущих BoxCycle, API/read model | Для коробки без предыдущих циклов отображается empty-state; API возвращает пустой список; отсутствуют синтетические даты, количества, статусы и идентификаторы; при появлении реального цикла он отображается согласно DB-52 |


## DB-54 — физическая коробка без текущего логистического цикла

| ID | Инвариант | Read-side enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-54 | Если физическая коробка существует, но текущий BoxCycle отсутствует (предыдущий завершён, новый ещё не начат), текущий цикл не синтезируется; блок текущего состава показывает «Коробка сейчас пуста» | Отсутствующая связь с текущим BoxCycle представляется как отсутствие объекта/пустое значение, а не как фиктивная сущность; empty-state формируется UI. Список предыдущих циклов читается отдельно по §47.9 и с самостоятельной авторизацией | Box detail, блок текущего состава, список предыдущих BoxCycle, API/read model | Для коробки без текущего цикла UI показывает согласованный empty-state, API не возвращает фиктивный BoxCycle и фиктивный состав; предыдущие циклы остаются доступны уполномоченному пользователю; отсутствие текущего цикла не удаляет коробку и не изменяет её custody/локацию; прямой доступ к прошлому циклу без права запрещён |

## DB-55 — создание BoxCycle только при добавлении первой карты

| ID | Инвариант | Enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-55 | Создание физической коробки не создаёт BoxCycle. Первый цикл создаётся атомарно с добавлением первой карты; если открытого цикла нет, операция добавления создаёт новый цикл и первое membership в одной транзакции. Пустой начальный цикл не создаётся | Закрытая команда box_create создаёт только Box; box_add_card блокирует Box и в одной PostgreSQL-транзакции либо добавляет membership в существующий открытый цикл, либо создаёт BoxCycle и первое membership. DB guard запрещает цикл без фактического первого membership и прямой обход команд | Создание коробки, первое добавление карты, повторное заполнение после завершённого цикла, конкурентные box_add_card | После box_create Box существует, открытого BoxCycle нет; первый box_add_card создаёт ровно один открытый цикл с первой membership; rollback не оставляет пустой цикл при неудачном добавлении; параллельные добавления не создают два открытых цикла; повторное заполнение создаёт новый ID цикла и не меняет историю предыдущего |
## DB-56 — автоматическое завершение BoxCycle после выхода последней карты

| ID | Инвариант | Enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-56 | При закрытии последнего активного membership текущий BoxCycle автоматически завершается в той же транзакции. Правило действует при P-09 extraction, перегруппировке и любой иной разрешённой операции удаления последней карты. Открытый цикл без активных карт недопустим | Все команды, закрывающие membership, выполняют атомарную проверку остаточного состава и закрывают BoxCycle, если активных membership больше нет. DB guard запрещает commit с открытым пустым циклом; завершённый цикл и его memberships остаются историческими | box_remove_card, box_repack, P-09 extraction, исправление результата/возврат, конкурентные операции добавления и удаления | Удаление последней карты закрывает цикл в той же транзакции; удаление не последней карты оставляет цикл открытым; rollback не оставляет рассогласованного состояния; regroup закрывает цикл пустеющей коробки, но сохраняет её BoxPlacement; P-09 extraction закрывает цикл и BoxPlacement; последующее заполнение создаёт новый цикл с новым ID; нельзя оставить открытый цикл без карт |

## DB-57 — повторное заполнение пустой коробки на полке после перегруппировки

| ID | Инвариант | Enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-57 | Пустую коробку, оставшуюся на полке после перегруппировки с активным BoxPlacement, разрешено повторно заполнить без предварительного снятия с полки. Добавление первой карты создаёт новый BoxCycle и первое membership атомарно, существующий BoxPlacement сохраняется | `box_add_card` допускает активный BoxPlacement только для пустой коробки в разрешённом состоянии после regroup; повторно проверяет scope, статус, shipment и конфликты; в одной транзакции создаёт новый цикл и membership, не закрывая и не пересоздавая placement | box_add_card, box_repack, конкурентные добавления/размещения/отправления | Пустая коробка после regroup остаётся на полке; первое добавление создаёт ровно один новый цикл, сохраняет тот же BoxPlacement и его историю; второе добавление входит в новый цикл; запрещено добавление, если коробка включена в активный shipment или нарушен scope/статус; rollback не оставляет пустой цикл; параллельные добавления не создают два открытых цикла |

## DB-58 — отдельное снятие пустой коробки с полки

| ID | Инвариант | Enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-58 | Пустую коробку архива без открытого BoxCycle и без активного shipment можно снять с полки отдельной командой `box_unplace`. Команда закрывает активный BoxPlacement, сохраняя его историю; физическая коробка, завершённые циклы, учётная локация и custody сохраняются. Снятие с полки не является списанием | Закрытая DB-команда `box_unplace` проверяет активность и полномочия archive actor, location scope, отсутствие активных memberships/открытого цикла и shipment, наличие ровно одного активного placement; атомарно закрывает placement и пишет DomainEvent. `box_write_off` остаётся отдельной командой и запрещён при активном placement | box_unplace, box_add_card, box_repack, box_write_off, конкурентные размещение/отправление | Пустая коробка после regroup снимается с полки, placement становится историческим, коробка остаётся в архиве; завершённые BoxCycle не меняются; непустую коробку, открытый цикл или коробку в shipment снять нельзя; без активного placement повторный unplace отклоняется; после unplace разрешены отдельное повторное размещение или списание при выполнении их условий; rollback не оставляет частично закрытого placement или события без изменения состояния |

## DB-59 — размещение заполненной или пустой коробки на полке

| ID | Инвариант | Enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-59 | Уполномоченный сотрудник архива может отдельной командой `box_place` разместить на активной полке своей архивной локации как заполненную коробку, так и пустую коробку без открытого BoxCycle. Коробка должна числиться в этой локации, не участвовать в активном shipment и не иметь активного BoxPlacement. Размещение не создаёт BoxCycle и не меняет membership, учётную локацию или custody; прежние placements сохраняются | Закрытая DB-команда `box_place` блокирует и повторно проверяет actor/membership, Box и Shelf; проверяет совпадение архивной локации, active shelf, отсутствие активного shipment и placement; допускает оба состояния коробки — с активным циклом/картами и без них; атомарно создаёт BoxPlacement и DomainEvent. Частичный UNIQUE DB-03 сохраняет максимум одно активное размещение | box_place, box_unplace, box_add_card, box_repack, box_write_off, конкурентные размещение/снятие/отправление | Положительные случаи: заполненная коробка размещается; пустая коробка без открытого цикла размещается; после box_unplace коробка размещается повторно с новым placement, а прежний остаётся в истории; пустая размещённая коробка затем заполняется через box_add_card без замены placement. Отрицательные случаи: чужая/неактивная полка, другая учётная локация, активный shipment, уже активный placement, неуполномоченный или деактивированный actor отклоняются; ошибка записи события откатывает создание placement; конкурентные box_place не создают два активных placement |

## DB-61 — перегруппировка коробки с активным полочным размещением

| ID | Инвариант | Enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-61 | Архивный сотрудник может извлекать карты из коробки с активным `BoxPlacement` в рамках перегруппировки, включая последнюю карту. При закрытии последнего membership `BoxCycle` завершается, но `BoxPlacement` сохраняется; пустая коробка остаётся на полке | Закрытая DB-команда `box_repack` проверяет активность и полномочия archive actor, scope, отсутствие активного shipment и конфликтующих операций; допускает исходную коробку с активным placement и не закрывает placement при закрытии последнего membership. Цикл и memberships меняются атомарно, событие append-only | box_repack, box_add_card, box_unplace, box_place, конкурентные отправления/перегруппировки | Положительно: извлечение части карт сохраняет placement и текущий цикл; извлечение последней карты закрывает цикл и сохраняет placement; пустую коробку можно повторно заполнить с тем же placement или отдельно снять с полки. Отрицательно: активный shipment, чужая область, неактивный/неуполномоченный actor и конфликтующая операция отклоняются; rollback не оставляет частичного изменения состава, цикла, placement или события |

## DB-60 — списание пустой физической коробки без обязательной причины

| ID | Инвариант | DB enforcement (target) | Транзакционная граница | Негативный тест |
|---|---|---|---|---|
| DB-60 | Пустая коробка может быть окончательно выведена из действующего учёта командой `box_write_off`, только если она не участвует в активном shipment и не имеет активного BoxPlacement. Причина/комментарий не обязательны. Физическая запись коробки и вся история сохраняются | Закрытая DB-команда проверяет роль и scope по P-05: `branch`, `central_office` и `archive` — собственная операционная область/локация; активный `admin` — любая локация по отдельному прямому полномочию, отсутствие активных memberships/открытого BoxCycle, активного shipment и placement; фиксирует actor/time и append-only DomainEvent. Прямой DML закрыт | Изменение состояния списания и событие фиксируются одной транзакцией | Списание без причины успешно при выполнении условий; непустая коробка, активный shipment/placement, неуполномоченный или деактивированный actor отклоняются; сбой события откатывает изменение; повторный вызов не создаёт второе списание; запись коробки и история остаются доступными в пределах прав |


| DB-62 | Исправление ошибочного перемещения коробки между архивными полками выполняется только отдельной корректирующей командой; первоначальные событие и запись размещения неизменяемы | Защищённая команда корректировки требует обязательный код причины из закрытого перечня (свободный комментарий не требуется), применяется только к одной коробке за команду, не поддерживает массовую корректировку и создаёт отдельное событие и новое действующее размещение; проверяет фактическое положение коробки, актуальное размещение, активность и область целевой полки, полномочия/scope и отсутствие конфликтующих процессов; изменение и обязательное событие атомарны | box placement correction | Позитивный: ошибочное перемещение исправлено отдельным событием без переписывания исходной истории. Негативные: прямое изменение/удаление исходного события запрещено; возврат на прежнюю полку отклонён, если фактическое положение не подтверждено, полка неактивна/в другой области либо есть конфликтующий процесс; ошибка обязательной записи события откатывает изменение. Состав коробки, BoxCycle, memberships, custody и учётная локация остаются неизменными |
| DB-63 | Коробку, включённую в незавершённую заявку ЦО → Архив, нельзя перемещать между архивными полками до завершения заявки | Защищённая команда перемещения проверяет актуальное участие коробки в незавершённой заявке на сервере; проверка участия/состояния заявки согласована транзакционно с изменением BoxPlacement и обязательным событием, конкурентные изменения состава/завершение заявки не обходят запрет | box move between archive shelves, request composition changes/completion, concurrent move | Перемещение отклоняется, если коробка включена в незавершённую заявку; после завершения заявки перемещение разрешено при соблюдении остальных правил; конкурентная гонка не позволяет переместить коробку на основании устаревшего состава или статуса; другие операции с коробкой не блокируются этим правилом автоматически |
| DB-64 | При участии коробки в незавершённой заявке ЦО → Архив обычное перемещение между архивными полками запрещено; исключение — отдельная корректирующая операция для исправления подтверждённого фактического размещения | Отдельная защищённая команда доступна только активному сотруднику архива с отдельным специальным полномочием (обычного `box_place` недостаточно; роль администратора сама по себе права не даёт) и scope; обязательны код причины из закрытого перечня и отдельное событие; проверяет актуальную заявку, фактическое положение коробки, целевую активную полку той же архивной области и конфликтующие процессы; не меняет заявку, manifest, handover/receipt, custody или состав коробки; изменение BoxPlacement и событие атомарны | ordinary box move, corrective placement while CO-to-archive request is unfinished, request/manifest changes, concurrent placement | Обычное перемещение при незавершённой заявке отклоняется; корректировка разрешена только с нужным полномочием, корректной причиной и подтверждённым фактическим положением; неверная причина, неактивная/чужая полка, конфликт или отсутствие полномочия приводят к отказу; заявка и её manifest/состояние/история, custody, состав коробки и учётная локация не меняются; конкурентные изменения не обходят проверки; сбой обязательного события откатывает корректировку |


## DB-65 — деактивация филиала при отсутствии карт, числящихся за ним

| ID | Инвариант | Enforcement (target) | Область проверки | Негативная/позитивная проверка |
|---|---|---|---|---|
| DB-65 | Филиал нельзя деактивировать, пока в системе остаётся хотя бы одна медицинская карта, учётно числящаяся за ним. До деактивации карты явно переводятся предусмотренным процессом в другое действующее подразделение либо в архив; автоматический перенос запрещён. История принадлежности и остальные факты карты не переписываются | Защищённая административная команда проверяет отсутствие карт с текущей учётной принадлежностью к филиалу непосредственно перед деактивацией; проверка остатка карт, изменение статуса филиала и обязательный `AdminSecurityAudit` атомарны и защищены от конкурентного изменения принадлежности. Существующие проверки активных сотрудников и незавершённых операций сохраняются | org-unit deactivation, card transfer, concurrent card assignment changes | Деактивация отклоняется при наличии хотя бы одной карты, числящейся за филиалом; успешна после явного перевода всех таких карт в допустимые активные подразделения/архив и выполнения остальных условий; параллельное создание/перевод карты не позволяет обойти проверку; отказ/ошибка audit не оставляет филиал частично деактивированным; принадлежность, custody и фактическая локация не меняются автоматически |

## Проверка возможных совпадений и нескольких карт одного пациента

- Перед регистрацией новой карты система ищет возможные совпадения только по ФИО пациента; ручной поиск по номеру карты, телефону и дате рождения не предоставляется. Сканирование штрихкода остаётся отдельным способом идентификации. Поиск поддерживает неполный ввод ФИО: только фамилию и начало фамилии; полное ФИО не обязательно. Запрос только по фамилии возвращает все доступные совпадения с этой фамилией независимо от имени и отчества. Если введено несколько частей ФИО, каждая часть сопоставляется с начала соответствующего слова, порядок частей сохраняется (например, «Иванов Ив» находит «Иванов Иван Петрович», а «Иванов Иван П» находит «Иванов Иван Петрович»; правило применяется также к началу отчества). Сопоставление не зависит от регистра; `е` и `ё` нормализуются и считаются эквивалентными. Поисковый ввод очищается от пробелов по краям, а последовательности нескольких пробельных символов между частями ФИО сводятся к одному; нормализация применяется только к поисковому вводу и не изменяет сохранённое ФИО. Порядок частей ФИО в запросе сохраняется; перестановка фамилии, имени и отчества не допускается. Поиск по фамилии выполняется только с начала слова: «Ива» находит «Иванов», «ванов» не находит; поиск по произвольному внутреннему фрагменту запрещён.
- В результатах поиска показываются ФИО и номер медицинской карты. Дата рождения и телефон (если указаны) показываются всем активным сотрудникам, имеющим доступ к соответствующей карте; отдельные права на чтение этих полей не требуются.
- В одном списке показываются все совпадения по ФИО, доступные сотруднику в его scope; искусственное ограничение количества, скрытый top-N и разделение на несколько списков запрещены. Техническая порционная загрузка допустима только как непрерывное продолжение того же списка без потери или сокрытия совпадений.
- Найденные записи и все поля результата выдаются только в пределах действующих read permissions, с проверкой scope до формирования ответа.
- Один пациент может иметь несколько самостоятельных медицинских карт, в том числе из разных филиалов; совпадение ФИО само по себе не запрещает создание ещё одной карты.
- Сотрудник может выбрать найденную запись, если это именно нужная медицинская карта. Если требуется другая карта тому же пациенту, разрешается создать отдельную запись.
- Номер медицинской карты и QR/штрихкод остаются уникальными идентификаторами; конфликт с уже существующим номером/QR нельзя обойти подтверждением, что создаётся другая карта.
- Автоматическое объединение/слияние карт и автоматическое изменение найденных записей запрещены.
- Сервер повторно проверяет уникальность номера/QR при выполнении команды; конкурентные регистрации не должны обходить защиту от одинаковых идентификаторов.
- Те же правила действуют при создании карты непосредственно из сценария добавления карты в коробку; новая карта и её добавление выполняются атомарно.

**Минимальная матрица проверок:** поиск совпадений по полному и неполному ФИО, включая только фамилию и начало фамилии; сохранение порядка частей ФИО (перестановка частей запроса не даёт совпадение); поиск каждой введённой части ФИО только с начала соответствующего слова с сохранением порядка («Ива» находит «Иванов», «ванов» не находит; «Иванов Ив» находит «Иванов Иван Петрович», «Иванов Иван П» находит «Иванов Иван Петрович»; внутренний фрагмент не совпадает); запрос только по фамилии возвращает все доступные карты с этой фамилией независимо от имени и отчества; отсутствие ручного поиска по номеру карты, телефону и дате рождения; отображение всех доступных совпадений в одном списке без искусственного лимита и потери результатов; отображение даты рождения и телефона всем активным сотрудникам с доступом к карте; отсутствие отдельных read permissions для этих полей; выбор существующей карты без создания новой; разрешение создать отдельную карту тому же пациенту; конфликт одинакового номера карты/QR; запрет автоматического слияния; конкурентная регистрация одинакового номера/QR; недоступность чужих/закрытых записей в выдаче совпадений.


**Отрицательный инвариант:** поиск только по имени или отчеству не выполняется. Например, запрос «Иван» не должен находить карты по совпадению только имени; первым компонентом поискового запроса всегда должна быть фамилия.


**UX-инвариант автоматического поиска:** после изменения допустимого поискового ввода интерфейс автоматически запускает поиск с небольшой задержкой; кнопка «Найти» не обязательна. Результаты отображаются только для актуального значения поля: поздний ответ от предыдущего запроса не должен перезаписывать более новые результаты. Права и scope проверяются сервером для каждого запроса.


## 2026-11-21 — сохранение поиска при возврате из карточки медицинской карты

Утверждено владельцем продукта: если сотрудник открывает карточку медицинской карты из результатов поиска и возвращается назад, система сохраняет поисковый запрос и состояние списка результатов, чтобы не требовалось выполнять поиск заново. При восстановлении списка сохраняются введённый запрос, позиция/состояние списка и ранее полученные результаты; при этом данные и доступность результатов не считаются бессрочно разрешёнными: серверные права, scope и актуальность доступа должны быть повторно проверены перед повторным показом или использованием сохранённых результатов. Если доступ изменился, недоступные записи и поля не показываются. Возврат не должен запускать неожиданный поиск по устаревшему вводу или позволять позднему ответу перезаписать актуальное состояние. Реализация не разрешена.


## 2026-11-21 — сортировка результатов поиска медицинских карт

Утверждено владельцем продукта: результаты поиска медицинских карт отображаются в алфавитном порядке по полному ФИО — фамилия, имя, отчество. Для однозначного и стабильного порядка записей с одинаковым ФИО применяется дополнительная сортировка по номеру медицинской карты. Сортировка не расширяет набор доступных результатов и применяется только после серверной проверки действующих прав, scope и доступности полей. Реализация не разрешена.


## 2026-11-21 — визуальное выделение совпадений в ФИО

Утверждено владельцем продукта: в результатах поиска визуально выделяются совпавшие части ФИО, соответствующие введённым компонентам поискового запроса. Например, при запросе «Иванов Ив» совпавшие префиксы фамилии и имени выделяются в отображаемом ФИО. Выделение является только визуальной подсказкой: оно не меняет правила сопоставления, порядок частей ФИО, сортировку, состав результатов или права доступа. Нельзя показывать скрытые/недоступные части данных ради подсветки; подсветка строится только по уже разрешённому к показу ФИО. Реализация не разрешена.


## 2026-11-21 — количество результатов поиска медицинских карт

Утверждено владельцем продукта: над списком результатов поиска показывается общее количество найденных медицинских карт, например «Найдено: 24 карты». Количество должно соответствовать полному набору результатов, доступных сотруднику с учётом действующих scope и read permissions, а не только текущей загруженной порции. Недоступные записи не должны учитываться или раскрываться через счётчик. Подсчёт выполняется после серверной авторизации и фильтрации; если список загружается порциями, счётчик отражает весь доступный набор. Реализация не разрешена.


## 2026-11-21 — пустая выдача поиска медицинских карт

Утверждено владельцем продукта: если поиск не находит доступных сотруднику медицинских карт, вместо пустого списка показывается понятное сообщение: «По вашему запросу медицинские карты не найдены». Поле поискового ввода остаётся доступным и сохраняет текущий запрос, чтобы сотрудник мог сразу изменить его. Сообщение не должно раскрывать наличие записей, скрытых действующими правами или scope: отсутствие доступных результатов обозначается одинаково независимо от причины недоступности. Реализация не разрешена.


## 2026-11-21 — очистка поискового запроса медицинских карт

Утверждено владельцем продукта: поле поиска медицинских карт имеет доступное действие очистки (иконка «×»). По нажатию поисковый запрос полностью очищается, результаты и счётчик приводятся к состоянию пустого запроса, фокус/возможность ввода сохраняются в соответствии с обычным поведением поля. Очистка не меняет правила поиска, права доступа или доступный сотруднику набор данных. Это стандартное UX-поведение; отдельное согласование реализации не требуется.

## Процесс согласования UX

Не выносить на отдельное согласование мелкие стандартные детали интерфейса. Типовые UX-решения принимать самостоятельно, фиксируя их в документации. Владельцу продукта выносить только существенные вопросы, влияющие на бизнес-процессы, права доступа, безопасность, целостность данных или заметные компромиссы удобства. Предпочитать закрытие целых функциональных блоков вместо серии малозначительных вопросов.

## Дополнение — инварианты ручного создания карты

Утверждено 2026-11-21:
- Ручное создание требует непустые номер карты, ФИО и дату рождения; телефон допускается пустым.
- Номер карты уникален на уровне БД; конкурентные попытки создать один номер не могут породить две карты.
- Перед созданием выполняется проверка возможных совпадений по ФИО.
- Совпадающее ФИО само по себе не запрещает отдельную карту: после проверки сотрудником отдельная запись разрешена.
- Нельзя автоматически объединять карты или считать ФИО идентификатором.
- Повторный номер карты не создаёт дубль.
- Тестировать эти правила как для отдельного ручного создания, так и для создания из сценария добавления карты в коробку.

## Решение от 2026-11-22 — исправление данных медицинской карты

Согласовано владельцем продукта: уполномоченные сотрудники могут исправлять ФИО и дату рождения медицинской карты. Для каждого исправления обязательна причина. Система сохраняет прежнее и новое значение, автора, дату и время; первоначальные данные и предыдущие события не переписываются и не удаляются. Номер медицинской карты не изменяется в рамках обычной корректировки данных. Исправление выполняется отдельным контролируемым действием с серверной проверкой полномочий и атомарной фиксацией изменения и истории. Конкретный перечень ролей/полномочий должен быть согласован в матрице доступа; реализация не разрешена.

## Решение от 2026-11-22 — полномочия на исправление данных медицинской карты

Согласовано владельцем продукта: исправлять ФИО и дату рождения медицинской карты могут сотрудники филиала, центрального офиса и архива только при наличии отдельно назначенного полномочия `card_correct` и только в пределах карт, доступных им по действующим scope и правилам доступа. Полномочие не предоставляется сотрудникам автоматически по роли или подразделению. Активный администратор может назначить или отозвать `card_correct` у другого сотрудника; назначать это полномочие самому себе администратор не может. Назначение/отзыв выполняется только для активной учётной записи, с проверкой целевой роли и состояния, и обязательно фиксируется в `AdminSecurityAudit`. Роль администратора сама по себе не даёт `card_correct`: администратор получает его только если другой активный администратор назначил это полномочие. Все требования к причине исправления, сохранению старых/новых значений, автора и времени, неизменности номера карты и append-only истории сохраняются. Реализация не разрешена.

## Решение от 2026-11-22 — отдельная корректировка номера медицинской карты

Согласовано владельцем продукта: номер медицинской карты нельзя изменять в рамках обычного исправления данных (`card_correct`). Если обнаружена ошибка в номере, используется отдельная контролируемая операция с повышенным уровнем полномочий, обязательной причиной и сохранением прежнего и нового номера, автора, даты и времени в неизменяемой истории. Новый номер должен сохранять глобальную уникальность; если он уже принадлежит другой карте, операция отклоняется и объединение/перенос номера автоматически не выполняются. Исходный номер и история его использования не удаляются и не переписываются. Конкретное отдельное полномочие и круг сотрудников, которым оно может назначаться, подлежат отдельному проектному решению. Реализация не разрешена.

## Решение от 2026-11-22 — полномочия на отдельную корректировку номера медицинской карты

Согласовано владельцем продукта: отдельную операцию изменения номера медицинской карты могут выполнять все активные администраторы автоматически — право `card_number_correct` входит в полномочия активной роли администратора и не требует отдельного назначения или отзыва. При деактивации администратора это право перестаёт действовать автоматически. Обычные сотрудники филиала, центрального офиса и архива такого права не имеют. Операция дополнительно проверяет, что actor остаётся активным администратором, имеет доступ к конкретной карте, новый номер глобально уникален и указана обязательная причина; старый и новый номера, автора, дату и время сохраняет в неизменяемой истории. Реализация не разрешена.


## Решение от 2026-11-22 — просмотр даты рождения и телефона

Владелец продукта утвердил, что все активные сотрудники всех целевых ролей (`branch`, `central_office`, `archive`, `admin`) могут просматривать дату рождения и телефон пациента, если телефон указан. Для этих двух полей отдельные read permissions не требуются. Доступ к самой медицинской карте по-прежнему определяется активностью пользователя, ролью, организационным scope, custody, состоянием объекта и участием в процессе; это решение не расширяет область доступных карт. Дата рождения и телефон могут отображаться в самостоятельной карточке карты и результатах поиска/проверки совпадений. Ручной поиск по этим полям по-прежнему запрещён. Их включение в манифесты, очереди, счётчики и уведомления по-прежнему запрещено. Право просмотра не даёт права исправления: изменение ФИО и даты рождения остаётся под `card_correct` и обязательной причиной. Адрес пациента по-прежнему не собирается. Это решение заменяет все прежние формулировки об отдельных правах на чтение даты рождения и телефона. Реализация не разрешена.


## DB-инвариант — списание медицинской карты (P-04/D-13)

ЦО может инициировать запрос на списание с обязательной причиной; только уполномоченный активный сотрудник архива после проверки основания выполняет списание. Успешное списание переводит карту в статус «Списана», сохраняет запись и всю историю, фиксирует автора/время и отдельное событие. Физическое удаление запрещено. Branch/admin не могут инициировать или исполнять эту операцию. Точные проверки допустимости и перечень оснований будут определены отдельно.

**Минимальные проверки:** инициирование только ЦО; обязательная причина; исполнение только активным уполномоченным сотрудником архива; отсутствие исполнения без проверки; статус после успешного списания; сохранение карты и истории; невозможность физического удаления; отказ branch/admin; событие и изменение статуса фиксируются согласованно.


## Решение от 2026-11-22 — порядок списания медицинской карты (P-04/D-13)

Владелец продукта подтвердил следующий процесс списания медицинской карты:

1. Центральный офис (ЦО) инициирует запрос на списание конкретной медицинской карты и указывает обязательную причину.
2. Архив проверяет карту и основание запроса.
3. Списание выполняет уполномоченный активный сотрудник архива после проверки.
4. После успешного списания карта получает статус «Списана» и больше не участвует в обычных операциях.

Физическое удаление записи медицинской карты запрещено. Сохраняются сама запись, прежние данные, история перемещений и события; факт списания фиксируется отдельным событием с автором и временем. Филиал и администратор не инициируют и не исполняют списание; ЦО инициирует, архив исполняет. Это решение утверждает общий бизнес-порядок, но не определяет пока закрытый перечень оснований/документов, точные проверки допустимости, возможность и порядок отказа по запросу, а также детальную модель состояний и команд — эти технические детали будут проработаны без изменения утверждённого распределения ролей. Реализация не разрешена.


## Решение от 2026-11-22 — формат причины списания медицинской карты

Владелец продукта утвердил, что причина списания обязательна и выбирается из закрытого справочника. Свободный текст не заменяет выбранную причину. Дополнительный комментарий допускается, но остаётся необязательным. Значение выбранной причины и комментарий (если указан) сохраняются в запросе и доступны в его истории в пределах действующих прав. Закрытый перечень причин будет определён отдельно при детализации оснований списания. Реализация не разрешена.


## Решение от 2026-11-22 — отдельное право на исполнение списания карты

Владелец продукта согласовал, что списание медицинской карты в архиве выполняет только активный сотрудник архива, которому явно назначено отдельное право на исполнение списания медицинских карт. Это право не предоставляется автоматически ни по роли `archive`, ни по факту работы в архиве; по умолчанию оно отсутствует. Наличие права не отменяет обязательную проверку основания, статуса карты и остальных предусловий операции. Инициирование запроса остаётся полномочием ЦО и не требует права исполнения архивного списания. Механизм назначения/отзыва отдельного права и субъект, который им управляет, будут определены при детализации модели полномочий. Реализация не разрешена.


## Решение от 2026-11-22 — отмена запроса на списание медицинской карты

Владелец продукта утвердил, что сотрудник ЦО вправе отменить запрос на списание до фактического исполнения списания архивом. После исполнения списания отмена запроса недоступна: статус карты «Списана» не может быть отменён через эту операцию. Ошибочное списание после исполнения требует отдельной контролируемой процедуры исправления с обязательной фиксацией причины и сохранением первоначального события списания и всей истории. Отмена запроса фиксируется отдельным событием с автором и временем; исходный запрос и его причина не удаляются и не переписываются. Технические предусловия, допустимые статусы запроса и точный механизм исправления ошибочного списания будут детализированы отдельно. Реализация не разрешена.


## Решение от 2026-11-22 — отображение результата списания в ЦО

Владелец продукта утвердил, что после исполнения списания архивом результат должен быть доступен сотрудникам ЦО в системе в контексте соответствующего запроса. Отображаются факт и статус исполнения, дата и время списания, а также ФИО сотрудника архива, выполнившего операцию (с сохранением исторического снимка имени). Запрос и его история остаются доступными в пределах действующих прав. Это обязательное отображение результата; отдельный канал push/email/SMS-уведомлений этим решением не вводится. Реализация не разрешена.


## Решение от 2026-11-22 — отказ архива в исполнении запроса на списание

Владелец продукта утвердил, что архив может отказать в исполнении запроса ЦО на списание медицинской карты, если проверка не подтвердила основания или выявила препятствия для операции. Причина отказа обязательна и выбирается из закрытого справочника; дополнительный комментарий допускается, но необязателен. В ЦО в контексте исходного запроса отображаются статус отказа, выбранная причина, комментарий (если указан), дата и время решения, а также ФИО сотрудника архива, принявшего решение (исторический снимок имени). Отказ не равен отмене запроса сотрудником ЦО и не является списанием карты: запись карты, её статус и история сохраняются без изменений. Решение архива фиксируется отдельным неизменяемым событием; исходный запрос и его причина не удаляются и не переписываются. Закрытый перечень причин отказа будет определён отдельно. Реализация не разрешена.


## Решение от 2026-11-22 — повторный запрос на списание после отказа архива

Владелец продукта утвердил, что ЦО может повторно подать запрос на списание той же медицинской карты после отказа архива, но только после устранения причины предыдущего отказа. Повторный запрос создаётся как новая самостоятельная запись, с обязательной причиной из закрытого справочника и собственной историей; предыдущий запрос, причина отказа и событие решения архива сохраняются без изменений. Повторная подача не снимает отказ и не меняет статус карты автоматически: новый запрос проходит обычную проверку архива и исполняется только при наличии отдельного права у активного сотрудника архива и выполнении всех предусловий списания. Реализация не разрешена.

## 2026-11-22 — дополнительные инварианты массовых операций и item-level выдачи archive → CO

Эти требования supersede более ранние DB-19…DB-22 и другие формулировки, если те требуют полной приёмки всех карт P-09 одним действием.

| ID | Инвариант | Механизм PostgreSQL | Транзакционная граница | Негативная проверка |
|---|---|---|---|---|
| DB-62 | Каждая карта заявки ЦО имеет независимое состояние выдачи и получения; невыданная карта не считается переданной | Typed command guards, FK и deferred consistency trigger для item/custody/shipment | Одна команда по позиции; bulk receipt — одна транзакция | Нельзя считать карту полученной без фактического receipt; изменение одной позиции не меняет соседние |
| DB-63 | Custody карты остаётся у архива после фиксации выдачи до фактического подтверждения получения ЦО | Защищённая команда item handover/receipt и deferred consistency guard | Изменение item outcome, shipment/custody и DomainEvent одной транзакцией | Выдача без receipt не переводит custody в ЦО; преждевременный receipt отклоняется |
| DB-64 | ЦО может подтвердить несколько фактически полученных карт атомарно; каждая карта получает отдельную запись actor/time | Закрытая DB command, блокировка выбранных items/cards в стабильном порядке, append-only DomainEvents | Весь выбранный набор — один commit | Если хотя бы одна выбранная карта не допускает receipt, не подтверждается ни одна |
| DB-65 | Архив может фиксировать not_found по позиции с обязательным reason code; карта не списывается и не получает фиктивное новое местонахождение | CHECK/command allowlist + FK на справочник причин + append-only event | Одна позиция | Отсутствие причины отклоняется; изменение card status/location при not_found отклоняется |
| DB-66 | Отмена P-09 до начала выдачи закрывает заявку целиком; после частичной выдачи закрывает только невыданную часть | Parent/item state guards и атомарная typed command | Parent и все затронутые items блокируются одним commit | Уже выданные/полученные позиции не отменяются; нельзя отменить только часть до начала выдачи |
| DB-67 | Массовое размещение нескольких принятых коробок на одной активной полке атомарно | Защищённая bulk box_place command, проверка одной target shelf и блокировка коробок/полки | Все выбранные коробки и target shelf — один commit | Неподходящая коробка или неактивная/чужая полка откатывает весь набор |
| DB-68 | Массовое перемещение коробок с одной или нескольких полок на одну целевую полку атомарно | Защищённая bulk box_move command, consistency guards и стабильный lock order | Все исходные placements, коробки и target shelf — один commit | Если одна коробка не может быть перемещена, ни одна не перемещается |
| DB-69 | Массовое извлечение карт из нескольких коробок не допускается | Command allowlist/guard | Одна команда извлечения одной карты или отдельный допустимый сценарий | Bulk extraction request отклоняется |
| DB-70 | Приёмка каждой коробки обычной отправки не проверяет количество карт внутри и не требует проверки пломб/упаковки | Контракт shipment_receive_box не содержит count/seal/packaging predicates | Одна коробка из исходного manifest, её custody и receipt event — одна транзакция | Отсутствие счёта карт/осмотра упаковки не блокирует receipt; остальные коробки shipment остаются ожидающими |

Bulk commands должны быть идемпотентны в рамках общего command ledger. Перечень reason codes для not_found, точные item state names и API error envelope — техническая детализация; она не должна менять утверждённые выше бизнес-правила. Реализация не разрешена.

## 2026-11-22 — superseding box-level receipt invariants

Более поздние утверждённые решения о поштучной приёмке коробок supersede любые прежние формулировки о полном receipt обычного box shipment одной операцией.

- Каждая коробка из manifest принимается отдельно; receipt одной коробки не ожидает приёмки остальных.
- Custody отдельной коробки переходит архиву в транзакции её индивидуального receipt.
- Shipment остаётся открытым, пока не обработаны все коробки исходного manifest и расхождения.
- Принятую коробку можно отдельно разместить на активной полке до завершения всего shipment.
- Поздняя коробка принимается отдельным receipt в рамках того же shipment.
- Коробка вне manifest фиксируется как расхождение; нельзя автоматически включить её в manifest или менять её custody/location.
- Счётчик отправления вычисляется по исходному manifest: total, individually received, awaiting receipt; размещение не влияет на receipt count.
- Receipt не проверяет число карт внутри коробки, пломбы или упаковку.

DB-70 следует читать как запрет count/seal/package inspection, а не как предписание принимать весь shipment целиком. DB command contract должен быть box-level, с независимым actor/time и DomainEvent для каждой коробки.

## 2026-11-22 — DB-71…DB-73: транспортные партии P-09

| ID | Инвариант | Механизм PostgreSQL | Транзакционная граница | Негативная проверка |
|---|---|---|---|---|
| DB-71 | Одна P-09 parent может иметь 0..N fulfillment batches; каждая batch принадлежит ровно одному parent, а manifest содержит только items этой заявки. Один item одновременно связан максимум с одной текущей активной/выданной batch | `shipments.request_id` nullable FK + non-unique index; составной UNIQUE `(shipments.id, request_id)`; item composite FK `(fulfillment_batch_id, request_id)`; `shipment_cards.request_item_id` и deferred manifest consistency trigger | Создание партии, subset manifest, item links, courier FK/snapshot и DomainEvent — одна транзакция | Второй batch того же parent разрешён; batch с item чужого parent, повторное назначение item или manifest mismatch отклоняется |
| DB-72 | Batch state является точной проекцией item-level receipt; custody остаётся у архива до receipt конкретной карты. `received` допустим только при receipt всех items партии, но не завершает parent автоматически | Batch/item state guards, FK/manifest integrity, deferred custody/location consistency trigger | Item receipt, custody/location, batch projection, parent completion при достижении terminal всех items и DomainEvents — одна транзакция | Handover не переводит custody; receipt одной карты не меняет соседние items; batch не становится received при непринятых картах; parent не completed при pending/extracted/issued_pending_receipt |
| DB-73 | Handover партии и отмена невыданного остатка сериализуются; позднее обнаруженная карта может быть восстановлена и выдана следующей batch той же P-09, если именно этот item ещё не выдан/не получен | Общий lock order parent → items/cards → batches/manifests; повторная проверка item assignment/state; idempotency ledger | Handover одной batch и cancel remainder каждый выполняются атомарно под блокировками parent/items; restore item — отдельная транзакция | Гонка не отменяет уже выданную карту и не выдаёт отменённую позицию; наличие другой batch не блокирует restore невыданного not_found item; двойная выдача невозможна |

