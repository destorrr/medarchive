# UX-09.4C — обычные коробочные отправления
2026-11-25. Проектная спецификация. Источники UX-06-INVARIANTS-DB-TEST-MATRIX DB-33–40,46; UX-06-TECHNICAL-DATA-MODEL; UX-08-BRANCH-V6-BASELINE-AND-HANDOFF. Маршруты branch→archive, central_office→archive. P-09 отдельно в UX-09-04A.

| UI/action | Предлагаемый use case ID | Actor/scope | Результат |
|---|---|---|---|
| Выбор свободных непустых коробок | ShipmentEligibleBoxes read model | branch/CO своей локации | Только допустимые коробки; повторная проверка при записи |
| Назначить курьера группе | локальная подготовка | отправитель | Выбор активного курьера; до сохранения не резервирует коробки |
| Создать черновики | shipment_create_draft | активный отправитель | Один draft на группу, непустой ShipmentBox manifest, courier FK/snapshot, автор, номер; несколько независимых drafts разрешены |
| Обновить черновик | shipment_edit_draft | текущий активный автор | Изменить состав/курьера только до handover; атомарно снять/добавить резервы |
| Отменить черновик | shipment_cancel_draft | текущий активный автор | cancelled, освободить коробки; не менять custody/membership |
| Передать курьеру | shipment_handover | текущий активный автор | draft→in_transit; весь manifest, custody курьера для коробок и карт, immutable snapshot |
| Переназначить автора | shipment_reassign_draft_author | admin | Только после деактивации прежнего автора, до handover, активный новый автор той же отправляющей локации; история исходного создания неизменна |
| Реестр/детали | ShipmentRegistry/ShipmentDetail | scoped read | Номер, маршрут, состояние, курьер snapshot, коробки, разрешённая история; без patient PII |

Имена use cases не являются подтверждением существования SQL функций. Группировка до сохранения — инженерное предложение; семантика batch create (общий rollback либо независимые результаты) требует технической фиксации, не выдавать частичный успех за полный. Не вводить лимит черновиков. Не создавать ShipmentCard для содержимого обычных коробок. Не менять BoxCycle/membership при handover. Состав коробки заблокирован при активном draft и в пути. После отмены draft коробка вновь доступна при выполнении прочих условий.

## Courier and history
Активность курьера проверяется при новом назначении; деактивация уже назначенного не отменяет назначение и не препятствует handover. Snapshot берётся сервером из справочника; после handover неизменяем. История прежних назначений и редактирования состава не ведётся; DB-35 допускает атомарный результат/событие, но не вводить пользовательскую историю редактирования. История создания, handover (окончательный manifest snapshot), полной приёмки (receipt snapshot), фактический actor/time. Отмена draft не видна архиву. Переназначение автора — append-only audit, без изменения manifest/courier. Точные event IDs и разделение security/domain audit сверить с каталогом.

## Transaction and API
Actor из сессии; lock/recheck principal/membership по DB-46, shipment и коробки в стабильном порядке; unique active reservation, повторная проверка nonempty, scope, custody, status, courier. command ledger и события атомарны. Retry с тем же command_id возвращает сохранённый результат при действующем доступе. Handover/cancel/edit/reassign сериализуются; handover только один раз. Не отслеживать GPS курьера. Receipt по одной коробке переводит custody и location её и карт архиву; не размещает на полке. Детальный receipt/discrepancy mapping следующий блок.

## Tests
Несколько drafts одного автора; пересекающиеся группы; пустой/чужой/занятый box; concurrent reserve; edit vs handover; cancel vs handover; повтор handover; deactivation vs command; reassignment active/deactivated/wrong scope; courier deactivated after assignment; immutable manifest after handover; no ShipmentCard; no PII in manifest; rollback on event failure; independent draft handover; batch failure UI. Код, миграции, HTML не изменялись; тесты не запускались.
