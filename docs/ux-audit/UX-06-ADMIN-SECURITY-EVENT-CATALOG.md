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

## 2026-11-21 — изменение роли другого сотрудника

Владелец продукта утвердил возможность активного администратора изменять роль другого сотрудника в пределах закрытого набора ролей `branch`, `central_office`, `archive` и `admin`. Изменение собственной роли через эту операцию запрещено. Назначение роли `admin` требует повторного подтверждения действующего пароля инициатора. Проверка того, что операция не оставит организацию без активного администратора, выполняется атомарно с изменением роли; нельзя понизить или лишить активности последнюю действующую административную учётную запись так, чтобы в организации не осталось ни одного активного администратора. Факт успешного изменения роли обязательно фиксируется событием `admin.user_role_changed` в `AdminSecurityAudit` в одной транзакции с изменением. При невозможности обязательной записи аудита или выполнения защитных проверок вся операция откатывается. Это решение не разрешает самоназначение роли `admin` или обход отдельного запрета на изменение собственной роли.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — изменение организационной привязки сотрудника

Владелец продукта утвердил возможность активному администратору изменять организационную привязку другого сотрудника: переводить его между филиалами, центральным офисом и архивом в соответствии с назначенной ролью. Новая привязка должна ссылаться на существующую и активную организационную единицу допустимого типа; несовместимая комбинация роли и организационной области отклоняется. Изменение организационной привязки обязательно фиксируется в `AdminSecurityAudit` с идентификатором целевой учётной записи и безопасными кодами прежней и новой привязки. Изменение и обязательная запись аудита выполняются атомарно; при ошибке проверки или записи аудита операция полностью откатывается. Это решение не меняет роль сотрудника и не предоставляет дополнительных полномочий само по себе. Детальные ограничения на перевод сотрудника с незавершёнными операциями/процессами должны быть учтены в техническом контракте до реализации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — редактирование персональных данных сотрудника администратором

Владелец продукта утвердил возможность активному администратору редактировать профиль другого сотрудника, включая ФИО и контактные данные. Изменение собственного профиля администратором через эту административную операцию запрещено. Пароль и любые учётные секреты не являются редактируемыми полями профиля и изменяются только через предусмотренные процедуры управления учётными данными. Изменение профиля другого сотрудника обязательно фиксируется в `AdminSecurityAudit`; запись должна содержать идентификатор целевой учётной записи и безопасное описание изменённых категорий полей, без включения прежних/новых значений персональных данных и любых секретов. Изменение профиля и обязательная запись аудита выполняются атомарно; при ошибке проверки или записи аудита вся операция откатывается. Допустимые поля профиля, ограничения на их формат и точный allowlist категорий для аудита определяются в техническом контракте до реализации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — запрет самостоятельного редактирования профиля сотрудником

Владелец продукта отклонил возможность самостоятельного редактирования сотрудником персональных данных своего профиля. Изменение ФИО и контактных данных выполняется только активным администратором в отношении другого сотрудника по утверждённой административной процедуре. Самостоятельное изменение этих полей через профиль, API или иные пользовательские поверхности не допускается. Ранее утверждённые ограничения сохраняются: администратор не редактирует собственный профиль этой операцией; пароли и учётные секреты изменяются только через отдельные процедуры управления учётными данными.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — неизменяемость логина сотрудника

Владелец продукта утвердил запрет на изменение логина после создания учётной записи. Если требуется другой логин, активный администратор создаёт новую учётную запись и деактивирует прежнюю с использованием предусмотренных процедур управления учётными записями. Исторические действия и записи аудита остаются связанными с исходной учётной записью; автоматическое объединение учётных записей, перепривязка истории или перенос авторства на новую учётную запись не выполняются. Новая учётная запись проходит обычную процедуру первоначальной активации и получает права только в соответствии с отдельно назначенными ролью и организационной привязкой. До реализации необходимо учесть незавершённые операции и процессы старой учётной записи в техническом контракте деактивации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — отсутствие email-адресов сотрудников в профиле

Владелец продукта подтвердил, что адреса электронной почты сотрудников не нужны и не должны храниться в профиле МедАрхива. Поле email сотрудника не входит в целевую модель профиля; администратор не может добавлять или редактировать такой адрес. Email не используется для уведомлений, подтверждения личности или восстановления доступа. Внешние email-уведомления не вводятся этим решением.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — утверждённый состав профиля сотрудника

