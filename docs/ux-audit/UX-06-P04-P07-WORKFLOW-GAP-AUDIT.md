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

Do not infer that a free-text comment, attachment, commission act or external document number is mandatory. The product owner has explicitly decided that an external document/act, its number and an attachment are not required and must not block initiation or execution. If external-document details are available, they may be retained as optional metadata; this does not assert that file-upload capability exists or is approved for implementation. A second-person archive approval is not required: one authorized archive employee reviews and executes the write-off, with executor and timestamp retained in the immutable history. Do not reuse the box write-off operation or its rules for a medical card.

### Decision required from product owner

Define the minimum evidence/fields for initiation and execution:
- whether a structured reason code is mandatory, and the allowed reason catalog;
- whether a comment is required, optional or disallowed;
- external document/act reference, number and attachment are not mandatory; optional metadata retention may be considered, while file-upload capability remains a separate implementation decision;
- archive execution is performed by one authorized employee; a second-person confirmation is not required, and executor/time are recorded in the immutable history;
- cancellation policy is approved: before archive processing starts, Central Office may cancel with a mandatory reason; after processing starts, only archive may cancel with a mandatory reason; after the write-off is executed, cancellation is unavailable and an erroneous write-off is handled through a separate P-07 administrative correction that preserves the original event.

These decisions should be made before the card write-off state machine and DB command contract are frozen.

### Принятое решение владельца продукта (2026-11-18)

- При инициировании списания медицинской карты ЦО **обязателен структурированный код основания** из утверждаемого справочника.
- Свободный текстовый комментарий/пояснение — **необязателен**; если указан, сохраняется вместе с заявкой и историей решения.
- Утверждён закрытый справочник кодов: `lost` — «Потеря карты»; `damaged` — «Порча»; `other` — «Прочие причины». В workflow/API/DB contract передаётся стабильный код, а не отображаемая подпись; произвольные значения запрещены. Свободное пояснение, в том числе при `other`, необязательно.

Решение закрывает вопрос обязательности структурированного основания и комментария. Внешний документ/акт, его номер и вложение не обязательны и не могут блокировать заявку или исполнение; при наличии реквизиты можно сохранить как необязательные сведения. Это не утверждает наличие или реализацию загрузки файлов. Второе подтверждение сотрудника архива не требуется: списание выполняет один уполномоченный сотрудник с фиксацией исполнителя и времени. Правила отмены утверждены: до начала обработки архивом заявку может отменить ЦО с обязательной причиной; после начала обработки — только архив с обязательной причиной; после фактического списания отмена невозможна. Ошибочное списание исправляется отдельной административной коррекцией P-07 без удаления исходного события. Полномочия остаются неизменными: ЦО инициирует, архив исполняет.


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


### Proposed P-07 allowlist for product-owner review (not yet approved)

The following is a constrained proposal, not an implementation authorization or an approved product decision. The guiding rule is to correct a demonstrably wrong current business fact through a typed compensating command; never edit or delete the original event, and never use admin authority to bypass an ordinary workflow.

| Category | Proposed admin correction | Guardrails / exclusions |
|---|---|---|
| Card master data | Correct explicitly designated clerical metadata fields only | No arbitrary patch; no silent replacement/merging of card identity; changes to card number or patient identity require a separately specified identity-reconciliation process and must retain old/new values under restricted audit |
| Card custody / current location | Correct a demonstrably wrong current custody/location assignment | Only when no active shipment, unprocessed archive request, external-storage state or incompatible placement exists; append compensating custody/location event; do not fabricate or rewrite a handover |
| Box / BoxCycle | Correct current-cycle association or membership when the recorded association is demonstrably wrong | Never reuse or rewrite a prior cycle ID; preserve cycle sequence; reject if either object participates in an active shipment/request or the correction would violate box/card composition rules |
| Box placement | Correct current BoxPlacement (wrong shelf / missing or duplicate active placement) | Validate active shelf, archive custody and one-active-placement invariant; append placement correction; do not create CardPlacement or place individual cards on shelves |
| Shipment route, manifest, handover, receipt, completion | No admin rewrite of route, manifest, sender/receiver confirmation, handover or completed receipt | Before handover use the ordinary shipment/request workflow; after handover or completion preserve the original evidence and resolve discrepancies through separately typed compensating facts, not manifest edits |
| Archive request items / outcomes | No generic admin edit of request composition or result | Use the P-06/P-09 actor-specific workflow; not_found remains final; preserve archive removal reason and all request history |
| Card write-off | A narrowly scoped correction of an erroneous completed write-off | Dedicated command only; mandatory reason, reference to original write-off event, actor/time, atomic admin/security audit and compensating business event; never delete the write-off event. This does not grant generic status editing |
| Historical event actor, timestamp, payload or deletion | Prohibited | Source event remains immutable. If attribution itself is wrong, append a separately typed attribution-correction event with evidence and reference; original actor/time/payload remain visible as originally recorded |
| User, role, reference data | Existing admin CRUD remains governed by its own D-15 rules | Not a P-07 historical business correction; changes must not rewrite past event attribution or retroactively change permissions recorded at event time |

**Common command contract proposed for every allowed correction:** allowlisted command and field set; nonblank reason; target aggregate and original event/fact reference; authenticated admin actor; state/scope/invariant validation; idempotent command ID; append-only typed DomainEvent for the business correction plus atomic admin/security audit record; before/after values restricted to the allowlisted fields; no arbitrary JSON patch or direct runtime DML. Sensitive card/patient values must be minimized in audit payloads and shown only to roles already authorized for the object.

**Product decisions requested:** approve or amend the category allowlist, especially (1) whether any card master-data fields may be corrected and whether card number/patient identity are categorically excluded pending a dedicated reconciliation flow; (2) whether current custody/location, BoxCycle membership and BoxPlacement corrections are allowed under the guards above; (3) whether the dedicated erroneous-write-off correction is in scope for admin; and (4) confirm that shipment/request history and original event attribution/payload are never overwritten.
