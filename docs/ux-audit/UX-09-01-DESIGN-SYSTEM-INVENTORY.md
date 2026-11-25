# UX-09.1 — инвентаризация визуальной базы и старт дизайн-системы

Дата: 2026-11-25. Статус: инженерный черновик, без изменения утверждённых HTML и production-кода.

## Проверенная контрольная точка
Прочитаны UX-08-APPROVED-BASELINE.md и пять утверждённых HTML в ветке docs/ux-audit-project. Blob SHA совпадают с baseline:
- branch V8: 0c4ab84a3eec5b66f3b5963b543f30db010e08bd
- CO V11: 17d13316f2bf1da30fa4bd32f4dbd6bedc758f39
- archive V14: 106c4626f8b27c6cf949d6a0da9ad0869d60787c
- admin V6: 3e93e939066e02e410095c92ab255aca94654043
- analytics V5: 9fe55cbdbe06efd0e4bfd639aaac09a3187f5ac4

## Наблюдения по HTML/CSS
Общая визуальная основа: Segoe UI / Arial; текст #233c4b; фон #f2f6f8; шапка #153d52; основные действия #116d7b; светлые панели с бордерами, радиусами и умеренной тенью.
- Максимальная ширина: филиал 1200px; архив 1250px; ЦО, администратор, аналитика 1320px.
- Боковая колонка: филиал 215px; архив 205px; ЦО, администратор, аналитика 240px.
- Панели: обычно радиус 13px, внутренний отступ 20px; межколоночный gap 18px.
- Поля/кнопки: обычно радиус 8px; присутствуют небольшие различия между рабочими местами.
- В HTML встречаются старые номера версии внутри title; ориентир — имя файла и baseline SHA, не title.

## Предварительный каталог токенов
Это не замена утверждённому CSS; значения для адаптивных режимов и всех состояний должны быть дополнительно извлечены и сверены.

```css
:root {
  --ma-text: #233c4b;
  --ma-page: #f2f6f8;
  --ma-header: #153d52;
  --ma-primary: #116d7b;
  --ma-surface: #fff;
  --ma-border: #d8e4e9;
  --ma-control-border: #c0d1dc;
  --ma-radius-panel: 13px;
  --ma-radius-control: 8px;
  --ma-panel-padding: 20px;
  --ma-layout-gap: 18px;
  --ma-font: 'Segoe UI', Arial, sans-serif;
}
```

Различия ширины и навигации не нормализовать вслепую: оформить как явные варианты shell и проверить визуальными регрессионными тестами.

## Первичная архитектурная проверка
В pyproject.toml зафиксированы Python 3.11+, FastAPI, Jinja2, SQLAlchemy asyncio, asyncpg, Alembic, Playwright в dev-зависимостях; aiosqlite пока присутствует. README описывает Jinja2/HTMX. Нельзя без анализа текущих routers/templates вводить новый SPA-фреймворк.
README содержит историческую формулировку об ответственности архива при передаче P-09; она противоречит более позднему UX-08 baseline (после передачи ответственность курьера). Исправлять при отдельной сверке документации, не переносить в код.
UX-06-IMPLEMENTATION-READINESS-PLAN.md требует additive migrations, preflight, backfill и поэтапный cutover; удаление legacy/SQLite не должно предшествовать инвентаризации зависимостей.

## Предварительные переиспользуемые компоненты
AppShell (варианты ширины), SidebarNav, Panel, PageHeader, ActionButton, FormField, SearchField, FilterBar, RegistryTable, StatusBadge, EmptyState, LoadingState, ErrorNotice, ConfirmDialog, EventTimeline, CardLookup, BoxSelector, ShipmentSummary.
Компоненты должны принимать данные от API; бизнес-переходы и полномочия проверяются сервером.

## Следующие проверки UX-09.1/09.2
1. Инвентаризировать selectors, CSS media queries, JS handlers и семантику всех пяти HTML.
2. Прочитать реальные routers/templates/static и определить границу Jinja2/HTMX, API и состояния.
3. Построить матрицу «экран → действие → DB command → право → событие → ошибка».
4. Зафиксировать визуальные snapshot tests, keyboard/responsive и HID scanner gates.
5. Провести PostgreSQL/SQLite dependency inventory до миграционного плана.

Не считать браузерные, аппаратные, PostgreSQL, конкурентные и межролевые проверки завершёнными.