Владелец продукта утвердил закрытый состав данных профиля сотрудника МедАрхива: ФИО, логин, роль, организационная привязка и статус учётной записи (активна/деактивирована). Иные персональные и контактные поля в профиль не входят. В частности, номера телефонов и адреса электронной почты сотрудников не хранятся. Логин неизменяем после создания учётной записи. ФИО и контактные данные не редактируются самим сотрудником; при этом контактные данные в целевой модели отсутствуют. Административные изменения разрешены только в пределах ранее утверждённых полномочий: администратор может изменять ФИО, роль и организационную привязку другого сотрудника, а также управлять статусом учётной записи через отдельные процедуры. Администратор не может изменять собственный профиль этой административной операцией. Пароли и учётные секреты не являются полями профиля и управляются только отдельными процедурами. Перечень полей и операций должен быть отражён в целевой модели данных, API-контрактах, UI и проверках доступа до реализации.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — фотография сотрудника не входит в профиль

Владелец продукта подтвердил, что фотография сотрудника в МедАрхиве не нужна. Фотоизображения сотрудников не собираются и не хранятся; поле фотографии, загрузка/замена изображения и связанные с ними API, UI и процессы в целевую модель не входят. Состав профиля остаётся закрытым: ФИО, неизменяемый логин, роль, организационная привязка и статус учётной записи. Самостоятельное редактирование профиля запрещено; изменения ФИО, роли и организационной привязки выполняются администратором в пределах ранее согласованных полномочий.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — деактивация сотрудника при наличии незавершённых операций

Владелец продукта утвердил, что наличие незавершённых операций не препятствует деактивации учётной записи сотрудника. Деактивация не удаляет и автоматически не отменяет незавершённые операции; история действий сотрудника сохраняется. Дальнейшая судьба и продолжение таких операций определяются правилами соответствующего бизнес-процесса. После деактивации сотрудник теряет доступ к системе; продолжение допустимых операций выполняют другие уполномоченные активные сотрудники в соответствии с их ролью, организационной областью и правилами конкретного процесса. До реализации необходимо определить технические ограничения и безопасную передачу ответственности за операции, для которых требуется участие или авторство сотрудника.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — передача незавершённых операций деактивированного сотрудника

Владелец продукта утвердил, что при деактивации сотрудника незавершённые операции не переназначаются автоматически другому сотруднику. Другой активный и уполномоченный сотрудник может принять такую операцию в работу только предусмотренным правилами соответствующего бизнес-процесса способом. Первоначальный автор, история действий и фактические участники сохраняются; принятие операции другим сотрудником не переписывает авторство и не переносит исторические действия. Необходимо предусмотреть явную фиксацию фактического продолжения/принятия ответственности там, где это требуется контрактом конкретного процесса.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — сохранение учётной записи после деактивации

Владелец продукта утвердил, что запись учётной записи деактивированного сотрудника сохраняется в МедАрхиве бессрочно и не удаляется штатными операциями. Сохраняются логин, ФИО, идентификатор учётной записи и связи с историческими действиями, бизнес-операциями и аудитом. Деактивация прекращает доступ, но не удаляет историческую идентичность и авторство. Повторная активация допускается только по административной процедуре уполномоченным активным администратором с соблюдением действующих проверок роли, организационной привязки и наличия активного администратора. Правовые основания и применимость сроков хранения персональных данных должны быть подтверждены организацией до production; утверждение о бессрочном хранении является целевым правилом продукта и не отменяет обязательные требования законодательства.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — повторная активация деактивированной учётной записи

Владелец продукта утвердил возможность повторной активации ранее деактивированной учётной записи активным администратором. При повторной активации используется прежняя учётная запись и неизменяемый логин; создание новой учётной записи только ради возвращения сотрудника не требуется. Сохраняются прежний идентификатор учётной записи, исторические действия, авторство и связи с аудитом. До активации администратор должен назначить/проверить актуальные роль и организационную привязку, а система — проверить их допустимость, отсутствие конфликта идентичности и соблюдение инварианта наличия хотя бы одного активного администратора. Активация и обязательная запись события AdminSecurityAudit выполняются атомарно; доступ предоставляется только после успешного commit. Конкретные коды события и технический контракт определяются в спецификации управления учётными записями.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — обязательная причина деактивации учётной записи

