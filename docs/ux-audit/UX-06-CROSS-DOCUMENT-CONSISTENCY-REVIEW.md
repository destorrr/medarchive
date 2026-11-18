# UX-06 — cross-document consistency review

Дата: 2026-11-18  
Проверенная ветка: `docs/ux-audit-project`  
Базовый HEAD: `86c13671b0ee52697a30bc8155ba10fc26dad104`  
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
| Исключение archive и not_found | storage model §8.2; technical data model §3.2; command contracts §§2.5–2.6; P-09/D-04 | gap: not_found comment policy | Для removed_by_archive комментарий обязателен во всех документах. Для not_found D-04/P-09 фиксируют окончательность результата, но не утверждают обязательный пользовательский комментарий. Command contracts требуют комментарий/обоснование, а technical data model говорит о пояснении «по утверждённой форме». Это дополнительное требование не подтверждено решением владельца продукта. До утверждения не делать обязательность комментария продуктовым инвариантом; actor/time и окончательность результата обязательны. |
| Единый handover и приёмка | storage model §§5.4, 8.3; technical data model §§5.4–5.5; command contracts §§2.8–2.9; invariants DB-19…21 | consistent | Все оставшиеся extracted позиции передаются одним shipment и одним handover. После handover parent остаётся processing; completed — после полной приёмки, если shipment создан. Частичная приёмка запрещена. |
| Отмена и гонка с handover | storage model §8.4; technical data model §5.3; command contracts §2.7; invariants DB-16/17; P-09 | consistent | До processing отменяет ЦО без возврата; после processing и до handover — archive с атомарным возвратом всех extracted карт. После handover отмена невозможна. Parent lock сериализует cancel/handover. |
| Позиции при отмене | storage model §8.2/8.4; technical data model §5.3; command contracts §2.7; invariants DB-16 | consistent | Незавершённые позиции становятся cancelled; not_found и removed_by_archive сохраняют окончательные результаты. Возврат карт, item states и parent update — одна транзакция. |
| Idempotency и command result | command contracts §§1, 8–9; readiness plan §§3–4, 10 | open technical design | Семантика command_id согласована, но ledger, hash входа, хранение результата и точный typed return contract ещё не выбраны. Закрыть на техническом проектировании до SQL API, без изменения бизнес-правил. |
| Actor trust и DB security | DB security architecture; command contracts §1.1; invariants §1; grants matrix §§2–6; DB-SEC-01/02/04 | consistent | p_actor_user_id — application-authenticated actor, не независимое доказательство личности. GUC не считается границей безопасности. Runtime получает закрытый command EXECUTE allowlist и необходимые SELECT; прямой DML отзывается поэтапно. |
| DomainEvent и admin/security audit | command contracts §§1, 7; grants matrix; readiness plan §§3, 4, 9; DB-SEC-03 | clarification required | Общий typed/versioned append-only DomainEvent и отдельный admin/security audit согласуются. Фраза «записать DomainEvent и необходимые admin/security audit entries» не должна означать дублирование каждой admin-корректировки в двух равноправных журналах. Нужно определить authoritative audit record для каждого класса событий и правила ссылочной корреляции. |
| Principals и grants | DB security architecture; DML/principals inventory; grants matrix; readiness plan | consistent as target, current ACL unverified | owner NOLOGIN, migrator LOGIN, runtime LOGIN. Матрица — целевая, не фактическая проверка grants. Login throttle, bootstrap, backup/restore, monitoring и deployment principals требуют отдельной инвентаризации/решений. |
| Миграция legacy данных | technical data model §§6–7; invariants §6; readiness plan §§4, 7–8 | open preflight dependency | Additive-first, backfill/reconciliation и ручная сверка неоднозначных CardPlacement согласуются. Нельзя автоматически конвертировать CardPlacement в BoxPlacement. Конкретная карта конфликтов и mapping старых заявок определяются только по read-only preflight на копии данных. |
| Физическая тара и циклы | storage model §3/§6; technical data model §§2, 4.2; D-10/P-03; readiness plan §10 | open technical/product detail | Общая модель Box → последовательные BoxCycle и сохранение истории согласованы, но точный reuse/identity mapping и поведение короткого номера коробки ещё не зафиксированы. Нужен отдельный дизайн до DDL. |
| Legacy viewer/incidents/CardPlacement | D-03/D-06; technical data model §2/§6; readiness plan §4/§10 | open migration plans | Целевой процесс без viewer/incidents и CardPlacement согласован. Существующие пользователи/ACL, incident data paths и read-only retention требуют отдельных планов; удалять их в UX-06 нельзя. |

