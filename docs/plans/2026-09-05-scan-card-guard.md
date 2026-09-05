# Дизайн и план: Guard «карта в другой коробке» + чистка остатков

Дата: 2026-09-05
Проект: МедАрхив
Концепт: `docs/plans/2026-09-05-card-identity-qr-design.md` (§4, ответ 5)
Решение пользователя (2026-09-05): guard = **блок + кнопка «Перенести»**.

## Проблема

`BoxService.scan_card` добавляет карту в коробку без проверки, что карта уже числится в
другой активной коробке (`card_in_other_boxes` используется только в `remove_card`).
Итог — «остатки»: одна карта в N активных коробках (демо: Иванов — в 10). В демо-БД сейчас
6 карт с остатками.

## Задача 1 — Guard в scan_card + «Перенести»

### Сервис (`BoxService`)

1. Новое исключение `CardInAnotherBoxError(card, other_boxes)`:
   `other_boxes = [{"box_id", "box_number", "status"}]` (активные, кроме текущей).
2. `scan_card(box_id, full_name)`:
   - карта найдена/создана как сейчас;
   - если карта уже в **этой** коробке → `DuplicateCardError` (без изменений);
   - если карта числится в **других** активных коробках → `CardInAnotherBoxError`
     (НЕ добавляем, НЕ меняем статус);
   - иначе — как сейчас.
3. Новый `transfer_card(box_id, card_id)` — «перенести в текущую коробку»:
   - для каждой другой активной коробки карты: `remove_card(other_box_id, card_id)`
     (мягкое удаление + лог `unpacked`, статус вернётся, если коробок не осталось);
   - `add_card(box_id, card_id)` + статус `in_box` + лог `packed`;
   - guard: карта не числится в других коробках или уже в текущей → `ValueError`.
   Нужен репозиторный метод `card_other_boxes(card_id, exclude_box_id) -> list[dict]`
   (номер+статус коробок), рядом с `card_in_other_boxes`.

### Роутер/шаблон (packing)

- `POST /packing/scan-card`: ловит `CardInAnotherBoxError` → рендер фрагмента с
  предупреждением «Карта „…“ уже числится в B-0003 (Размещена). Перенести в эту коробку?»
  + форма-кнопка **«Перенести»** (hx-post на `/packing/transfer-card`, hidden `card_id`);
  карта в список содержимого НЕ попадает.
- Новый `POST /packing/transfer-card` (card_id) → `service.transfer_card` → рендер обычного
  фрагмента (карта в списке). Ошибки — флеш.
- `packing_fragment.html`: блок предупреждения (переменная `transfer` =
  {card_id, card_name, boxes:[{box_number, status_label}]}), стиль — `.flash` warning + кнопка.
- Контекст фрагмента: `_fragment_ctx` дополнить `transfer=None`.

### Тесты

- service: scan_card с картой в другой активной коробке → `CardInAnotherBoxError`, в текущую
  коробку не добавлена, статус не тронут; обычный скан без изменений; duplicate в той же —
  `DuplicateCardError`; `transfer_card` переносит (старая: removed_at, новая: добавлена,
  статус in_box, лог); transfer без «других коробок» → ValueError.
- router (packing): скан «занятой» карты → фрагмент содержит «уже числится» и кнопку
  «Перенести», в содержимом карты нет; POST transfer-card → карта в содержимом, из старой
  коробки снята (БД); регресс: обычный скан/дубль-сообщение не изменились.
- Полный `pytest -q` без регрессий (в т.ч. stage3 smoke: там карта создаётся заново — guard не мешает).

## Задача 2 — Разовый скрипт чистки остатков

`scripts/cleanup_stale_box_cards.py` (standalone sqlite3, не зависит от FastAPI):

- Аргументы: `--db <path>` (по умолчанию env `MEDARCHIVE_DB_PATH` или `./medarchive.db`),
  `--apply` (без него — **dry-run preview**), `--backup` (по умолчанию при `--apply`: копия
  `medarchive.db.bak-<ts>` через `sqlite3.Connection.backup` до записи).
- Логика на карту с >1 активной коробкой (та же, что в search_service):
  - актуальная коробка: маппинг статуса карты → допустимые статусы коробок
    (in_box→packing/ready; in_transit→in_transit; in_archive→received; placed/pending_return→placed),
    свежайшая по `(placed_at, added_at, id)` DESC; нет подходящей → свежайшая активная;
  - остальные активные — кандидаты на снятие.
- Dry-run: таблица-отчёт: карта | статус | оставляем (номер, статус, место) | снимаем (номера).
- Apply (в транзакции): `UPDATE box_cards SET removed_at = datetime('now','localtime')`
  для кандидатов; если после снятия у карты не осталось коробок — статус `in_branch`
  (не должно случиться: актуальная остаётся); бэкап до записи.
- Никогда не снимает ВСЕ коробки карты (всегда сохраняет актуальную).
- Тест: `tests/test_cleanup_script.py` на временной БД: seed карты с остатками →
  dry-run ничего не меняет; apply снимает только остатки, сохраняет актуальную; бэкап создаётся.

## Коммиты

- `feat: packing — guard «карта в другой коробке» + перенос в текущую (CardInAnotherBoxError)`
- `feat(scripts): cleanup_stale_box_cards.py — чистка остатков (dry-run + backup + apply)`
- docs-правка концепта (§4) при необходимости.

## Пуш

НЕ пушить без явного подтверждения пользователя (правило 2026-09-05).
После реализации: dry-run preview по демо-БД показать пользователю; apply — только после его «ок».
