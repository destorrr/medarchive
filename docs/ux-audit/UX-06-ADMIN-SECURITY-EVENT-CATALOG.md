# UX-06 — AdminSecurityAudit: event catalog proposal

**Status:** technical/product proposal for owner review; not an implementation authorization.  
**Prepared:** 2026-11-19  
**Baseline:** branch `docs/ux-audit-project`, HEAD `1659ad150acb9e2cdb95f4fa275c3c15a8b029cd`.  
**Scope:** event taxonomy, common envelope, payload minimization, bounded denial telemetry and privileged-read boundary.

## 1. Contract boundaries

- `AdminSecurityAudit` is the authoritative record for authentication/security actions and privileged administrative actions.
- `DomainEvent` remains authoritative for business facts. A privileged correction writes its typed DomainEvent and a semantically distinct AdminSecurityAudit record atomically; the audit record references the business event and does not copy its full payload.
- The command ledger remains authoritative only for idempotency/result replay.
- The global admin/security feed contains no ordinary card, box, shipment or archive-request lifecycle events.
- DB-SEC-01 remains the accepted actor trust baseline: application-authenticated actor, validated by protected DB command. Audit attribution is not independent cryptographic proof.
- **Approved fail-closed rule (owner decision, 2026-11-21):** every state-changing command whose contract requires a `DomainEvent` and/or `AdminSecurityAudit` record must commit the business mutation, its required event record(s), and the command-ledger result atomically. If any mandatory record cannot be written, the entire transaction rolls back and no business state change is committed. This applies to significant business operations (cards, boxes, shipments, archive requests) as well as privileged administrative mutations. A privileged correction that requires both event classes commits both in the same transaction. Successful-login audit must commit before session issuance; otherwise no session is issued. Ordinary reads are not automatically blocked by audit-store unavailability. Not every 401/403 requires a synchronous durable event; bounded denial telemetry remains non-blocking.

## 2. Event classes and proposed catalog

The catalog distinguishes **mandatory transactional records** from **bounded security telemetry**. A telemetry event must never become an implicit availability dependency for an ordinary request. This exception does not weaken the atomic-write requirement for mandatory business or security records explicitly required by a command contract.

### 2.1 Mandatory transactional events

| Event type | Trigger | Required target/context | Atomicity |
|---|---|---|---|
| `authn.login_succeeded` | Credentials and account state accepted | actor user, authentication method, outcome | Audit commits before session issuance; failure means no new session |
| `admin.user_created` | Admin creates user | target user ID, assigned role/scope summary | Same transaction as mutation |
| `admin.user_profile_updated` | Allowlisted non-credential user attributes change | target user ID, changed field names only | Same transaction as mutation |
| `admin.user_role_changed` | Role/privilege assignment changes | target user ID, old/new role codes | Same transaction as mutation |
| `admin.user_activated` / `admin.user_deactivated` | Account active state changes | target user ID | Same transaction as mutation |
| `admin.credential_reset` | Admin resets/replaces credential | target user ID, reset mechanism code; never credential material | Same transaction as mutation |
| `admin.reference_data_created` / `admin.reference_data_updated` / `admin.reference_data_activated` / `admin.reference_data_deactivated` | Branch, courier, shelf or other approved reference-data mutation | target type/ID, operation, changed field names | Same transaction as mutation |
| `admin.correction_applied` | Approved P-07 correction command | aggregate type/ID, correction type, reason reference/code, source DomainEvent ID, resulting DomainEvent ID | Same transaction as business mutation and DomainEvent |

The reference-data family is a taxonomy proposal; implementation should use explicit event types per aggregate/operation or a closed typed discriminator, not an arbitrary free-form event name. Existing `admin.*` DomainEvent rows remain legacy evidence and must not be rewritten or deleted as part of a future migration.

A failed privileged command may have an outcome record only if its event policy and transaction semantics are explicitly defined. Do not force a failed command to write into the same transaction that is intentionally rolled back; do not misrepresent a failed mutation as a committed successful audit fact. A separate bounded denial/security telemetry path is described below.

### 2.2 Bounded security telemetry

