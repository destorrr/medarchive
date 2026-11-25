# UX-09.2 — frontend architecture

2026-11-25. Инженерное предложение, не реализация. Продолжение UX-09-01-DESIGN-SYSTEM-INVENTORY.md.

## Проверенные источники
src/medarchive/main.py, config.py, dependencies.py, templates/base.html, static/app.css, static/app.js; routers/postgresql_{branch,packing,transfer,central_office,archive,shipments,admin_pages,dashboard}.py; templates/postgresql_{packing,requests,central_requests,receive}.html, dashboard.html; docs/ux-audit/README.md и UX-07-UNIFIED-SCREEN-MAP-AND-NAVIGATION.md.

## Фактическое состояние
FastAPI + Jinja2, локальный HTMX, обычные POST-формы на четырёх проверенных операционных страницах. base.html содержит ролевую навигацию и inline SVG. app.js реализует клавиатурную прокрутку таблиц. app.css содержит старые токены #315fbd, #f5f7fb, #172033, sidebar 244px. main.py условно подключает PostgreSQL или SQLite routers. config.py: auth_backend=sqlite, postgres_enabled=false по умолчанию; dependencies.py импортирует оба адаптера.

## Разрывы с утверждённой моделью
| Текущая поверхность | Целевое изменение |
|---|---|
| /packing, /packing/scan-card, /packing/confirm-card | Два способа регистрации и упаковки; валидный QR без конфликта — атомарная регистрация/добавление без лишнего подтверждения |
| /transfer, /transfer/confirm | Только филиал → архив; черновики по курьерам, отдельный handover |
| /branch/receive, confirm, resolve | Исключить маршрут ЦО → филиал после проверки legacy зависимостей |
| /central/packing, /central/transfer | ЦО → архив; черновики и поштучная приёмка коробок |
| /receive, /receive/confirm | Сейчас шаблон требует приёмку целиком; целевая приёмка по коробкам, размещение отдельно |
| /placement/card и другие placement endpoints | Исключить новые самостоятельные CardPlacement после миграции; отделить коррекции |
| /requests/new, /requests/create | Сейчас отдельная заявка на карту; целевая групповая заявка parent/items |
| /requests/unpack-card, /requests/fulfill, /requests/not-found | P-09: все позиции обработаны до единственного отправления; атомарный handover; ответственность курьера до получения каждой карты ЦО |
| /central/receive/confirm | Сверить с контрактом поштучного и группового получения |
| /shipments, /shipments/{shipment_id} | Scope, фильтры, частичная приёмка, хронология и даты создания |
| /dashboard, /search, /history | dashboard_view отдельно от операционных функций; исключить viewer; общий поиск только ФИО |
| /admin/* | Дополнительные права, отдельный аудит, retirement legacy incidents по контракту |

Наличие endpoint не подтверждает соответствие целевому контракту. Новые URL и DB command IDs пока не утверждаются. Отмена P-09 только до handover, с возвратом извлечённых карт и сохранением истории. Аналитика не отдельная роль.

## Предлагаемая архитектура
Сохранить FastAPI + Jinja2 + HTMX, не вводить SPA без доказанной необходимости. HTML-first, server-rendered pages/fragments, локальные JS-модули. Обычный POST/redirect для простых операций, HTMX для локальных обновлений после определения контрактов; единая серверная авторизация и бизнес-команда. GET не мутирует данные.

Предлагаемые каталоги:
- templates/layouts: shell, login;
- templates/components: nav, panel, form, registry, status, timeline, dialog, feedback;
- templates/workspaces: branch, central, archive, admin, analytics;
- static/css: tokens, base, layout, components, workspace overrides;
- static/js: scanner, search, dialog, selection, focus, request lifecycle.

Сохранить skip-link, aria и клавиатурную прокрутку. Не переносить старые визуальные токены автоматически. Утверждённые пять HTML UX-08 остаются эталоном.

## Состояние и безопасность
Сервер — источник истины. URL/query — фильтры и пагинация; локальное состояние — временный ввод, выделение и диалоги. Не хранить ФИО, телефон, QR в localStorage или telemetry. Скрытие кнопки не является авторизацией. Scope на каждом GET/POST, актуальные права и предусловия в транзакции. При конфликте обновлять состояние с сервера, не повторять мутацию вслепую. Идемпотентность по UX-06; disabled не заменяет серверную защиту. Сохранять фокус и ввод при исправимой ошибке. Игнорировать устаревшие ответы поиска. Проверить origin-защиту и CSRF до cutover.

## Последовательность
1. Завершить компонентную инвентаризацию пяти утверждённых HTML.
2. Сопоставить экраны с UX-06 command contracts, UX-07 screen map и поздними UX-08 revisions.
3. Определить read models, права, ошибки, транзакции и тесты.
4. Подготовить PostgreSQL-only preflight и миграцию; не удалять legacy до проверки зависимостей.
5. UX-10 — вертикальная реализация после отдельного разрешения; UX-11 — тестирование.

README определяет UX-09 как дизайн-систему и компоненты; API/БД здесь вспомогательное проектирование, не production реализация. Открыты браузерные, HID, адаптивные, клавиатурные, серверные и межролевые gates. Полный аудит всех шаблонов и handlers ещё не завершён. Код, миграции, утверждённые HTML не изменялись; тесты не запускались.
