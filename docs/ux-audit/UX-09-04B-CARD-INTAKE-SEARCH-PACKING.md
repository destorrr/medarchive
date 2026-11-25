# UX-09.4B — card intake, search and packing mapping
2026-11-25. Проектирование, не реализация. Источники: UX-07-WF-08-CONTINUOUS-QR-SCANNING.md §6 (позднее утверждение заменяет раннее ожидание подтверждения), UX-08-BRANCH-V6-BASELINE-AND-HANDOFF.md, UX-06-TECHNICAL-DATA-MODEL.md, UX-06-INVARIANTS-DB-TEST-MATRIX.md DB-50–59, src/medarchive/services/postgresql_card_intake.py, утверждённый branch V8.

## QR и текущий код
JSON v1: version, card_number, full_name, birth_date ISO YYYY-MM-DD, optional phone. Реальный parser принимает 1 и строку "1", преобразует значения в строки, trim, проверяет canonical date; совместимость и строгую типизацию проверить тестами, не менять внешний формат молча. _actor_and_location допускает только активный branch с активной локацией. inspect_from_qr/register_from_qr ищут уникальный номер, сравнивают ФИО и ДР, проверяют active/location/no shipment/available. register_from_qr создаёт Card и card.registered_from_medical_system_qr. Текущий сервис описывает подтверждение новой карты; целевой UX-08 требует автоматического создания без конфликта. Не считать существующий код готовой защищённой DB-командой.

## Mapping
Названия card_intake_qr, card_create_manual, card_intake_and_pack, card_duplicate_review — предлагаемые use case IDs, не утверждённые SQL-функции. box_create, box_add_card, box_remove_card — проектные DB-команды UX-06.
| Screen/action | Use case | Scope/precondition | Atomic result |
|---|---|---|---|
| B01 QR вне коробки | card_intake_qr | активный филиал, доступная карта или новый уникальный номер без конфликтов | существующая карта read result либо создание; без membership |
| B01 ручное создание | card_create_manual | branch, номер/ФИО/ДР обязательны, phone optional, проверка совпадений | новая самостоятельная карта |
| B03 QR в коробке | card_intake_and_pack | branch, доступная свободная коробка и карта | существующая карта добавляется либо новая создаётся и добавляется в одной транзакции |
| B03 ручное создание в коробке | card_intake_and_pack (manual) | те же проверки, подтверждение совпадений при наличии | card + membership + cycle при необходимости; rollback целиком |
| B02 создать коробку | box_create | активный branch, scope | только Box, без пустого BoxCycle |
| B02 добавить существующую | box_add_card | scope, допустимый custody/status, отсутствие конфликтующего membership/shipments | membership, первый цикл при необходимости |
| B02 убрать ошибочно добавленную | box_remove_card | свободная коробка, scope | закрыть membership; последняя карта закрывает цикл; карта не удаляется |
| B01/B03 совпадение ФИО | card_duplicate_review | scoped projection, подтверждение отдельной карты | повторная серверная проверка перед созданием; не объединять |
| C05 упаковка | box_add_card/box_remove_card/box_create | ЦО, доступные карты и коробки, разрешённое состояние | те же инварианты цикла; не расширять право регистрации на ЦО |
| Общий поиск | CardSearch read model | активный actor, scope | без мутации |
| Поиск в упаковке | PackingCardLookup read model | отдельный контекст и scope | без мутации |

## Поиск и противоречие
Общий поиск: только ФИО, префикс каждой части в исходном порядке, trim/пробелы, регистр и е/ё, без substring/reorder, автоматический debounce, stale response rejection, сортировка ФИО затем номер, подсветка разрешённого ФИО. Все доступные совпадения без искусственного отсечения; техническая пагинация не должна скрывать совпадения. ДР/телефон видны всем активным целевым ролям в доступных карточках/результатах (решение 22.11), без отдельных read grants, не в манифестах/очередях. В упаковке ранее пользователь отдельно разрешил ФИО/номер/телефон; WF-08 и branch v6 описывают только ФИО. Зафиксировать расхождение как узкое контекстное исключение по прямому указанию владельца, не расширять общий поиск; требуется синхронизация источников перед реализацией. Не вводить общий профиль пациента.

## UX и транзакции
HID целый payload + терминатор; не перехватывать обычный ввод, диалоги; видимый box context, не перенаправлять queued scan при смене коробки; последовательная очередь, восстановление фокуса, нейтральный повтор уже в коробке. Невалидный QR, чужой scope, несовпадение номера/ФИО/ДР, заблокированная коробка — без мутации. Не исправлять телефон/ФИО/ДР автоматически. Не раскрывать чужие PII при конфликте. Actor из сессии; command_id, correlation_id, lock/recheck по UX-06; уникальный номер, максимум одно активное membership/цикл, ledger и DomainEvent в одной транзакции. Для нового номера row lock отсутствующей строки недостаточен: UNIQUE и обработка concurrent insert обязательны. Точные event IDs ручного создания и membership сверить с каталогом; существующий QR event не переименовывать без migration/consumer plan. Не хранить QR в telemetry/localStorage. Не задавать лимит вместимости без решения владельца.

## Tests
30 последовательных QR в коробке и отдельно вне коробки; duplicate scan; same number concurrent create; same FIO different number review; mismatch identity; malformed/version/date; чужая локация; locked draft; rollback card+membership+event; first/last membership cycle; concurrent add/remove; смена коробки при pending scan; typing/dialog; revoked actor; scoped search/direct URL; е/ё, prefixes, sort, stale response; no PII leakage. Реальный HID и browser gates открыты. Точные API, event schemas, нормализация identity при сравнении QR, специализированный поиск и разрешения требуют технической сверки. Код, миграции, HTML не изменялись, тесты не запускались.