| Event type | Trigger | Proposed handling | Blocking behavior |
|---|---|---|---|
| `authn.login_failed` | Invalid credentials or inactive/unknown principal | Count/rate-limit by keyed identity and source buckets; optionally emit sampled/threshold-crossing events | Never reveal whether account exists; telemetry failure does not turn a failed login into success |
| `authn.login_throttled` | Throttle blocks an attempt | Emit threshold/cooldown transitions or bounded aggregates, not one durable row per retry | No new session; telemetry write is not a second availability dependency |
| `authn.session_rejected` | Invalid, expired, stale-credential or otherwise rejected session | Aggregate by safe reason and bounded source/principal digest; avoid per-request flood | Request remains rejected regardless of telemetry availability |
| `authz.access_denied` | Authorization check returns denial | Routine expected denials are not written to the business journal or as one durable audit row per attempt. Use a separate, access-restricted security telemetry channel only for explicitly selected high-risk classes or threshold crossings; group repeated denials and enforce rate/volume limits | Request remains denied; telemetry failure or unavailability does not block request processing |
| `authn.logout` | Explicit user-initiated logout | Approved best-effort security event; not a mandatory transactional audit write | Telemetry failure/unavailability must not delay or prevent logout or credential clearing; browser close/network loss without explicit logout is not guaranteed to be recorded |

For each telemetry family, the implementation contract must specify: reason-code enum, aggregation key, window, threshold, maximum event rate, retention, and behavior when the telemetry store is unavailable. Do not log a raw HTTP response body, SQL error, stack trace or unbounded exception string. No blanket synchronous durable write for every 401/403 is proposed.

**Owner decision (2026-11-21) — access-denial handling:** routine failed authorization attempts are excluded from the main business journal (`DomainEvent`) and are not individually persisted as mandatory `AdminSecurityAudit` rows. If security monitoring is needed for selected high-risk denials, record it only in a separate, restricted and bounded security-telemetry path. Repeated denials must be grouped/aggregated and rate-limited so a burst cannot flood storage. Telemetry is best-effort: its unavailability or write failure must not block the request path or change the authorization outcome. The denial itself remains denied. This does not weaken atomic persistence of mandatory records explicitly required by a command contract.

### 2.3 Privileged reads and exports

**Approved initial scope (product decision, 2026-11-19):**
- Audit successful bulk export/download of patient-identifying or medical-card data.
- Audit generation/download of a manifest or report when the operation exports data outside the normal object-detail UI.
- Do **not** audit bulk lookup/search, including large result sets and cross-scope searches, even when the operation covers many cards or extends beyond ordinary viewing.
- Do not audit ordinary single-card views, normal search pages, dashboard reads or object-history views.
- Failed export attempts follow the bounded denial policy; do not make routine reads depend on a synchronous audit write.

Event types in scope:
- `data_export.generated`
- `data_export.downloaded`

`data_bulk_lookup.executed` is explicitly out of scope and must not be introduced as an AdminSecurityAudit event without a new product decision.

Required context: actor, operation code, data category, export format, outcome, correlation ID and timestamp. Do not store exported rows, patient names, card numbers, raw filters containing identifiers, file contents or download tokens in the audit event. If a generated artifact needs its own lifecycle record, reference an opaque artifact ID and apply separate access controls/retention.

## 3. Common event envelope

Proposed required fields:

| Field | Rule |
|---|---|
| `event_id` | Server-generated immutable UUID; unique |
| `schema_version` | Positive integer for the envelope contract |
| `event_type` | Closed, namespaced event type from the catalog |
| `occurred_at` | Database-generated UTC timestamp at the authoritative write boundary |
| `actor_user_id` | Authenticated actor UUID; nullable only for unauthenticated/unknown-principal telemetry |
| `actor_role_code` | Role snapshot at action time; nullable for unknown actor |
| `actor_scope_ref` | Minimal stable scope reference where relevant; avoid duplicating full ACL |
| `outcome` | Closed enum such as `succeeded`, `failed`, `denied`, `throttled` |
| `reason_code` | Closed event-family enum; required for failures/denials and where business correction policy requires it |
| `target_type`, `target_id` | Typed target reference where known; nullable for unknown login principal |
| `command_id` | Idempotent command UUID for command-backed actions; nullable for non-command telemetry |
| `correlation_id` | Request/workflow correlation UUID; required where available |
| `source_channel` | Closed enum such as `web_ui`, `api`, `operator_tool`, `scheduled_job` |
| `payload` | Strict per-event allowlisted object, validated against event type and schema version |

**Approved data-minimization decision (2026-11-21):** the initial product does not collect or retain raw source IP, IP digests, attempted usernames or identity digests in AdminSecurityAudit or bounded security telemetry, and does not correlate security events by source IP or attempted identity. Login-throttle counters remain a separate, narrowly scoped mechanism under their own contract; their internal bucket keys must not be copied into audit events or exposed as event-correlation identifiers. Reconsideration requires a concrete abuse-detection use case, privacy/legal assessment and a new explicit product decision.

## 4. Per-event payload and prohibited data

Payload schemas are allowlists, not generic model dumps. Prefer stable codes and references over labels or copied business data.