Владелец продукта утвердил обязательное указание причины при административной деактивации учётной записи сотрудника. Причина выбирается из закрытого контролируемого перечня; предварительные категории: увольнение, длительное отсутствие, организационные изменения, другое. Свободный комментарий остаётся необязательным. Выбранный код причины фиксируется в обязательной записи AdminSecurityAudit; произвольный текст комментария не должен попадать в структурированные поля события и подлежит отдельному ограничению/санитизации в техническом контракте. Точный закрытый перечень кодов и правила комментария уточняются в технической спецификации до реализации. Деактивация и обязательная запись аудита остаются атомарными.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — отдельный журнал истории изменений профиля сотрудника не создаётся

Владелец продукта подтвердил, что отдельный журнал истории изменений данных сотрудника не нужен. Не вводить отдельную таблицу, журнал, ленту или самостоятельный интерфейс истории профиля. Административные изменения ФИО, роли и организационной привязки фиксируются в существующем AdminSecurityAudit в рамках ранее утверждённых правил; отдельное дублирование истории не создаётся.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — просмотр истории учётной записи из её карточки

Владелец продукта утвердил возможность для активного администратора просматривать из карточки учётной записи историю относящихся к ней изменений и административных действий. Источник данных — существующий AdminSecurityAudit; отдельный журнал, таблица или самостоятельная система истории не создаются. Представление ограничивается событиями, связанными с выбранной учётной записью, и теми полями, которые разрешены действующим allowlist для просмотра аудита. Доступ проверяется на сервере для каждого запроса до формирования ответа; остальные роли не получают эту историю ни через UI, ни через прямые URL/API. Это представление не расширяет общие права чтения AdminSecurityAudit, не разрешает экспорт аудита и не раскрывает секреты или избыточные персональные данные. Просмотр истории не создаёт отдельного audit event.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — отображение исполнителя в истории учётной записи

Владелец продукта утвердил отображение в истории учётной записи сведений о том, кто выполнил административное действие. Для каждого отображаемого события показываются ФИО инициатора (на момент события, если это значение доступно в сохранённом событии/аудитном представлении), дата и время, тип действия и результат. Идентификатор учётной записи инициатора сохраняется как надёжная внутренняя связь; отображаемое имя не должно подменять историческое авторство. Если историческое ФИО не зафиксировано, интерфейс не должен угадывать его по текущему профилю; следует показать доступное безопасное обозначение инициатора. Представление остаётся доступным только активному администратору, использует существующий AdminSecurityAudit и его allowlist, не раскрывает секреты/избыточные PII и не разрешает экспорт аудита. Точный состав полей события и поведение для старых записей без snapshot имени уточняются в технической спецификации.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — фильтрация истории учётной записи по типу действия

Владелец продукта утвердил фильтрацию истории учётной записи по типу административного действия. Активный администратор может ограничить отображаемый список выбранным типом события (например, изменение роли, сброс пароля, деактивация или повторная активация). Фильтрация применяется только к уже разрешённым для просмотра событиям выбранной учётной записи и не расширяет права доступа, состав доступных полей или область AdminSecurityAudit. Фильтрация выполняется на сервере после проверки полномочий и до формирования результата; количество, наличие и значения недоступных событий не должны раскрываться. Отдельный журнал, экспорт или аудит самого факта фильтрации не вводятся.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — фильтрация истории учётной записи по периоду

Владелец продукта утвердил возможность фильтровать историю учётной записи по временному диапазону: дата/время начала и дата/время окончания. Диапазон применяется совместно с фильтром типа действия и только к событиям, уже разрешённым активному администратору для выбранной учётной записи. Проверка полномочий и ограничение области данных выполняются на сервере до формирования результата; временной фильтр не должен раскрывать наличие или количество недоступных событий. Границы диапазона и отображение времени используют согласованные для МедАрхива правила хранения timestamp и представления локального времени; точные технические границы включения начала/окончания закрепляются в API/UX-спецификации. Отдельный журнал или аудит применения фильтра не вводится.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — просмотр полной истории учётной записи за всё время

Владелец продукта утвердил, что активный администратор может просматривать полную историю относящихся к выбранной учётной записи событий AdminSecurityAudit за всё время, без обязательного ограничения временным диапазоном. Фильтр периода остаётся доступным как дополнительное сужение результатов и может применяться совместно с фильтром типа действия; отсутствие заданного периода означает просмотр всей доступной истории. Представление по умолчанию может открываться с последними событиями для удобства, но администратор должен иметь явную возможность перейти ко всей истории. Доступ по-прежнему ограничен активными администраторами, только событиями выбранной учётной записи и разрешёнными allowlist полями; серверная проверка полномочий и области данных выполняется до формирования результата. Это не разрешает экспорт AdminSecurityAudit, не создаёт отдельный журнал и не требует аудита самого просмотра или применения фильтров.

