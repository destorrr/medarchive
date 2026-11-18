# UX-06 — Open product workflows P-04 and P-07: implementation gap audit

**Status:** static audit and decision brief; product decisions remain open. No implementation authorization.  
**Inspection baseline:** `docs/ux-audit-project`, HEAD `b3dc9561b5023a22fffcc8e6860fcfa1bd2899e6`.

## 1. P-04 / D-13 — medical card write-off

### Confirmed current model and paths

- PostgreSQL Card status currently permits `active`, `inactive` and `under_review`.
- The inspected PostgreSQL workflow exposes `write_off_box` for an empty, stationary box. No corresponding medical-card write-off command/service/route was found in the inspected PostgreSQL card, archive, shipment or admin workflow files.
- `inactive` and `under_review` do not by themselves define a governed card write-off lifecycle, initiator, executor, reason, approval or immutable result.
- Existing card external-custody transitions are a separate process and must not be repurposed as write-off.
- D-13/P-04 already fixes the authority split: Central Office initiates; archive executes. The basis/evidence and mandatory fields remain open.

### Required target process boundary

At minimum the target must distinguish:
1. a Central Office initiation/request, which does not itself destroy or silently change card custody;
2. archive review and execution by an authorized archive employee;
3. a terminal write-off fact with actor and timestamp;
4. rejection/cancellation behavior, if permitted, without erasing the request history.

The write-off must be blocked while the card is in an active shipment, has an incompatible custody state, or is otherwise subject to a conflicting workflow. Exact guards and transaction boundary require DDL/workflow design.

Do not infer that a free-text comment, attachment, commission act or external document number is mandatory. The product owner has explicitly decided that an external document/act, its number and an attachment are not required and must not block initiation or execution. If external-document details are available, they may be retained as optional metadata; this does not assert that file-upload capability exists or is approved for implementation. A second-person archive approval remains an open product decision. Do not reuse the box write-off operation or its rules for a medical card.

### Decision required from product owner

Define the minimum evidence/fields for initiation and execution:
- whether a structured reason code is mandatory, and the allowed reason catalog;
- whether a comment is required, optional or disallowed;
- external document/act reference, number and attachment are not mandatory; optional metadata retention may be considered, while file-upload capability remains a separate implementation decision;
- whether archive execution requires a second-person confirmation or only the executing archive employee;
- whether Central Office may cancel an unexecuted request, and at what point cancellation becomes unavailable.

These decisions should be made before the card write-off state machine and DB command contract are frozen.

### Принятое решение владельца продукта (2026-11-18)

- При инициировании списания медицинской карты ЦО **обязателен структурированный код основания** из утверждаемого справочника.
- Свободный текстовый комментарий/пояснение — **необязателен**; если указан, сохраняется вместе с заявкой и историей решения.
- Сам справочник кодов и его значения ещё требуется определить до фиксации workflow/API/DB contract. Нельзя подменять код основания произвольной строкой.

Решение закрывает вопрос обязательности структурированного основания и комментария. Внешний документ/акт, его номер и вложение не обязательны и не могут блокировать заявку или исполнение; при наличии реквизиты можно сохранить как необязательные сведения. Это не утверждает наличие или реализацию загрузки файлов. Открытыми остаются необходимость второго подтверждающего сотрудника архива, правила отмены заявки ЦО и момент запрета отмены. Полномочия остаются неизменными: ЦО инициирует, архив исполняет.


## 2. P-07 — admin corrective actions

### Confirmed current model and paths

- PostgreSQL admin service currently manages branch, courier, shelf and user records: create/update/activate/deactivate operations.
- The inspected admin service/pages do not expose explicit corrective commands for card, box, shipment, archive request, custody, placement, cycle or event history.
- Existing `DomainEvent.correction_of_event_id` is a schema field, but no complete correction command/read workflow was established by this inspection.
- Generic CRUD updates are not equivalent to audited correction of a historical business fact.
- D-07 global object access and P-07 corrective authority are separate: the former does not imply permission to change every object.

### Required target contract

Every approved correction action must:
- be explicitly allowlisted by object type and command;
- require a nonblank reason and record actor/time;
- identify the target aggregate and, when applicable, the event/fact being corrected;
- preserve the original immutable business event and append a correction/superseding fact rather than rewriting history;
- validate current state, actor role and object/location scope;
- execute through protected DB commands and write the correction audit record atomically;
- avoid generic arbitrary-field PATCH/SQL-like correction interfaces.

A correction must not become a hidden bypass for D-01/D-02/D-08/D-11/D-13/D-18 or for completed shipment/request transitions. Admin may correct only cases that are explicitly listed and whose safe state transition is specified.

### Decision required from product owner

The allowlist must identify which of these categories admin may correct, if any:
- card master data / card identity metadata;
- card current location or custody;
- box identity, current cycle or membership;
- box/card shelf placement;
- shipment manifest/route/status/receipt;
- archive request items and outcomes;
- historical event attribution or payload;
- other category (must be named).

For each allowed category, define whether correction is permitted only before handover/completion or may also be performed after completion through a compensating event. Historical event payload/actor/time must never be overwritten. Any correction of a completed shipment must preserve P-08 manifest evidence and the original sender/receiver facts.

## 3. Recommended separation

P-04 is a domain workflow with Central Office initiation and archive execution. P-07 is a privileged corrective-command catalog. They should not share a generic "admin override" endpoint or a single unrestricted reason field.

The audit store may record that a correction was authorized/performed, but the corrected business fact and its history belong in the relevant aggregate's typed DomainEvent timeline. Both writes must be atomic where one command affects both.

## 4. Readiness impact

- P-04 blocks final card write-off state machine, command mapping, DB invariants and UX wireframe for that operation.
- P-07 blocks the admin correction UI, command allowlist, event correction semantics, DB grants and negative/positive role tests.
- Neither gap blocks continuing read-only audits or unrelated UX-06 documentation.
- No source code, tests, migrations, schema, workflows or deployment files were changed by this audit.