**Allowed examples**
- changed field names and stable role/status codes;
- target UUID and target aggregate type;
- correction reason code and a short bounded operator reason only where required by P-07;
- source/result DomainEvent IDs;
- authentication method and generic outcome;
- data category, export format and result-count band;
- stable reason codes and bounded counters.

**Prohibited**
- passwords, password hashes, reset secrets, MFA secrets or recovery codes;
- session cookies, bearer/access/refresh tokens, signing keys or raw credentials;
- full HTTP request/response bodies, unrestricted ORM objects or arbitrary JSON patches;
- SQL text, stack traces and unbounded exception messages;
- patient names, card numbers, dates of birth, phone numbers or exported row contents unless a separate approved, strictly necessary field is documented (none is proposed here);
- raw attempted usernames, raw source IP, IP digests or attempted-identity digests in security-event payloads;
- file contents, download URLs containing secrets, and unbounded search filters.

All payloads require maximum size/depth and schema validation. Unknown keys and unsupported schema versions must be rejected rather than silently persisted/rendered.

## 5. Read permissions and presentation

- **Approved read-access decision (2026-11-21):** only an active `admin` may read the global `AdminSecurityAudit` feed and event details. `branch`, `central_office` and `archive` receive neither the global feed nor any audit-payload endpoint. The same server-side restriction applies to direct URLs/API requests; UI hiding is not an authorization boundary.
- The event list renders event-specific labels and allowlisted fields; no generic raw-JSON viewer.
- Audit detail access should be narrower than general admin object access where payload contains security context. Admin's global object/history access does not justify exposing secrets or unnecessary PII in audit.
- Audit read operations remain admin-only. Opening the feed and viewing event details are not logged; no audit-feed export or export-specific access log exists in the target product. Separate business-data export operations remain audited under their own event types.
- Business history remains in the object's DomainEvent timeline with its existing object-level authorization.

## 6. Retention, integrity and operational gates — unresolved

No statutory or universal retention period is inferred. Before schema/DDL, the owner and operational/legal stakeholders must define:
1. retention by event family (mandatory admin/authentication, bounded denial telemetry, and business-data export events);
2. legal hold, preservation, deletion/archival and backup/restore behavior;
3. confirm implementation conformance with the approved exclusion of source/identity identifiers and event correlation;
4. HMAC key custody, separation, rotation, version overlap and destruction;
5. business-data export permissions, access logging and artifact lifecycle;
6. tamper-evidence requirements, privileged DBA threat model, backup immutability and independent monitoring;
7. event-volume limits, partitioning/index strategy and alerting for failed mandatory writes versus telemetry drops.

Append-only protections and least-privilege INSERT/SELECT grants remain required. Append-only at the application layer alone is insufficient; the eventual DB contract must prevent runtime UPDATE/DELETE and restrict who can read/insert.

## 7. Acceptance evidence required before implementation

- Catalog enum and payload schema validation tests for every event type/version.
- Atomic rollback tests for privileged mutation + DomainEvent + AdminSecurityAudit.
- Login tests proving no session/cookie issuance before successful audit commit.
- Negative tests proving ordinary roles cannot read global audit and runtime cannot update/delete audit rows.
- Privacy tests scanning serialized payloads and rendered UI for prohibited fields.
- Rate/volume tests for denial telemetry and proof that telemetry failure does not convert a denial into success or block ordinary reads.
- Export tests proving event emission contains metadata only, not exported patient rows or secrets.
- Migration reconciliation proving legacy `admin.*` evidence is preserved and old records are not rewritten.
- Operational evidence for retention, key rotation, backup/restore, tamper detection and audit-read access.

All are acceptance criteria only; none were run in this documentation step.

## 8. Product decision recorded

**PR-READ-01 — resolved (2026-11-19):** the product owner rejected auditing mass card lookup/search. This exclusion applies even to large result sets and cross-scope searches. The initial privileged-read audit scope is limited to successful exports/downloads of patient-identifying or medical-card data and report/manifest generation or download that exports such data outside the normal object-detail UI. Ordinary views, routine search, bulk lookup, dashboards and object timelines are not AdminSecurityAudit events by default. Reconsideration requires a new explicit product decision.

Retention periods, HMAC/IP policy and tamper-evidence controls are not product defaults to guess; they remain technical/operational gates for a later evidence-based decision.

## 9. Non-goals

- No implementation, DDL, migration, API/UI change or new session registry.
- No second business event stream or RequestEvent.
- No new incident entity.
- No durable row for every 401/403.
- No claim that current runtime behavior already satisfies this target catalog.


## 10. Retention and integrity proposal

See `UX-06-AUDIT-RETENTION-INTEGRITY-PROPOSAL.md` for proposed event retention classes (without guessed durations), legal-hold/purge lifecycle, approved exclusion of raw IP/identity digests, per-event HMAC and externally signed checkpoint design. This is a technical/operational proposal only; retention schedules, key custody, checkpoint infrastructure and business-data export audit controls remain unapproved.