## 2026-11-21 — деактивация организационной единицы

Владелец продукта утвердил порядок деактивации филиала или иной организационной единицы. Деактивация запрещена, пока к единице привязаны активные учётные записи сотрудников или остаются незавершённые операции. До деактивации сотрудники должны быть переведены на допустимые активные организационные привязки, а незавершённые операции — завершены либо обработаны предусмотренным соответствующим бизнес-процессом способом; автоматическое удаление или произвольная отмена операций не допускаются. После выполнения предварительных условий активный администратор может деактивировать организационную единицу. Исторические данные, события, связи и авторство сохраняются; физическое удаление организационной единицы штатными операциями запрещено. Деактивация и обязательная запись AdminSecurityAudit выполняются атомарно. Проверки должны учитывать конкурентные изменения сотрудников и операций, чтобы нельзя было обойти условия деактивации.


## 2026-11-21 — повторная активация организационной единицы

Владелец продукта утвердил возможность повторной активации ранее деактивированного филиала или другой организационной единицы уполномоченным активным администратором. Повторная активация выполняется для прежней записи с сохранением прежнего идентификатора; создавать новую организационную единицу только ради возобновления работы не требуется. Исторические данные, связи, события и авторство сохраняются и продолжают относиться к той же организационной единице. До активации проверяются актуальные параметры и допустимость организационной единицы, а также действующие ограничения доступа и целостности. Активация и обязательная запись AdminSecurityAudit выполняются атомарно. Конкретный технический контракт и состав проверок закрепляются в спецификации управления справочными данными.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — изменение названия организационной единицы

Владелец продукта утвердил возможность изменения названия филиала или другой организационной единицы уполномоченным активным администратором без создания новой записи. При переименовании сохраняются прежний идентификатор, связи, история, события и авторство; переименование не считается созданием новой организационной единицы и не меняет её идентичность. В исторических представлениях должно использоваться название, зафиксированное на момент события, если такой snapshot предусмотрен и доступен; текущее название не должно выдаваться за историческое. Изменение названия и обязательная запись AdminSecurityAudit выполняются атомарно. Технические ограничения формата/уникальности названий и способ хранения исторического отображаемого значения определяются в спецификации справочных данных.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — перевод сотрудника в другую организационную единицу

Владелец продукта утвердил возможность перевода сотрудника в другую активную организационную единицу уполномоченным активным администратором. При переводе сохраняются прежняя учётная запись, логин, идентификатор и вся история действий; создавать новую учётную запись не требуется. Новая организационная привязка применяется к последующим операциям после успешного завершения перевода. Исторические действия и операции сохраняют сведения об организационной области, действовавшей на момент их выполнения; перевод не переписывает прошлое авторство или область ответственности. Перевод и обязательная запись AdminSecurityAudit выполняются атомарно. Перевод должен учитывать ранее утверждённые ограничения на деактивацию организационной единицы и правила продолжения незавершённых операций. Технический контракт и проверки допустимости новой привязки определяются в спецификации управления учётными записями.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — просмотр истории организационных переводов сотрудника

Владелец продукта утвердил просмотр истории организационных переводов сотрудника из его карточки активным администратором. История формируется на основании существующего AdminSecurityAudit; отдельный журнал или таблица переводов не создаются. Для доступных событий отображаются прежняя и новая организационная привязка, дата и время перевода, а также ФИО инициатора на момент события, если оно было сохранено. Исторические значения не подменяются текущими данными справочника или профиля сотрудника; если историческое значение недоступно, интерфейс показывает безопасное обозначение и не пытается его восстановить по текущему состоянию. Доступ ограничен активными администраторами и проверяется на сервере до формирования результата. Просмотр истории переводов не расширяет права на глобальный AdminSecurityAudit, не разрешает его экспорт и сам по себе не журналируется. Состав полей, необходимых для отображения исторических организационных значений, закрепляется в технической спецификации.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — деактивация курьера

