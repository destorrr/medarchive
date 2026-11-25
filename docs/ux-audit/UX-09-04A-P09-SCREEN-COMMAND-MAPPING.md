# UX-09.4a — screen/action mapping P-09
2026-11-25. Проектная спецификация, не реализация. Источники: UX-06-DB-COMMAND-CONTRACTS.md §§1,2; UX-06-DB-COMMAND-MAPPING.md; UX-07-UNIFIED-SCREEN-MAP-AND-NAVIGATION.md; UX-08-APPROVED-BASELINE.md. При расхождении ранних описаний применять поздние утверждённые P-09 правила. Имена команд проектные.

| Экран/действие | Команда | Actor/scope | Предусловие и результат | Событие/проверка |
|---|---|---|---|---|
| C02 создать | request_create_draft | ЦО | доступные карты, без конфликтующих резервов; draft | проверить duplicate/reservation |
| C02 изменить | request_edit_draft | автор ЦО | draft; состав и резервы атомарно | archive_request.item_added/item_removed_by_central_office; race submit |
| C02 отправить | request_submit | автор ЦО | непустой draft, повторная проверка; submitted | archive_request.submitted; нет физического перемещения |
| A08 начать | request_start_processing | архив, scope заявки | submitted; атомарное извлечение всех карт, extracted, processing; закрытие membership и цикла/placement последней карты по P09 | processing_started/item_extracted; rollback всего состава |
| A09 исключить | request_remove_item | активный архив, scope | processing до shipment draft; возврат в коробку, removed_by_archive, резерв сохраняется | item_removed_by_archive; комментарий при первой фиксации |
| A09 не найдена | request_mark_not_found | активный архив, scope | extracted до draft; not_found, reason code, необязательный комментарий; не создавать placement | item_marked_not_found; резерв сохраняется |
| A09 исправить | request_restore_item_to_extracted | активный архив, scope | исключение до draft, фактическое membership; извлечение | item_result_corrected; история неизменна |
| A09 все исключены | автоматический переход в команде результата | архив | нет extracted, все исключены; completed без shipment | archive_request.completed; не отдельная кнопка |
| A10 подготовить | request_create_fulfillment_draft | активный архив, scope | все позиции обработаны, >=1 extracted, активный курьер; единственный draft со всеми extracted | shipment.draft_created; запрет частичного/второго manifest |
| A10 курьер | shipment_change_fulfillment_courier | автор draft | draft, активный новый курьер; snapshot, manifest неизменен | не добавлять отдельную историю назначений |
| A10 передать | request_handover | активный текущий автор draft | полный immutable manifest; in_transit, все issued_pending_receipt, custody курьера | shipment.handed_over; race cancel |
| C04 получить | request_confirm_item_receipt | ЦО, scope | реально полученная карта manifest; custody ЦО | item receipt, проверить точное имя события по каталогу |
| C04 получить выбранные | request_confirm_bulk_receipt | ЦО, scope | атомарный набор реально полученных карт | rollback при недопустимой позиции; completion после последней |
| C02 отменить | request_cancel | автор ЦО | draft, без причины | archive_request.cancelled |
| A08/A09/A10 отменить | request_cancel | уполномоченный архив | submitted/processing до handover, причина обязательна; возврат extracted, отмена связанного draft, сохранить исключения | archive_request.cancelled, shipment.cancelled при наличии; rollback если возврат невозможен |

## Read models (предлагаемые, не URL)
RequestRegistry: id, created_at, status, item counts, allowed actions; RequestDetail: parent, items, current result, permitted card fields, shipment reference, timeline; FulfillmentDetail: manifest, courier snapshot, handover/receipt states; CardReceiptSelection: eligible item IDs, current state, received count. Все scope проверяются сервером; не раскрывать PII по одному факту участия в заявке. Счётчики и drilldown используют одинаковый фильтр. Фильтры/пагинация в URL; точные параметры и timezone остаются открыты.

## HTTP adapter (предложение)
GET только чтение; POST для команды. Actor из аутентифицированной сессии, не из доверенного поля формы; command_id и correlation_id, стабильный command_id для retry того же намерения. DB envelope versioned JSONB согласно UX-06 §1. success/already_applied — обновить read model; not_found/forbidden — безопасный отказ без раскрытия объекта; invalid_state/conflict — обновить состояние, не повторять вслепую; validation_error — показать ошибки полей, сохранить ввод. Предлагаемые HTTP mappings 200/303, 404/403 (с учётом anti-enumeration), 409, 422 соответственно; точный API контракт ещё не утверждён. SQL диагностику не отдавать. Replay повторно проверяет доступ по UX-06 §1.4.

## Транзакции и тесты
Общий envelope и lock order UX-06: principal/membership, P09 parent → shipment (если есть) → items → cards/memberships/boxes; повторная проверка прав после lock. DomainEvent и ledger в той же транзакции. Тесты: draft edit vs submit; start rollback; exception/restore vs draft creation; cancel vs handover; duplicate handover; receipt vs duplicate receipt; bulk rollback; deactivation vs command; replay после отзыва доступа; scope direct URL; PII; UI refresh после конфликта. Parent processing до получения последней карты; частичное получение допустимо, частичная отправка нет. Не создавать новую кнопку «Найдена». Не переносить demo simulation.
Открыто: точные receipt event IDs, API payload schemas, полная матрица остальных рабочих мест, timezone/pagination, DB migration и тесты. Production код не менялся, тесты не запускались.