## 11. Audit access and export policy proposal

See `UX-06-AUDIT-ACCESS-AND-EXPORT-POLICY-PROPOSAL.md`. It retains admin-only feed/detail read access, prohibits export of the AdminSecurityAudit feed entirely, and confirms that ordinary feed/detail reads are not logged. Separate business-data export events remain in scope under section 2.3.


## 12. Product decision — admin audit-feed export approved (2026-11-19)

Any user with the existing `admin` role may export the AdminSecurityAudit feed with a mandatory non-empty reason. No separate export permission and no second-admin approval are required. Export must remain bounded, field-allowlisted, attributable and protected; the export outcome is recorded. This does not grant export access to branch/archive/CO roles and does not change the audit catalog for business-data exports.


## Owner decision clarification — audit-feed export excluded (2026-11-19)

The product owner rejected export of AdminSecurityAudit itself. Any references in this catalog to successful exports/downloads or report/manifest operations concern **business data leaving ordinary object viewing** (for example, patient-identifying card data or qualifying reports/manifests), not export of the AdminSecurityAudit feed. Do not add audit-feed export events, commands, artifacts or access-log records. The existing business-data export event scope remains unchanged.

## 2026-11-21 — утверждена governance-политика сроков хранения AdminSecurityAudit

Владелец продукта утвердил следующий подход к срокам хранения `AdminSecurityAudit`: конкретные периоды сейчас не устанавливаются, поскольку у организации пока нет утверждённого графика хранения. До ввода системы в эксплуатацию организация должна назначить ответственного за юридические требования, персональные данные и архивное хранение. Этот ответственный организует оценку и утверждает отдельную версионированную политику для каждого класса событий R1–R4. Политика должна определять сроки и начальные точки их исчисления, порядок контролируемого удаления, приостановку удаления при legal hold, периодический пересмотр/снятие hold, а также согласование удаления с резервными копиями и восстановлением. До утверждения такой политики автоматическое удаление записей аудита запрещено; произвольное бессрочное хранение и ad hoc удаление также не допускаются. Конкретные сроки и юридические основания остаются открытым организационным вопросом. Решение не разрешает реализацию или изменение БД.


## 2026-11-21 — logout telemetry approved

Logout event decision (owner approval, 2026-11-21): record an explicit user-initiated logout as a best-effort security event. Failure or unavailability of telemetry must not delay or prevent local/session credential clearing and must not change the logout outcome. This is not a mandatory transactional audit write and must not become an availability dependency. The event is not guaranteed when a user closes a browser, loses connectivity, or otherwise leaves without invoking logout. It does not imply a new session registry. This decision closes the product question; implementation details remain subject to the bounded telemetry contract.


## 2026-11-21 — approved denial-telemetry aggregation dimensions

Owner decision (2026-11-21): for selected high-risk authorization denials and threshold crossings, bounded security telemetry may aggregate repeated events using only the operation type/code and denial-reason code. The aggregation must not use or retain actor identity, attempted identity, source IP, IP digest, or other per-user/network correlation key. Operation and denial reason are controlled categories, not free-form request data. This defines the approved grouping dimensions; aggregation windows, thresholds, maximum rate/volume, retention and unavailable-store behavior remain technical implementation parameters to be specified within the existing best-effort policy.


## 2026-11-21 — denial telemetry aggregation window approved

Owner decision (2026-11-21): use fixed 15-minute aggregation windows for selected high-risk authorization-denial telemetry. Within each window, aggregate by the already approved controlled operation type/code and denial-reason code. Store an aggregate count rather than one row per repeated denial. Do not add actor, attempted-identity, source-IP, IP-digest or other per-user/network correlation dimensions. Window boundaries and implementation must avoid unbounded storage or request-path blocking. Other limits (thresholds, maximum rate/volume and retention) remain to be specified as technical parameters under the best-effort telemetry policy.


## 2026-11-21 — denial telemetry threshold approved

Owner decision (2026-11-21): the threshold for selected high-risk authorization-denial telemetry is five matching denials within one fixed 15-minute aggregation window. A matching group is defined by the approved controlled operation type/code and denial-reason code. The threshold is reached at the fifth matching denial; record one aggregate event for that group/window rather than one event per attempt. Do not add actor, attempted-identity, source-IP, IP-digest or other per-user/network correlation dimensions. Exact counter update/finalization mechanics and maximum rate/volume remain implementation parameters; telemetry remains best-effort and must not block or change the authorization result.


## 2026-11-21 — denial telemetry hourly cap approved