Владелец продукта утвердил возможность деактивации курьера уполномоченным активным администратором, если курьер больше не должен назначаться на новые отправления. После деактивации курьер недоступен для выбора при создании или изменении назначения курьера в новых отправлениях; уже сохранённые отправления и исторические доставки не переписываются. Исторические связи, сведения о курьере, авторство/факт назначения и события сохраняются, чтобы прежние отправления оставались корректно читаемыми. Деактивация не должна автоматически отменять, переназначать или изменять отправления, в которых курьер уже указан; их дальнейшее выполнение определяется правилами соответствующего процесса. Деактивация и обязательная запись AdminSecurityAudit выполняются атомарно. Физическое удаление курьера штатными операциями не допускается. Точные ограничения для незавершённых отправлений определяются технической спецификацией без потери исторических данных.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — повторная активация курьера

Владелец продукта утвердил возможность повторной активации ранее деактивированного курьера уполномоченным активным администратором. Повторно используется прежняя запись курьера и её идентификатор; создание дубликата не требуется. После успешной активации курьер снова доступен для назначения на новые отправления в соответствии с действующими правилами процесса. Исторические отправления, доставки, назначения и события сохраняются без переписывания. Активация и обязательная запись AdminSecurityAudit выполняются атомарно. Проверки допустимости активации и актуальности записи выполняются на серверной стороне.

Изменена только Markdown-документация; реализация не разрешена.


## 2026-11-21 — минимальный состав данных курьера

Владелец продукта утвердил минимальный состав данных курьера: ФИО и статус (активен / деактивирован). Идентификатор записи используется системой технически. Телефон, email, фотография и иные дополнительные персональные сведения о курьере не собираются и не хранятся; соответствующие поля, формы ввода, загрузки и связанные интерфейсы/API не предусматриваются. Сведения о курьере, необходимые для отображения исторических отправлений и доставок, сохраняются согласно правилам хранения исторических записей.

Изменена только Markdown-документация; реализация не разрешена.

## 2026-11-21 — управление карточками курьеров

Владелец продукта утвердил, что создавать и редактировать карточки курьеров может только активный администратор. Сотрудники филиалов, центрального офиса и архива не управляют карточками курьеров; в рамках своих разрешённых процессов они могут выбирать активного курьера при оформлении отправления. Это правило действует совместно с утверждённым минимальным составом карточки (ФИО и статус; технический идентификатор используется системой), запретом дополнительных персональных данных, деактивацией и повторной активацией курьера. Изменения карточек выполняются с обязательной атомарной записью в AdminSecurityAudit. Реализация не разрешена.

## 2026-11-21 — завершение отправления деактивированным курьером

Владелец продукта утвердил, что деактивация курьера не препятствует завершению уже назначенного ему отправления. Деактивация запрещает новые назначения курьера, но не отменяет и не переназначает автоматически отправления, назначенные до деактивации. Курьер может завершить такое отправление в рамках предусмотренного процесса и действующих правил подтверждения доставки. Историческое назначение и сведения о курьере сохраняются. Реализация не разрешена.

## 2026-11-21 — изменение ФИО курьера

Владелец продукта утвердил возможность изменения ФИО курьера только активным администратором. Изменение ФИО не должно переписывать исторические отправления, доставки и назначения: в историческом представлении сохраняется ФИО курьера на момент соответствующего назначения/события, если такое значение зафиксировано. Текущее ФИО используется для новых назначений и текущих карточек. Историческое авторство и сведения не подменяются текущими значениями. Изменение ФИО и обязательная запись AdminSecurityAudit выполняются атомарно. Реализация не разрешена.

## 2026-11-21 — запрет физического удаления карточек курьеров

Владелец продукта утвердил запрет физического удаления записей курьеров штатными операциями МедАрхива. Запись курьера сохраняется независимо от текущего статуса и наличия новых назначений, поскольку связана с историческими отправлениями, доставками и событиями. Прекращение использования курьера оформляется деактивацией; повторная активация использует ту же запись и идентификатор. Исторические назначения и сведения о курьере не удаляются и не переписываются. Реализация не разрешена.

## 2026-11-21 — закрытие продуктового блока управления курьерами

Владелец продукта подтвердил закрытие продуктового блока управления курьерами. Согласованы создание и редактирование карточек только активным администратором; минимальный состав данных (ФИО и статус, технический идентификатор); изменение ФИО с сохранением исторического значения; деактивация и повторная активация прежней записи; запрет физического удаления; выбор активного курьера уполномоченными сотрудниками при оформлении отправлений; возможность завершить уже назначенное отправление после деактивации. Дополнительные продуктовые вопросы по этому блоку не задаются. Недостающие технические детали определяются при подготовке спецификации без изменения утверждённых правил. Реализация не разрешена.

