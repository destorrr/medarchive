# Дизайн: /receive — единый стиль с /packing

Дата: 2026-08-31
Статус: утверждён (макет `receive_redesign_mockup.html` одобрен пользователем)

## Проблема

Страница `/receive` (роль Архив) выбивается из общего стиля:
- input формы — нативный, без стилей (в `base.html` стилизован только `select`)
- результат скана — HTML-фрагмент из роутера: голая таблица без карточки и отступов, `h3` без стиля
- таблица «Ожидают приёмки» — без единого стиля строк
- статусы карт выводятся сырыми значениями enum (`in_transit` и т.п.)

## Цель

Привести `/receive` к визуальному стандарту `/packing` (карточки `.card`, крупный input сканера, таблицы с padding 8px и разделителями, бейджи).

## Решения

### 1. Форма сканера (receive.html)
- Карточка `.card`
- `label` «Номер коробки» (font-weight 600, цвет #1a1a2e) — как в packing
- `input` — стиль из packing: `width:100%; padding:10px; border:2px solid #ddd; border-radius:6px; font-size:16px; font:inherit`
- focus: `outline:none; border-color:#e94560`
- Кнопка «Проверить» — `.btn .btn-primary`

### 2. Результат скана (фрагмент в archive.py `scan_box`)
- Обёртка `.card`
- `h3` «Состав коробки {номер}» (как h3 в packing: font-size 15px, margin-bottom 12px)
- Таблица: `thead tr { border-bottom:2px solid #eee; text-align:left }`, `th { padding:8px }`, `tbody tr { border-bottom:1px solid #f0f0f0 }`, `td { padding:8px }`, колонка `#` — цвет #888
- Статус — бейдж `<span class="badge badge-{status}">Русская подпись</span>`
- Кнопка «Подтвердить приёмку» — `.btn .btn-primary` (с отступом сверху)

### 3. «Ожидают приёмки» (receive.html)
- Карточка `.card`, `h3` «⏳ Ожидают приёмки»
- Таблица: та же стилистика; номер коробки — font-weight 600

### 4. Русские подписи статусов карт
В `src/medarchive/domain/statuses.py` — словарь `CARD_STATUS_LABELS: dict[CardStatus, str]`:

| Статус | Подпись | Бейдж |
|--------|---------|-------|
| in_branch | В филиале | серый |
| in_box | В коробке | серый |
| in_transit | В пути | жёлтый |
| in_archive | В архиве | зелёный |
| placed | Размещена | зелёный |
| pending_return | Возврат | красный |

### 5. CSS-классы бейджей (base.html)
Добавить в `<style>` (глобально, не ломает существующие страницы — только новые классы):
- `.badge` — базовый: inline-block, radius 12px, padding 2px 10px, font-size 12px, font-weight 600
- `.badge-in_branch`, `.badge-in_box` — серый (#eef1f6 / #1a1a2e)
- `.badge-in_transit` — жёлтый (#fff3cd / #8a6d1a)
- `.badge-in_archive`, `.badge-placed` — зелёный (#e8f5e9 / #2e7d32)
- `.badge-pending_return` — красный (#fdecea / #c0392b)

## Вне скоупа
- `/packing`, `/transfer`, `/my-shipments` и другие страницы НЕ меняются (принятые пункты Этапа 1 не трогаем)
- Семантика и логика приёмки не меняется
- Современный редизайн всего приложения — отдельный этап после п.1.12

## Тесты
- `tests/domain/test_statuses.py`: `CARD_STATUS_LABELS` содержит все 6 статусов с русскими подписями
- `tests/routers/test_archive.py`:
  - scan-фрагмент содержит «Состав коробки», badge-класс `badge-in_transit`, русскую подпись «В пути», кнопку «Подтвердить приёмку»
  - страница `/receive` содержит «Ожидают приёмки»