Owner decision (2026-11-21): cap selected high-risk authorization-denial aggregate telemetry at 100 aggregate events per application-wide hour. Once the cap is reached, additional telemetry aggregates are not persisted for the remainder of that hour. This cap applies only to best-effort denial telemetry; it must not block, delay or change business operations or authorization outcomes. The cap does not relax the approved 15-minute aggregation window, five-matching-denial threshold, operation/reason grouping dimensions, or prohibition on per-user/network correlation. Exact concurrency-safe enforcement and hour-boundary mechanics remain implementation details.


## 2026-11-21 — denial telemetry retention approved

Owner decision (2026-11-21): retain bounded aggregate authorization-denial telemetry for 30 calendar days, then automatically delete it. This retention applies only to the separate best-effort denial telemetry, not to AdminSecurityAudit, DomainEvent or authoritative business records. Cleanup must be bounded and must not block or alter request processing. The previously approved 15-minute aggregation window, threshold of five matching denials and application-wide cap of 100 aggregate events per hour remain in force.


## 2026-11-21 — denial telemetry unavailable-store behavior approved

Owner decision (2026-11-21): if the separate best-effort denial-telemetry store is unavailable or a telemetry write fails, do not retry and do not enqueue the event for later delivery; the telemetry event is dropped. The failure must not block or delay the request, change the authorization/business-operation result, or expose internal technical details to the user. This rule applies only to optional denial telemetry and does not weaken atomic fail-closed requirements for mandatory DomainEvent/AdminSecurityAudit writes.


## 2026-11-21 — telemetry-loss monitoring approved

Owner decision (2026-11-21): do not create a separate user-interface notification for dropped or unavailable optional denial telemetry. Operational monitoring must track telemetry-store availability and telemetry write failures using technical health signals only, without collecting or exposing personal data. This monitoring is separate from AdminSecurityAudit and must not become a request-path dependency or block authorization/business operations. The decision does not authorize a new user-facing alert, per-event retry, or deferred delivery queue.

## 2026-11-21 — operational telemetry monitoring access approved

Owner decision (2026-11-21): technical monitoring metrics and telemetry-store/write-failure diagnostics are available only to infrastructure administrators and personnel explicitly responsible for system operations. Ordinary MedArchive users, including application-role `admin`, must not see internal infrastructure metrics or telemetry storage diagnostics through the application. This is an infrastructure/operations access boundary, separate from application authorization and AdminSecurityAudit. Exact infrastructure identities and grants depend on deployment topology; implementation remains unauthorized.

## 2026-11-21 — technical monitoring access-view audit boundary approved

Owner decision (2026-11-21): viewing technical monitoring metrics is not recorded in MedArchive `AdminSecurityAudit`. Access to monitoring is governed by infrastructure controls. If the infrastructure requires an access log for monitoring views, it is a separate infrastructure/operations log, outside MedArchive business/security audit. This does not expand application audit events or grant application users access to monitoring. Implementation remains unauthorized.

## 2026-11-21 — no telemetry-outage notifications approved

Owner decision (2026-11-21): do not send separate notifications or alerts to infrastructure/operations personnel for denial-telemetry store unavailability or prolonged telemetry loss. Technical monitoring may retain passive health metrics/diagnostics under the approved access boundary, but it must not generate an alerting/notification workflow for this condition. This does not change drop-on-failure behavior, the absence of retries/queues, or the non-blocking nature of optional telemetry.

## 2026-11-21 — telemetry health is not exposed in the application UI

Owner decision (2026-11-21): do not expose denial-telemetry storage availability, health, write failures, or monitoring diagnostics in the MedArchive application interface, including to users with the application `admin` role. Technical health information remains outside the application UI and is available only through the separately controlled infrastructure/operations monitoring boundary. The application `admin` role does not grant access to that monitoring.

## 2026-11-21 — no separate AdminSecurityAudit integrity-verification feature

Owner decision (2026-11-21): a separate feature for cryptographic integrity verification of `AdminSecurityAudit` is not required for the target product. Do not introduce audit hash chains, MACs, signatures, checkpoints, verification jobs, integrity dashboards, or related UI/API solely for this purpose. Existing append-only/write-permission controls, mandatory atomic audit writes, retention governance, and backup/restore procedures remain separate requirements and are not waived. Key custody/rotation remains deferred and must not be treated as an approved implementation task.

## 2026-11-21 — failed login attempts are not persisted as audit or telemetry events

Owner decision (2026-11-21): do not persist individual failed-login attempts or login-throttle denials as `AdminSecurityAudit`, `DomainEvent`, or bounded security-telemetry events. The candidate telemetry event types `authn.login_failed` and `authn.login_throttled` are excluded from the target event catalog and must not be implemented. The login-throttle mechanism remains in place and may maintain only the narrowly required internal counters/state under its separate contract; those counters/bucket keys are not audit events and must not be copied into audit or telemetry. This decision does not alter successful-login auditing or the previously approved bounded telemetry for selected high-risk authorization denials. It also does not approve recording raw IPs, attempted usernames, or identity digests.