## 2026-11-21 — управление справочником архивных полок

Владелец продукта утвердил, что создавать, переименовывать, деактивировать и повторно активировать записи архивных полок может только активный администратор. Сотрудники архива используют активные полки для размещения коробок в рамках своих полномочий, но не управляют справочником полок. Это правило не меняет утверждённые ограничения на размещение коробок и сохранение исторических BoxPlacement. Изменения справочника выполняются с обязательной атомарной записью AdminSecurityAudit; физическое удаление и влияние на исторические размещения не предполагаются. Реализация не разрешена.

## 2026-11-21 — условия деактивации архивной полки

Утверждено: архивную полку нельзя деактивировать, пока на ней размещена хотя бы одна коробка с активным BoxPlacement. До деактивации сотрудники архива должны переместить все размещённые коробки на другие активные полки в пределах своих полномочий. После отсутствия активных размещений активный администратор может деактивировать полку. Исторические записи BoxPlacement сохраняются; автоматическое перемещение коробок, закрытие или удаление исторических размещений не выполняется. Проверка отсутствия активных размещений и деактивация должны быть защищены от конкурентных изменений и выполняться атомарно с обязательной записью AdminSecurityAudit. Физическое удаление полки не допускается. Реализация не разрешена.

## 2026-11-21 — повторная активация архивной полки

Утверждено: активный администратор может повторно активировать ранее деактивированную архивную полку. Используется прежняя запись и прежний идентификатор; новая запись не создаётся. Исторические BoxPlacement, события и связи сохраняются без переписывания. После успешной активации полка снова доступна сотрудникам архива для размещения коробок в рамках их полномочий. Активация и обязательная запись AdminSecurityAudit выполняются атомарно. Реализация не разрешена.

## 2026-11-21 — переименование архивной полки

Утверждено: активный администратор может переименовать архивную полку. Используется прежняя запись и идентификатор; новая полка не создаётся. Исторические BoxPlacement, события и связи сохраняются. В исторических представлениях отображается название полки на момент события, если соответствующее историческое значение сохранено; текущее название не должно подменять историческое. Переименование и обязательная запись AdminSecurityAudit выполняются атомарно. Технические ограничения формата и уникальности названий, а также способ хранения исторического отображаемого значения определяются в технической спецификации. Реализация не разрешена.

## 2026-11-21 — запрет физического удаления архивных полок

Утверждено: физическое удаление записей архивных полок штатными операциями МедАрхива запрещено. Если полка больше не используется, активный администратор деактивирует её после выполнения ранее утверждённых условий: на ней не должно оставаться коробок с активным BoxPlacement. Запись полки, её идентификатор, исторические размещения, события и связи сохраняются. Повторная активация прежней записи разрешена по утверждённой процедуре. Реализация не разрешена.

## 2026-11-21 — создание архивных полок

Утверждено: создавать новые записи архивных полок может только активный администратор. Сотрудники архива используют существующие активные полки для размещения коробок в рамках своих полномочий, но не создают новые записи и не управляют справочником. Правило действует совместно с утверждёнными полномочиями администратора по переименованию, деактивации и повторной активации полок, сохранением исторических BoxPlacement и запретом физического удаления. Создание записи полки и обязательная запись AdminSecurityAudit выполняются атомарно. Реализация не разрешена.

## 2026-11-21 — неизменяемость короткого номера коробки

Утверждено: короткий номер является постоянным человекочитаемым идентификатором физической коробки и не изменяется после создания коробки в рамках обычных операций. Сотрудники архива не могут переименовывать коробку или менять её короткий номер. Если обнаружена ошибка в номере, исправление не выполняется произвольным редактированием; для такого случая требуется отдельная контролируемая процедура, которая должна быть согласована до её применения. Настоящее решение не вводит автоматическую перенумерацию, не разрешает изменение номера через P-07 и не меняет исторические связи, циклы, размещения и события коробки. Реализация не разрешена.

## 2026-11-21 — глобальная уникальность короткого номера коробки

Утверждено: короткий номер коробки уникален в пределах всего МедАрхива, а не только отдельного филиала, центрального офиса или архива. Один и тот же номер не может быть назначен другой записи коробки. Уникальность сохраняется для всех записей, включая выведенные из использования и списанные коробки: номер не освобождается и повторно не используется. Это обеспечивает однозначную идентификацию физической коробки и сохраняет корректность исторических связей и событий при перемещениях между локациями. Реализация не разрешена.

