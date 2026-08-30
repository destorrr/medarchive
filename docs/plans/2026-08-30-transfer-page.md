# План: редизайн страницы /transfer (3 задачи, TDD)

Дата: 2026-08-30. Проект: МедАрхив. Дизайн: `docs/plans/2026-08-30-transfer-page-design.md` (утверждён).

## Task 1 — Единые стили форм в base.html

**Тест (RED):** в `tests/routers/test_transfer.py` добавить `test_base_has_form_styles`:
GET `/transfer` (под логином) → `r.text` содержит `.form-group { margin-bottom: 16px` и
`.form-group select { width: 100%`. Также прогнать существующие тесты login-страницы — не должны падать.

**Имплементация (GREEN):**
- В `<style>` `src/medarchive/templates/base.html` добавить:
  `.form-group { margin-bottom: 16px; }`,
  `.form-group label { display: block; margin-bottom: 6px; font-weight: 600; color: #1a1a2e; }`,
  `.form-group select { width: 100%; padding: 8px 12px; border: 1px solid #ddd; border-radius: 6px; font: inherit; background: #fff; color: #1a1a2e; }`,
  `.form-group select:focus { outline: 2px solid #e94560; outline-offset: -1px; border-color: #e94560; }`
- Убрать дублирующий `<style>` блок из `login.html` (стили форм теперь в base).
- Проверить рендер login и transfer (визуально/тестами).

**Commit:** `docs+styles: единые стили форм в base.html, чистка login.html`

## Task 2 — Шаблон transfer.html: убрать «Коробка: {id}», подготовить структуру dropdown

**Тест (RED):** в `tests/routers/test_transfer.py`:
- `test_transfer_page_no_raw_id`: GET `/transfer` → `"Коробка:</strong>"` отсутствует; `"<strong>Коробка:"` отсутствует;
- `test_transfer_preselect_box`: GET `/transfer?box_id=10` → `'value="10"'` присутствует в `select[name=box_id]`
  и option B-0010 имеет `selected`;
- `test_transfer_form_fields`: есть `select[name=box_id]` и `select[name=courier_id]`, форма `action="/transfer/confirm"`.

**Имплементация (GREEN):** в `transfer.html`:
- удалить `<p><strong>Коробка:</strong> {{ selected_box_id }}</p>`;
- select'ам добавить класс `dd-select` (нативный select остаётся в DOM — источник значений для submit);
- обернуть каждый `.form-group` в `<div class="dd">` (JS построит toggle+list внутри).

**Commit:** `feat: /transfer — убрана строка с raw id, каркас кастомного dropdown`

## Task 3 — JS dropdown (block scripts в transfer.html)

**Тест (RED):** `test_transfer_has_dropdown_script`: GET `/transfer` → `r.text` содержит
`document.documentElement.classList.add('js')` и `dd-list` (инициализация dropdown).

**Имплементация (GREEN):** в `{% block scripts %}`:
- `document.documentElement.classList.add('js')` (скрытие нативного select только при JS);
- для каждого `.dd`: построить `.dd-toggle` (текст выбранного + стрелка-шеврон SVG) и `.dd-list`
  (пункты из option'ов; чек-кружок у выбранного); клик по пункту → `select.value = option.value`,
  обновить UI, `aria-expanded/aria-selected`, закрыть; клик вне/Esc — закрыть; стрелка поворачивается.
- CSS dropdown (в `<style>` transfer.html или в base — по дизайн-доку в transfer.html): `.dd-toggle`
  (включая `position: relative`, стрелка `right: 17px; top: 50%; translateY(-50%)`, при open —
  `translateY(-50%) rotate(180deg)`), `.dd-list` (absolute, radius 8px, тень, max-height+scroll),
  `.dd-option` hover/selected, `.js .dd-select` визуально скрыт (но доступен для submit).

**Commit:** `feat: /transfer — кастомный dropdown (прогрессивное улучшение поверх select)`

## Проверка после всех задач

1. `pytest -q` — все зелёные (было 210, ожидается ~213+).
2. Сервер: перезапуск uvicorn, вручную открыть `/transfer` и `/transfer?box_id=10` — строка «Коробка: 10» отсутствует,
   dropdown открывается/закрывается, стрелка над кружками, выбор сохраняется при submit.
3. Визуально: headless Chrome скриншот + screenshot-analyzer (цвета/зоны).
4. Без JS (отключить JS в браузере) — нативный select работает, форма отправляется.