## 2026-11-21 — ordinary session expiration is not persisted as an event

Owner decision (2026-11-21): do not create `AdminSecurityAudit`, `DomainEvent`, or bounded security-telemetry records when a user session expires normally due to timeout or ordinary expiry. Do not introduce a session-expiration event or session registry solely for this purpose. Successful-login auditing and the approved best-effort event for explicit user-initiated logout remain unchanged. Rejected requests using expired/invalid sessions continue to follow the separately approved rule: no per-request session-rejection telemetry is introduced unless a new explicit product decision is made.

## 2026-11-21 — administrative session termination is not separately audited

Owner decision (2026-11-21): when an account is deactivated or otherwise administratively disabled, audit the account-management action under its existing mandatory `AdminSecurityAudit` contract. Do not create a separate event for each session that is consequently invalidated or terminated. This does not change the approved handling of successful login, explicit user-initiated logout, or ordinary session expiration.

## 2026-11-21 — успешное самостоятельное изменение пароля

Владелец продукта утвердил регистрацию успешного самостоятельного изменения пароля сотрудником как отдельного события безопасности `authn.password_changed` в `AdminSecurityAudit`. В событии допускаются только необходимые безопасные метаданные; пароль, его хеш, reset/recovery secrets, токены и иные секретные данные не сохраняются. Неудачные попытки самостоятельного изменения пароля отдельно не журналируются. Это решение не меняет ранее утверждённое исключение неудачных попыток входа и отказов login throttle. Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — атомарность самостоятельного изменения пароля и аудита

Владелец продукта утвердил применение общего правила обязательной атомарной записи к самостоятельному изменению пароля: изменение пароля и обязательная запись `AdminSecurityAudit` с событием `authn.password_changed` выполняются в одной транзакции. Если запись события не удаётся, вся операция изменения пароля откатывается, новый пароль не вступает в силу, а прежний пароль остаётся действующим. Событие не должно записываться отдельно после успешного изменения пароля. Это решение не меняет исключение неудачных попыток смены пароля из журналирования. Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — аудит успешного административного сброса пароля

Владелец продукта утвердил регистрацию успешного административного сброса/замены учётных данных отдельным обязательным событием `admin.credential_reset` в `AdminSecurityAudit`. Событие содержит только идентификатор целевой учётной записи и безопасный код способа сброса. Пароли, их хеши, reset/recovery secrets, токены и иные секретные данные запрещено сохранять. Неудачные попытки административного сброса отдельно не журналируются. Запись события и изменение учётных данных выполняются атомарно по общему правилу обязательного аудита: при ошибке записи вся операция откатывается. Это согласуется с уже существующей строкой каталога `admin.credential_reset`; данное решение уточняет её payload и поведение. Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — завершение активных сессий при смене/сбросе пароля

Владелец продукта утвердил следующие правила:
- При успешной самостоятельной смене пароля все остальные активные сессии этой учётной записи должны стать недействительными; текущая сессия, в которой выполнена смена, сохраняется.
- При успешном административном сбросе пароля все активные сессии целевой учётной записи должны стать недействительными.
- Изменение пароля, обязательная запись соответствующего `AdminSecurityAudit` event и применение инвалидации сессий должны иметь согласованную атомарную семантику: операция не считается успешной, пока обязательные изменения и аудит не зафиксированы. Истечение/инвалидация сессий не создаёт отдельные события на каждую сессию.
- События `authn.password_changed` и `admin.credential_reset` остаются единственными обязательными audit-событиями для соответствующих операций; отдельное событие на каждую завершённую сессию не вводится.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — подтверждение действующего пароля при самостоятельной смене

Владелец продукта утвердил следующие правила:
- При самостоятельной смене пароля сотрудник обязан подтвердить операцию вводом действующего пароля.
- При административном сбросе пароля действующий пароль целевой учётной записи не требуется; это отдельная привилегированная операция.
- Эти правила не меняют ранее согласованные требования к обязательному аудиту, атомарности изменения учётных данных и инвалидации остальных активных сессий.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — повторная проверка пароля администратора при сбросе чужого пароля

Владелец продукта утвердил требование: непосредственно перед выполнением административного сброса пароля другого сотрудника администратор должен повторно подтвердить свою личность вводом собственного действующего пароля. Это дополнительная проверка для привилегированной операции и не заменяет обычную серверную проверку активной учётной записи, роли и полномочий администратора. Действующий пароль целевой учётной записи не требуется. Повторная проверка не меняет ранее согласованные правила обязательной атомарной записи `admin.credential_reset` и инвалидации всех активных сессий целевой учётной записи.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — ограничение неудачных повторных подтверждений пароля администратора