## 2026-11-21 — автоматическое присвоение короткого номера коробки

Утверждено: при создании новой физической коробки МедАрхив автоматически присваивает ей короткий номер. Сотрудник не вводит и не выбирает номер вручную. Механизм присвоения должен обеспечивать глобальную уникальность номера в пределах всего МедАрхива, не допускать повторного использования ранее назначенных номеров и быть безопасным при одновременном создании нескольких коробок. После создания номер остаётся неизменяемым согласно ранее утверждённому правилу. Конкретный формат номера и технический механизм генерации определяются в спецификации без изменения этих продуктовых ограничений. Реализация не разрешена.

## 2026-11-21 — полномочия на создание физических коробок

Утверждено: физические коробки могут создавать уполномоченные активные сотрудники филиалов, центрального офиса и архива — каждый в пределах своей операционной области и действующих полномочий. Создание коробки не является исключительной административной операцией; администратор не подменяет обычный процесс создания коробок. При создании МедАрхив автоматически присваивает коробке глобально уникальный короткий номер по ранее утверждённым правилам. Проверка активности учётной записи, роли, организационной области и права на операцию выполняется на серверной стороне. Реализация не разрешена.

## 2026-11-21 — начальная учётная локация новой коробки

Утверждено: при создании физической коробки МедАрхив автоматически устанавливает её начальную учётную локацию по операционной области уполномоченного сотрудника, выполнившего создание: соответствующий филиал, центральный офис или архив. Создание коробки само по себе не создаёт физическое размещение на архивной полке и не создаёт BoxPlacement. Если коробка создаётся в архивной учётной локации, её размещение на конкретной активной полке выполняется отдельно уполномоченным сотрудником архива командой box_place по действующим правилам. Начальная локация и права создания проверяются серверной стороной; реализация не разрешена.

## 2026-11-21 — создание пустой коробки без немедленного заполнения

Утверждено: физическую коробку разрешено создать без немедленного добавления в неё медицинских карт. Такая коробка может оставаться пустой и ожидать последующего заполнения. Само создание коробки не создаёт BoxCycle и не создаёт membership. Первый BoxCycle создаётся атомарно с добавлением первой карты командой box_add_card по ранее утверждённым правилам. Начальная учётная локация определяется при создании коробки; размещение на архивной полке не возникает автоматически и выполняется отдельно по правилам box_place. Реализация не разрешена.

## 2026-11-21 — вывод из использования ошибочно созданной пустой коробки

Утверждено: если физическая коробка создана ошибочно, ещё пуста и не участвовала в операционных процессах, её разрешено вывести из использования отдельной операцией списания коробки box_write_off по действующим правилам P-05 и DB-операции. Для этого случая обязательны отсутствие карт и membership, отсутствие BoxCycle, shipment и иных операционных размещений/перемещений; операция не должна обходить существующие проверки полномочий и области доступа. Физическое удаление записи коробки запрещено. Её короткий номер остаётся навсегда закреплённым за этой записью и не может быть повторно использован. История и событие списания сохраняются. Это уточнение не вводит отдельный механизм удаления или отмены создания и не меняет ранее утверждённые общие правила box_write_off. Реализация не разрешена.

## 2026-11-21 — отдельное название коробки не используется

Утверждено: у физической коробки нет отдельного пользовательского названия. Для её идентификации и поиска используется автоматически присваиваемый глобально уникальный короткий номер. Этот номер постоянен и не изменяется после создания. Дополнительное поле названия, его редактирование и переименование коробки не вводятся. Для отображения и поиска также могут использоваться её учётная локация, текущее физическое размещение и состояние — без подмены ими идентификатора. Реализация не разрешена.

## 2026-11-21 — причина создания коробки не фиксируется

Утверждено: при создании физической коробки не требуется и не сохраняется причина создания. Не вводятся поле причины, обязательный комментарий или свободное пояснение. Это решение относится только к причине создания и не отменяет фиксацию самого факта создания и стандартных технических атрибутов события (инициатор, дата и время) в соответствии с общей моделью учёта и аудита. Реализация не разрешена.

## 2026-11-21 — полномочия добавления медицинских карт в коробку

