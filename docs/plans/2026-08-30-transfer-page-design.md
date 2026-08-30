# Дизайн: редизайн страницы /transfer (передача курьеру)

Дата: 2026-08-30. Проект: МедАрхив. Статус: утверждён пользователем (макет v2, вариант A).

## Контекст

Приёмочное тестирование Этапа 1, п.1.6 («Закрой первую коробку → статус ready»). После закрытия коробки 10
пользователь попал на `/transfer?box_id=10` и зафиксировал дефекты UI:

1. **Избыточная строка** «Коробка: 10» — показывает сырой ID, дублирует select «Коробка» (который и так предвыбран).
2. **Строки слишком близко** — `.form-group` не стилизован в `base.html` (стили форм есть только в `login.html`), отступы 0.
3. **Выпадающие меню разного размера/расположения** — нативные select растягиваются по содержимому, нет `width: 100%`.
4. **Открытый нативный dropdown угловатый** (системное окно браузера с системной подсветкой выбранного пункта —
   подтверждено пиксельным анализом скриншота: сине-фиолетовая зона ~8К пикселей). CSS нативный option-список не стилизует.

## Утверждённый макет

`~/.openclaw/canvas/documents/medarchive/transfer_redesign_mockup_v2.html` (вариант A — без инфо-строки;
стрелка-шеврон выровнена по вертикали с чек-кружками списка: центр 23px от правого края).

## Решения

### 1. Убрать строку «Коробка: {id}»
- Удалить `<p><strong>Коробка:</strong> {{ selected_box_id }}</p>` из `transfer.html`.
- Предвыбор коробки при `?box_id=N` сохраняется через `selected` у `<option>` — контекст перехода виден в самом dropdown.

### 2. Единые стили форм — в `base.html`
- Добавить в `<style>` base.html общие стили:
  - `.form-group { margin-bottom: 16px; }`
  - `.form-group label { display: block; margin-bottom: 6px; font-weight: 600; color: #1a1a2e; }`
  - `.form-group select { width: 100%; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; background: #fff; }`
- Убрать дублирующий `<style>` блок из `login.html` (стили переезжают в base — единый источник).
- Это улучшает все формы проекта, не только /transfer (унификация, DRY).

### 3. Кастомный dropdown — прогрессивное улучшение поверх нативного select
- Нативный `<select name="box_id|courier_id">` **остаётся в DOM** (важно для submit и a11y), но скрывается
  визуально ТОЛЬКО при работающем JS (`document.documentElement.classList.add('js')`; CSS `.js .dd-select { … }`).
  Без JS форма работает нативным select — деградация безопасная.
- JS (в `{% block scripts %}` transfer.html) строит из option'ов:
  - `.dd-toggle` — кнопка-триггер в виде поля: текст выбранного, SVG-стрелка-шеврон справа
    (absolute, `right: 17px; top: 50%; translateY(-50%)` — центр на 23px, как у чек-кружков списка);
  - `.dd-list` — открытый список: absolute, белый, `border-radius: 8px`, тень, `max-height` + scroll,
    пункты с hover (`#f0f0f0`), выбранный — `#eef4fb` + жирный + чек-кружок `#e94560`;
  - поворот стрелки при открытии (`rotate(180deg)` с сохранением `translateY`).
- Поведение: клик по триггеру — open/close; клик по пункту — выбор + синхронизация `select.value`; клик вне — close; `Esc` — close.
- A11y: `role="listbox"/"option"`, `aria-expanded` на триггере, `aria-selected` на пунктах.
- Стили dropdown — в `base.html` (компонент переиспользуем) или в `transfer.html`? **Решение: в `transfer.html`**
  (`{% block scripts %}` + `<style>` в шаблоне), т.к. пока это единственное место использования; перенос в base —
  при втором потребителе (YAGNI).

### 4. Роутер не меняется
- Логика GET/POST `/transfer` остаётся прежней (`selected_box_id` в контексте — только для предвыбора option).
- Форма по-прежнему шлёт `box_id` + `courier_id` — `/transfer/confirm` не трогаем.

## Scope

**В scope:** `base.html` (стили форм), `login.html` (чистка дубля), `transfer.html` (строка, структура, JS dropdown),
тесты `tests/routers/test_transfer.py` (+ при необходимости новые).

**Вне scope:** `/transfer/confirm` логика, shipment-сервисы, другие страницы, доступность-доработки других форм,
перенос dropdown в отдельный JS-файл/статику (в проекте нет static-пайплайна — инлайн в block scripts).

## Тестирование

- Роут-тесты (pytest, httpx ASGI):
  - `/transfer` без query: 200, нет «Коробка:», есть `select[name=box_id]` и `select[name=courier_id]`;
  - `/transfer?box_id=10`: option B-0010 имеет `selected`;
  - `/transfer/confirm` — без регрессий (уже покрыт);
  - базовые стили: ответ `/transfer` содержит `.form-group` правила (base.html).
- JS: структурная проверка (в HTML присутствует `block scripts` с инициализацией dropdown);
  визуальная проверка — headless Chrome + screenshot-analyzer (вручную на этапе review).
- Полный прогон: `pytest -q` — все тесты зелёные, без регрессий.