Владелец продукта утвердил применение существующего механизма защиты от частых попыток входа к неудачным повторным подтверждениям пароля администратора перед административным сбросом пароля другого сотрудника. Отдельный механизм блокировки/счётчиков для этой операции не создаётся. Неудачные повторные подтверждения не записываются в `AdminSecurityAudit`, `DomainEvent` или bounded security telemetry как отдельные события; действующие ограничения на сохранение неудачных попыток входа остаются в силе. Успешный административный сброс продолжает фиксироваться обязательным событием `admin.credential_reset` с ранее утверждённой атомарностью и инвалидацией активных сессий целевой учётной записи.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — fail-closed инвалидация сессий при административном сбросе пароля

Владелец продукта утвердил строгое fail-closed поведение административного сброса пароля: операция считается успешной только если все активные сессии целевой учётной записи успешно сделаны недействительными. Если инвалидация хотя бы одной активной сессии не удалась, административный сброс не завершается успешно: изменение пароля и обязательная запись `admin.credential_reset` должны быть отменены/откачены в рамках согласованной атомарной операции. Нельзя оставлять новое значение пароля действующим при сохранении хотя бы одной прежней активной сессии. Требование дополняет ранее утверждённое обязательное завершение всех сессий целевой учётной записи и повторное подтверждение собственного пароля администратора.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — уведомление сотрудника об административном сбросе пароля

Владелец продукта утвердил уведомление целевого сотрудника об успешном административном сбросе пароля при его следующем успешном входе в МедАрхив. Уведомление должно сообщать только факт административного сброса; личность администратора не раскрывается. Внешняя отправка email/SMS и иные внешние каналы уведомления не вводятся. Уведомление не заменяет обязательное событие `admin.credential_reset` в `AdminSecurityAudit` и не меняет атомарность сброса, обязательную инвалидацию всех активных сессий и fail-closed поведение при ошибке инвалидации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — обязательная смена пароля после административного сброса

Владелец продукта утвердил обязательную смену пароля целевым сотрудником после административного сброса. При следующем успешном прохождении аутентификации сотрудник должен сначала установить собственный новый пароль; до завершения этой процедуры доступ к остальным функциям МедАрхива не предоставляется. Уведомление о факте административного сброса показывается в рамках этого следующего входа; личность администратора не раскрывается, внешние каналы уведомления не используются. Установка нового пароля выполняется по правилам самостоятельной смены: сотрудник задаёт собственный пароль, но повторно вводить сброшенный административный пароль как действующий пароль не требуется. После успешной установки нового пароля обязательное событие `authn.password_changed` записывается атомарно с изменением пароля; при ошибке обязательной записи операция смены пароля откатывается, а ограниченный режим до завершения смены сохраняется. Это не отменяет обязательную инвалидацию всех прежних сессий при административном сбросе.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — срок действия административного временного пароля

Владелец продукта утвердил, что для временного пароля, установленного администратором, отдельный срок действия не задаётся. После успешной аутентификации с таким паролем сотрудник остаётся в ограниченном режиме: доступ к остальным функциям МедАрхива закрыт до самостоятельной установки нового пароля. Для завершения обязательной смены сотрудник не должен повторно вводить административный временный пароль как действующий пароль. Временный пароль передаётся сотруднику безопасным способом вне МедАрхива; внешняя доставка самим приложением не вводится. При успешной смене обязательно атомарно записывается `authn.password_changed`; при ошибке записи смена откатывается и ограниченный режим сохраняется.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — генерация административного временного пароля

Владелец продукта утвердил автоматическую генерацию временного пароля системой при административном сбросе. Администратор не задаёт временный пароль вручную. Значение генерируется криптографически стойким случайным способом, показывается администратору только один раз и не может быть повторно просмотрено через интерфейс или API. В хранилище учётных данных сохраняется только парольный хеш; открытое значение временного пароля не записывается в журналы, события, telemetry или иные постоянные хранилища МедАрхива. Передача временного пароля сотруднику выполняется безопасным способом вне МедАрхива. После входа с временным паролем сотрудник остаётся в ограниченном режиме до самостоятельной установки нового пароля; отдельный срок действия временного пароля не устанавливается. Все ранее утверждённые требования к аудиту, инвалидации сессий и обязательной смене пароля сохраняются.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — утраченный или недоступный для повторного просмотра временный пароль

