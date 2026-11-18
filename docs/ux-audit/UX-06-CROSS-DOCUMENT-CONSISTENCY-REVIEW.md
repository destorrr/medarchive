# UX-06 — cross-document consistency review

Дата: 2026-11-18  
Проверенная ветка: `docs/ux-audit-project`  
Базовый HEAD для этой сверки: `dd6a7cbb41a8084960f41dddc052d80bec3d6acc`; статус повторно актуализирован на HEAD текущего коммита  
Тип проверки: read-only анализ проектных документов; это не проверка фактической БД или работающего приложения.

## 1. Итог

Ключевые уже согласованные бизнес-правила UX-06 в целом согласованы между предметной моделью, технической моделью, контрактами DB-команд, матрицей инвариантов и readiness plan. Найден один прямой текстовый конфликт в формулировке инварианта BoxPlacement; он исправлен в технической модели этим коммитом. Пересмотра D-01…D-18, P-01…P-09 или DB-SEC-01…04 не требуется.

Обнаружены уточнения контрактов и несколько незакрытых вопросов. Они перечислены отдельно; этот документ не меняет утверждённые решения и не разрешает реализацию.

## 2. Матрица согласованности

| Область | Документы/разделы | Статус | Результат и требуемое действие |
|---|---|---|---|
| Физическая модель архива | storage model §§2–6; technical data model §§2, 4; invariants DB-01…07 | consistent | Полка → коробка → карты. На полке только BoxPlacement; CardPlacement выводится из целевой операционной модели, legacy-история сохраняется. |
| Извлечение при начале обработки | storage model §5.3; technical data model §§5.1, 4.2; command contracts §2.4; invariants DB-10/11 | consistent | Только явный archive start-processing атомарно закрывает memberships и переводит позиции в extracted. Последняя карта снимает placement; ошибка откатывает всю операцию. |
| Перегруппировка коробок | storage model §6; technical data model §§4.2, 5.6, invariant 4; invariants DB-12/13; D-11/P-03 | contradiction corrected | В технической модели фраза «BoxPlacement только для непустой коробки» противоречила разрешённой пустой коробке после regroup. Формулировка §4.2 и инвариант 4 уточнены: пустой placement допустим только после regroup; extraction последней карты закрывает placement атомарно. |
| Родительская заявка и позиции | storage model §8; technical data model §3; command contracts §2; P-09 | consistent, с уточнением | Parent: draft → submitted → processing → completed; cancelled — отдельный конечный исход до handover. Item states отражают отдельные результаты. Нужна единая терминология и чёткое разделение текущего состава и аудиторского следа удаления ЦО. |
| Изменение состава ЦО | storage model §8.2; technical data model §3.2; command contracts §2.3; P-09 | consistent, presentation gap | Удалённая ЦО позиция не показывается в текущем составе, но факт/автор/время сохраняются. Техническая модель допускает tombstone-строку removed_by_central_office. В UI/read model её нельзя показывать как активную позицию; audit trail остаётся доступным уполномоченным лицам. |
| Исключение archive и not_found | storage model §8.2; technical data model §3.2; command contracts §§2.5–2.6; P-09/D-04 | consistent — product decision recorded | Для `removed_by_archive` комментарий обязателен. Для `not_found` комментарий необязателен; обязательны окончательный результат, actor и timestamp. Command contract, technical model и DB invariant синхронизированы с решением владельца продукта от 2026-11-18. |
| Единый handover и приёмка | storage model §§5.4, 8.3; technical data model §§5.4–5.5; command contracts §§2.8–2.9; invariants DB-19…21 | consistent | Все оставшиеся extracted позиции передаются одним shipment и одним handover. После handover parent остаётся processing; completed — после полной приёмки, если shipment создан. Частичная приёмка запрещена. |
| Отмена и гонка с handover | storage model §8.4; technical data model §5.3; command contracts §2.7; invariants DB-16/17; P-09 | consistent | До processing отменяет ЦО без возврата; после processing и до handover — archive с атомарным возвратом всех extracted карт. После handover отмена невозможна. Parent lock сериализует cancel/handover. |
| Позиции при отмене | storage model §8.2/8.4; technical data model §5.3; command contracts §2.7; invariants DB-16 | consistent | Незавершённые позиции становятся cancelled; not_found и removed_by_archive сохраняют окончательные результаты. Возврат карт, item states и parent update — одна транзакция. |
| Idempotency и command result | command contracts §11; readiness plan | product-approved technical baseline; implementation design remains | Четыре обязательных правила command_id lifecycle, server-side schema-specific canonicalization + SHA-256, replay policy и tombstone для необратимых команд утверждены. Не закрыты конкретные обычные TTL после inventory retry horizons и окончательная детализация envelope/API error mapping. |
| Actor trust и DB security | DB security architecture; command contracts §1.1; invariants §1; grants matrix §§2–6; DB-SEC-01/02/04 | consistent | p_actor_user_id — application-authenticated actor, не независимое доказательство личности. GUC не считается границей безопасности. Runtime получает закрытый command EXECUTE allowlist и необходимые SELECT; прямой DML отзывается поэтапно. |
| DomainEvent и admin/security audit | command contracts §§1, 7, 11; admin audit contract; DB-SEC-03 | ownership clarified; retention/outage policy open | DomainEvent — authoritative бизнес-факт/коррекция агрегата. Отдельный AdminSecurityAudit — authoritative факт привилегированного admin/security действия, включая actor, reason, outcome и ссылки. При admin-коррекции бизнес-агрегата обе записи атомарны, но не дублируют payload и не конкурируют за источник истины. Чистый admin CRUD/auth/security event не создаёт DomainEvent. |
| Principals и grants | DB security architecture; DML/principals inventory; grants matrix; readiness plan | consistent as target, current ACL unverified | owner NOLOGIN, migrator LOGIN, runtime LOGIN. Матрица — целевая, не фактическая проверка grants. Login throttle, bootstrap, backup/restore, monitoring и deployment principals требуют отдельной инвентаризации/решений. |
| Миграция legacy данных | technical data model §§6–7; invariants §6; readiness plan §§4, 7–8 | open preflight dependency | Additive-first, backfill/reconciliation и ручная сверка неоднозначных CardPlacement согласуются. Нельзя автоматически конвертировать CardPlacement в BoxPlacement. Конкретная карта конфликтов и mapping старых заявок определяются только по read-only preflight на копии данных. |
| Физическая тара и циклы | ORM Box/BoxCycle; integration test packing; storage model §3/§6; D-10/P-03 | fact confirmed; target alignment required | Текущий код уже разделяет постоянную Box (UUID, unique box_number) и BoxCycle (box_id, cycle_number, unique open cycle). Интеграционный тест подтверждает повторное использование той же коробки: цикл 1 закрывается, цикл 2 открывается, старое membership сохраняется. В целевой модели следует сохранить Box как физическую тару, а BoxCycle как новый логистический цикл. Нового продуктового решения о связи не требуется. |
| QR карты | PostgreSQLCardIntakeService.parse_payload; packing integration test; D-17 | fact confirmed | Parser принимает JSON object, version=1 (integer или string \"1\"), обязательные card_number/full_name/birth_date (ISO YYYY-MM-DD), optional phone. Preview не создаёт карту до отдельного confirm. Payload не менять; при реализации сохранить поведение и добавить characterization tests. |
| Legacy viewer/incidents/CardPlacement | D-03/D-06; technical data model §2/§6; readiness plan §4/§10 | open migration plans | Целевой процесс без viewer/incidents и CardPlacement согласован. Существующие пользователи/ACL, incident data paths и read-only retention требуют отдельных планов; удалять их в UX-06 нельзя. |

## 3. Исправленный конфликт BoxPlacement

Техническая модель ранее содержала безусловный инвариант «активное BoxPlacement только для непустой коробки». Он прямо противоречил D-11/P-03, DB-12 и соседнему описанию regroup, по которым пустая коробка после перегруппировки остаётся на полке до решения пользователя. Исправлены §4.2 и инвариант 4: разрешено только это контекстное исключение; после extraction последней карты по заявке placement обязательно закрывается. Общий trigger автоматического снятия пустых коробок не допускается.

## 4. Уточнения, которые следует внести до реализации

1. **not_found comment policy — закрыто 2026-11-18.** Владелец продукта выбрал вариант Б: комментарий/обоснование необязателен; обязательны финальный результат, actor и timestamp. Контракты, техническая модель и DB-15 обновлены.
2. **Удаление позиции ЦО.** В технической схеме можно хранить audit tombstone, но текущая выборка/манифест заявки должны исключать такую позицию. История изменения состава — отдельный audit/event projection.
3. **Audit ownership.** Зафиксировать для каждого класса событий, что является authoritative записью: DomainEvent или отдельный admin/security audit. Не создавать дублирующие источники истины.
4. **Command ledger.** До проектирования SQL API выбрать уникальность command_id, input hash, хранение typed result, retention и поведение при повторе после успешного commit.
5. **Box/BoxCycle.** Связь подтверждена текущей ORM и integration test; до DDL проверить сохранение Box UUID, unique box_number и нумерации циклов. Новое бизнес-решение по связи не требуется.

Пункт 1 закрыт решением владельца продукта; пункт 3 остаётся открытым. Пункты 2, 4 и 5 могут быть закрыты техническим проектированием с последующей проверкой владельцем продукта там, где меняется пользовательское поведение.

## 5. Реестр действительно открытых решений (актуализирован)

### Продуктовые решения, закрытые владельцем

- **P-04/D-13:** процесс, отмена, один исполнитель архива и закрытый справочник `lost` / `damaged` / `other` утверждены.
- **P-07:** категории корректировок и запреты переписывания истории утверждены. Остаётся перечислить точные поля карточки в техническом allowlist.
- **not_found:** комментарий необязателен; обязательны результат, actor и timestamp.

### Можно закрыть проектированием и read-only аудитом

- **D-10/P-03:** проверить сохранение уже существующей связи Box → BoxCycle в target schema; нового продуктового решения не требуется.
- **D-17:** parser contract установлен read-only; формат не менять.
- **D-06:** миграция viewer и legacy ACL.
- **D-03:** retirement plan legacy incidents без возврата инцидентов в целевой процесс.
- **DB command idempotency/result:** основной lifecycle/hash/replay/tombstone baseline утверждён; остаются ordinary-command TTL после inventory retry horizons и финальный API error mapping.
- **DB audit/event ownership:** семантика authoritative records и correlation определена в этом review; остаются retention/privacy/outage policy и фактические schema/grants.
- **RLS/actor scope, login throttle, bootstrap, infra principals, consumers, read permissions и rollout compatibility:** сначала инвентаризация репозитория/deployment, затем проектные рекомендации. Не подменять DB-SEC-01 независимой DB-аутентификацией.

## 6. Ограничения проверки

- Проверены проектные документы в рабочей ветке на указанном HEAD.
- Не выполнялись подключения к PostgreSQL, запросы к pg_catalog, миграции, SQL, pytest или CI.
- Не проверялись production данные и фактические deployment secrets/ACL.
- Исходники, ORM, API/UI, тесты, migrations, workflows и deployment не изменялись.
- Этот review не закрывает UX-06 и не разрешает реализацию.


## 7. Решение владельца продукта по `not_found` (2026-11-18)

Владелец продукта выбрал вариант Б: комментарий/обоснование при фиксации `not_found` не обязателен. Обязательны только результат «не найдена», пользователь (`actor`) и время события (`timestamp`). Необязательный комментарий допускается сохранить, если он предоставлен. Это не затрагивает обязательный комментарий при `removed_by_archive` и обязательную причину отмены заявки. Соответствующие command contract, technical data model и DB-15 обновлены; продуктовый вопрос закрыт.