## 3. Исправленный конфликт BoxPlacement\n\nТехническая модель ранее содержала безусловный инвариант «активное BoxPlacement только для непустой коробки». Он прямо противоречил D-11/P-03, DB-12 и соседнему описанию regroup, по которым пустая коробка после перегруппировки остаётся на полке до решения пользователя. Исправлены §4.2 и инвариант 4: разрешено только это контекстное исключение; после extraction последней карты по заявке placement обязательно закрывается. Общий trigger автоматического снятия пустых коробок не допускается.\n\n## 4. Уточнения, которые следует внести до реализации

1. **not_found comment policy.** Не считать комментарий обязательным, пока владелец продукта явно этого не утвердил. Минимально обязательны actor, timestamp и финальный результат. Если бизнесу нужен комментарий, оформить отдельным решением.
2. **Удаление позиции ЦО.** В технической схеме можно хранить audit tombstone, но текущая выборка/манифест заявки должны исключать такую позицию. История изменения состава — отдельный audit/event projection.
3. **Audit ownership.** Зафиксировать для каждого класса событий, что является authoritative записью: DomainEvent или отдельный admin/security audit. Не создавать дублирующие источники истины.
4. **Command ledger.** До проектирования SQL API выбрать уникальность command_id, input hash, хранение typed result, retention и поведение при повторе после успешного commit.
5. **Box/BoxCycle.** До DDL закрепить физическую идентичность тары, правила открытия/закрытия цикла и повторного использования короткого номера.

Пункты 1 и 3 требуют продуктового/операционного уточнения; пункты 2, 4 и 5 могут быть закрыты техническим проектированием с последующей проверкой владельцем продукта там, где меняется пользовательское поведение.

## 4. Реестр действительно открытых решений

### Требуют решения владельца продукта

- **P-04/D-13 — списание медицинской карты:** основания, обязательные поля/подтверждения и проверки для инициирования ЦО и исполнения архивом.
- **P-07 — admin correction:** точный allowlist корректировок, ограничения по состояниям, формат before/after и защита истории.
- **not_found:** обязателен ли комментарий/обоснование при фиксации «не найдена» (см. §3.1).

### Можно закрыть проектированием и read-only аудитом

- **D-10/P-03:** физическая тара ↔ BoxCycle, идентификаторы и повторное использование.
- **D-17:** фактический QR payload/parser contract — извлечь из исходников и тестов, формат не менять.
- **D-06:** миграция viewer и legacy ACL.
- **D-03:** retirement plan legacy incidents без возврата инцидентов в целевой процесс.
- **DB command idempotency/result:** ledger, input hash, typed result.
- **DB audit/event ownership:** authoritative source и корреляция.
- **RLS/actor scope, login throttle, bootstrap, infra principals, consumers, read permissions и rollout compatibility:** сначала инвентаризация репозитория/deployment, затем проектные рекомендации. Не подменять DB-SEC-01 независимой DB-аутентификацией.

## 5. Ограничения проверки

- Проверены проектные документы в рабочей ветке на указанном HEAD.
- Не выполнялись подключения к PostgreSQL, запросы к pg_catalog, миграции, SQL, pytest или CI.
- Не проверялись production данные и фактические deployment secrets/ACL.
- Исходники, ORM, API/UI, тесты, migrations, workflows и deployment не изменялись.
- Этот review не закрывает UX-06 и не разрешает реализацию.
