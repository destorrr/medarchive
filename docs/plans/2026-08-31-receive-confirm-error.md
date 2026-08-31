# План: ошибка «Подтвердить приёмку» — вариант C (htmx) + русские статусы

Дата: 2026-08-31
Дизайн: `docs/plans/2026-08-31-receive-confirm-error-design.md`
Метод: TDD (красный тест → реализация → зелёный → коммит), subagent-driven + review-gates

## Task 1 — BOX_STATUS_LABELS

- **RED** `tests/domain/test_statuses.py`: тест `BOX_STATUS_LABELS` покрывает все 6 статусов `BoxStatus` с подписями: packing=«Упаковывается», ready=«Готова», in_transit=«В пути», received=«Принята», placed=«Размещена», lost=«Потеряна».
- **GREEN** `src/medarchive/domain/statuses.py`: добавить словарь `BOX_STATUS_LABELS` (рядом с `CARD_STATUS_LABELS`).
- **Verify**: `pytest tests/domain/test_statuses.py -q`
- **Commit**: `feat: русские подписи статусов коробок (BOX_STATUS_LABELS)`

## Task 2 — русские статусы в сообщениях receive_service

- **RED** `tests/services/test_receive_service.py`:
  - `receive_box` с коробкой в статусе `ready` → `ValueError` с текстом `в статусе 'В пути', сейчас 'Готова'`
  - `place_box` с коробкой в статусе `in_transit` → `ValueError` с текстом `в статусе 'Принята', сейчас 'В пути'`
- **GREEN** `src/medarchive/services/receive_service.py`: в обоих сообщениях заменить `'...'` (сырые значения) на `BOX_STATUS_LABELS[...]` (и ожидаемый, и текущий статус).
- **Verify**: `pytest tests/services/test_receive_service.py -q`
- **Commit**: `fix: статусы коробок в сообщениях приёмки — по-русски`

## Task 3 — вариант C: htmx + HX-Redirect в confirm_receive

- **RED** `tests/routers/test_archive.py`:
  - `test_scan_fragment_confirm_is_htmx`: scan-фрагмент содержит `hx-post="/receive/confirm"` и `hx-target="#scan-result"`; НЕ содержит `method="post"`.
  - `test_confirm_receive_success_sends_hx_redirect`: POST `/receive/confirm` с валидной коробкой (id=10, in_transit) → `status_code == 200` и `headers["HX-Redirect"] == "/placement?box_id=10"` (сейчас: 303 + Location → RED).
  - `test_confirm_receive_error_returns_fragment`: коробка в статусе `received` → `status_code == 200`, в теле `class="flash"` и русский текст `в статусе 'В пути', сейчас 'Принята'` (сейчас: 303 на /receive?error= → RED).
- **GREEN** `src/medarchive/routers/archive.py`:
  - `scan_box`: форма → `<form hx-post="/receive/confirm" hx-target="#scan-result">`
  - `confirm_receive`: успех → `Response(status_code=200, headers={"HX-Redirect": f"/placement?box_id={box_id}"})`; ошибка ValueError → `HTMLResponse(flash-фрагмент, status_code=200)`.
- **Verify**: `pytest tests/routers/test_archive.py -q` + полный `pytest -q`
- **Commit**: `fix: ошибка приёмки показывается в блоке скана (htmx), успех — HX-Redirect на /placement`

## Review-gates (после каждой задачи)

- Spec-reviewer: код соответствует дизайн-доку (вариант C, русские статусы, HX-Redirect)
- Code-quality reviewer: нет дублирования, стиль проекта, отсутствие регрессий

## Финиш

- Полный `pytest -q` (ожидаем 219+ passed, без регрессий)
- Push в origin/master, рестарт сервера
- Проверка пользователем: B-0002 (ready) → ошибка в блоке скана с русским текстом; B-0011 → переход на /placement
