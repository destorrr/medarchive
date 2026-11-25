# UX-09.3 — компонентный контракт

2026-11-25. Проектная спецификация, не production реализация. Источники: UX-08-01-BRANCH-APPROVED-V8 (0c4ab84a), UX-08-02-CO-APPROVED-V11 (17d13316), UX-08-03-ARCHIVE-APPROVED-V14 (106c4626), UX-08-04-ADMIN-APPROVED-V6 (3e93e939), UX-08-05-ANALYTICS-APPROVED-V5 (9fe55cbd). CSS и JS повторно прочитаны.

## Токены
Общие: Segoe UI/Arial; text #233c4b, page #f2f6f8, header #153d52, primary #116d7b, surface #fff, panel border #d8e4e9 (филиал #d8e3e9), panel radius 13px, padding 20px, layout gap 18px, grid gap 12px. Control border #c0d1dc (варианты #c2d2dc, #bccdd8, #bcced9), radius 8px (branch button 9px), active #e2f3f5/#62afbb (branch #e6f4f6/#63aeb8), hover #edf6f8, muted #667d8b (варианты #637987/#627b89), focus #e5a852 или #f6b84d, 3px. Success #e5f5ed, warning #fff4df, error #fee9e9 — семантические варианты, не окончательная нормализация.
| Workspace | max width | sidebar | breakpoint | h1 |
|---|---:|---:|---:|---:|
| Branch |1200|215|760|25|
| CO |1320|240|800 (+740)|25|
| Archive |1250|205|720|24|
| Admin/analytics |1320|240|800|25|
Сохранить варианты до визуального и responsive теста. CO содержит позднее правило nowrap для меню и повторное определение grid3 (2 колонки), admin/analytics — каскадные overrides. Не копировать CSS без проверки итогового cascade. Не менять утверждённые HTML.

## Общие компоненты
| Компонент | Источник | Контракт |
|---|---|---|
| AppShell, SidebarNav | shell/layout/main, nav | workspace, server-authorized menu, active route, responsive variant; аналитика не роль |
| Panel, PageHeader, ActionBar | panel/article, row/actions | заголовок, описание, содержимое, действия; не дублировать основное действие |
| Button, FormField | button, label, input/select/textarea | primary/secondary/link, disabled/pending, label, error, required; native semantics |
| StatusBadge, Feedback | chip/tag, message/notice | текстовый статус, semantic tone; alert/status; не только цвет |
| Registry, FilterBar | table/scroll, filters, registryControls | columns, rows, scope, filter state, pagination, empty/error/loading; URL state |
| MetricTile | statcard/metric/tile | value, unit, label, drilldown с тем же scope/фильтром |
| Timeline | timeline/event, history | server events, actor, timestamp, event label, object link; no fabricated timestamps |
| Dialog | archive dialog/modal, CO externalForm | title, body, actions, focus restore, Escape; destructive confirmation только по контракту |
| ScannerPanel | branch scan/handleQR, CO scan-panel/scanCard | context, input, result, recoverable error, focus; HID/Enter, one submission, no QR telemetry |
| CardLookup | branch search, CO showMatches, archive reportQuery | общий ФИО search отдельно от специализированного поиска упаковки; debounce, stale response rejection |
| SelectionList | shiprow, toggleReceipt/toggleBox/toggleCard | stable IDs, eligibility, selected count, select all scope explicit, server validation |
| ObjectDetail | box/card/request/shipment/objectPage | identity, status, location/custody, permitted actions, history; scope checked on server |

## Предметные композиции
Branch: BoxWorkspace, ManualCardForm, CourierAssignment, ShipmentDraft, ShipmentRegistry. Источники groupAssignments/assignCourier/createDrafts/saveDraft/cancelDraft/handoverDraft, register/insert/handleQR. Два входа в регистрацию/упаковку, атомарность QR, запрет пустых коробок.
CO: RequestDraft, RequestItems, CardReceipt, ExternalCustodyForm, BoxWorkspace, ShipmentDraft. Источники createDraft/submitDraft/cancelDraft, toggleReceipt/receive, externalize/returnExternal, createShipment/handover. 
Archive: BoxReceipt, DiscrepancyPanel, PlacementSelector, StorageCycleHistory, RegroupWorkspace, P09Processing, ShipmentSummary. Источники receive/missing/clearMissing/place/unplace/repack, extractItem/restoreItem/prepare/handover/cancelReq/completeEmpty. Все позиции P09 обработаны до единственного отправления, custody курьера после handover, поштучное получение ЦО.
Admin: UserForm, PermissionEditor, DirectoryRegistry, ArchiveStructure, SecurityAudit, ObjectDetail. Источники saveUser/toggleUser/rights/dashboardRights/resetPassword/saveSimple/saveSection. Не переносить demo role switch; admin audit отдельно.
Analytics: MetricTile, PeriodFilter, MovementChart, QueueDrilldown, StorageExplorer. Источники filter/showQueue/chart/storage/setSec/setShelf/openBox. Только dashboard_view согласно утверждённой модели; данные и drilldown серверные.

## Поведение и критерии
Прототипные JS-функции — источники сценариев, не production API. resetDemo, simulateArchiveReceipt, setRole и demoTime не переносить в production. Не использовать inline onclick/onchange как целевой стандарт; делегирование событий и локальные модули, безопасный вывод данных. Реальные команды и права определяются UX-06/UX-09 mapping. Для каждого компонента проверить default, focus, disabled, loading, empty, error, success и conflict, где применимо. Pending не заменяет идемпотентность. Сохранять фокус и ввод при исправимой ошибке. Проверить клавиатуру, screen reader, контраст WCAG AA, отсутствие горизонтального overflow страницы (таблицы могут иметь собственную прокрутку), HID, responsive и visual regression на пяти эталонах. Никакие эти тесты пока не запускались.

## Следующий шаг
Screen/action → read model/DB command → permission/scope → transaction/event → error → test. Точные API и миграции остаются открыты. Компонентные имена и декомпозиция — инженерное предложение, не новые бизнес-правила. Production код и утверждённые HTML не менялись.