Владелец продукта утвердил, что открытое значение временного пароля после его однократного показа администратору повторно не отображается и не восстанавливается. Если администратор потерял пароль или закрыл страницу до его передачи сотруднику, он выполняет новый административный сброс. Новый сброс генерирует новый временный пароль и делает предыдущий временный пароль недействительным. Все активные сессии целевой учётной записи при каждом успешном сбросе подлежат инвалидации в рамках обязательной атомарной операции; если инвалидация хотя бы одной сессии не удалась, сброс и обязательная запись аудита откатываются. Каждый успешно завершённый повторный сброс регистрируется отдельным событием `admin.credential_reset` без включения пароля, его хеша или иных секретов в payload. Хранение открытого временного пароля для повторного просмотра запрещено.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — уведомление после повторного административного сброса

Владелец продукта утвердил, что после повторного административного сброса сотруднику при следующем успешном входе показывается одно уведомление о последнем успешном сбросе. Уведомление не раскрывает историю предыдущих сбросов и личность администратора. Несколько последовательных сбросов до следующего входа не приводят к показу списка или серии уведомлений: показывается только факт последнего сброса. Это правило дополняет ранее утверждённое уведомление о факте административного сброса и обязательную смену временного пароля; внешние каналы уведомления не вводятся.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — запрет административного сброса собственного пароля

Владелец продукта утвердил, что административный механизм сброса пароля нельзя использовать для собственной учётной записи администратора. Администратор изменяет собственный пароль только через обычную процедуру самостоятельной смены с обязательным подтверждением действующего пароля и атомарной записью события `authn.password_changed`. Привилегированная операция `admin.credential_reset` доступна только для другой целевой учётной записи. Это ограничение не вводит отдельную процедуру восстановления доступа; recovery/break-glass остаётся отдельным ранее выделенным процессом.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — административный сброс пароля другой учётной записи администратора

Владелец продукта утвердил, что активный администратор может выполнить административный сброс пароля другой активной учётной записи администратора. Запрет административного сброса собственной учётной записи сохраняется. Следовательно, административный сброс пароля другого администратора допускается только при условии, что инициатор и целевая учётная запись различны и обе активны; инициатор проходит повторное подтверждение собственного действующего пароля и все общие проверки полномочий. Сохраняются автоматическая генерация временного пароля, однократный показ, обязательная инвалидация всех активных сессий целевой учётной записи, уведомление о последнем сбросе при следующем входе, обязательная смена временного пароля и атомарная запись `admin.credential_reset`. Никаких специальных исключений из общих правил безопасности для учётных записей администраторов не вводится.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — аварийное восстановление доступа единственного активного администратора

Владелец продукта утвердил отдельную аварийную процедуру (*break-glass*) на случай, когда в организации остаётся только один активный администратор и он теряет доступ к своей учётной записи. Восстановление выполняет уполномоченный инфраструктурный специалист вне приложения МедАрхив. Обычный административный сброс через интерфейс не расширяется и не получает исключения для сброса собственного пароля. Действия аварийной процедуры подлежат обязательной фиксации; конкретный состав доказательств, место и формат журналирования, порядок подтверждения полномочий и операционный runbook должны быть определены в отдельной эксплуатационной спецификации до ввода в эксплуатацию. Процедура не должна превращаться в постоянный HTTP/API endpoint или штатный пользовательский workflow.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — создание учётной записи другого администратора

Владелец продукта утвердил возможность создания активным администратором учётной записи другого администратора. Создаваемая учётная запись не получает активный статус до завершения предусмотренной процедуры первоначальной активации. Факт создания фиксируется в `AdminSecurityAudit` обязательной записью в рамках атомарной операции. Создатель не может использовать эту операцию для назначения себе дополнительных полномочий или изменения собственной роли. Самостоятельное повышение привилегий через создание/изменение учётной записи запрещено. Конкретные шаги первоначальной активации и допустимый способ первоначальной передачи секрета должны быть определены в технической спецификации до реализации; секреты не включаются в аудит.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — деактивация другой учётной записи администратора

Владелец продукта утвердил, что активный администратор может деактивировать учётную запись другого администратора. Деактивация собственной учётной записи через эту операцию запрещена. Нельзя деактивировать последнюю активную учётную запись администратора организации: перед такой деактивацией должна быть создана и активирована другая учётная запись администратора. Проверка наличия другой активной учётной записи должна выполняться атомарно с деактивацией, чтобы параллельные административные операции не могли оставить организацию без активного администратора. Действуют общие правила деактивации: обязательная запись действия в `AdminSecurityAudit`, аннулирование активных сессий целевой учётной записи и атомарный откат всей операции при невозможности выполнить обязательные действия. Отдельные события на каждую аннулированную сессию не создаются.

Изменена только Markdown-документация; реализация не разрешена.