Утверждено: добавлять медицинские карты в коробку может только уполномоченный активный сотрудник филиала, центрального офиса или архива в пределах своей операционной области и учётной локации. Перед добавлением МедАрхив проверяет доступность карты для помещения в коробку, соответствие учётной локации коробки области сотрудника, отсутствие списания коробки и её участия в активном отправлении, а также отсутствие дублирующего размещения карты. Операция выполняется по действующим атомарным правилам box_add_card и не должна создавать конкурирующие активные membership/размещения. Реализация не разрешена.

## 2026-11-21 — состав коробки при заполнении в филиале, ЦО и архиве

Утверждено уточнение правила состава коробки: ограничение на добавление в одну коробку карт только одного филиала применяется к этапу заполнения коробок в филиалах. Оно не распространяется на операции заполнения и перегруппировки коробок в центральном офисе и архиве. В ЦО и архиве допускается совместное нахождение в одной коробке карт, поступивших из разных филиалов, при условии соблюдения действующих полномочий, учётной локации, custody, доступности карт и остальных инвариантов box_add_card. Это уточнение не разрешает филиалу добавлять в одну коробку карты другого филиала. Реализация не разрешена.

## 2026-11-21 — запрет добавления карты, уже находящейся в другой коробке

Утверждено: сотрудник не может добавить медицинскую карту в коробку, если карта уже состоит в активном membership другой коробки. До добавления карта должна быть извлечена из прежней коробки по предусмотренной операции; только после успешного закрытия прежнего membership допускается добавление в новую коробку. Прямое создание второго одновременного активного membership запрещено. Проверка и изменение выполняются атомарно с защитой от конкурентных операций; история прежнего и нового размещения сохраняется. Реализация не разрешена.

## 2026-11-21 — лимит вместимости коробки пока не устанавливается

Утверждено: на текущем этапе максимальная вместимость коробки по количеству медицинских карт не устанавливается, поскольку допустимые лимиты не определены. При добавлении карты проверка по максимальному количеству карт не выполняется; искусственный программный лимит не вводится. Вопрос может быть пересмотрен после определения фактических размеров используемых коробок и допустимой загрузки. Реализация не разрешена.

## 2026-11-21 — порядок карт внутри коробки не учитывается

Утверждено: МедАрхив не фиксирует и не контролирует физический порядок медицинских карт внутри коробки. Нумерация мест, позиции карт, последовательность укладки и обязательная сортировка внутри коробки не вводятся. Система учитывает состав коробки через действующие связи membership; это решение не отменяет учёт факта добавления и извлечения карт и сохранение истории соответствующих операций. Реализация не разрешена.

## 2026-11-21 — два равноправных сценария добавления медицинских карт в коробку

Утверждено: филиал использует МедАрхив прежде всего для документального оформления отправки собственных медицинских карт в архив. Сотруднику предоставляются два равноправных сценария, выбор между которыми остаётся за сотрудником:

1. Сначала создать записи медицинских карт, затем сформировать коробку и добавить в неё ранее созданные карты.
2. Сначала создать коробку, затем непосредственно в её составе находить и добавлять существующие карты либо создавать отсутствующие в системе карты с немедленным добавлением в эту коробку.

Для поиска и добавления карты должны быть доступны оба способа идентификации: сканирование штрихкода и ручной поиск/ввод данных. Ручной поиск должен поддерживать ФИО пациента, номер медицинской карты и телефон. Доступ к поиску по телефону подчиняется действующим отдельным правам доступа к чувствительным данным; наличие поля поиска не расширяет полномочия сотрудника. Ручной ввод данных карты также должен быть доступен как альтернатива сканированию штрихкода.

Если при добавлении карта не найдена, сотрудник может создать её запись в рамках того же сценария и сразу добавить карту в открытую коробку; не требуется выходить из процесса работы с коробкой и отдельно повторно искать созданную карту. Создание записи карты и её добавление в коробку должны выполняться как единый согласованный бизнес-сценарий с соблюдением обязательных проверок, атомарности, защиты от дублирования и действующих ограничений по роли, организационной области, custody и membership. Если подходящая существующая карта обнаружена, система должна предложить использовать её, а не создавать дубликат автоматически.

Оба сценария должны приводить к одинаковому учётному результату и использовать общие правила создания карты, box_add_card и дальнейшей подготовки коробки к отправке. Интерфейс должен явно поддерживать оба пути и не навязывать сотруднику единственную последовательность действий. Реализация не разрешена.
