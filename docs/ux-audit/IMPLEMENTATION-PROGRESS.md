# UX-10 — журнал реализации

Разрешение владельца проекта получено в чате: «даю разрешение». Ветка feature/ux-implementation создана от 86bfa30971815e078484694c8cd0f554e37de4d9. Ветка проектирования сохранена. Разрешение на production не предоставлено.

## Пакеты

| Пакет | Статус |
|---|---|
| I00 | Clean CI accepted; external inventory open |
| I01 | In progress: I01.1 accepted, I01.2 active |
| I02–I11 | Planned |

## I00 — исходный baseline

- Проверены pyproject.toml, docker-compose.yml, alembic.ini, tests/conftest.py и .github/workflows/postgresql.yml. Python >=3.11, CI 3.12, PostgreSQL 16, legacy SQLite fixtures. Compose использует постоянный том; не запускать против неизвестной базы. Зависимости преимущественно с нижней границей.
- Коммит 16c4cd486e5dd1f4d000b6d0566a71be48e14bf1: существующий baseline workflow включён для push и PR реализации, workflow_dispatch, contents:read. Существующие проверки сохранены. Это не целевой J01–J10 и не доказательство успешного CI.
- DB1: фактические PostgreSQL revision/catalog/ACL/counts/conflicts/bootstrap parity не проверены. Изолированное окружение, версии зависимостей, результаты pytest и CI, migration head и восстановление пока не подтверждены. Не использовать реальные медицинские данные. Не выполнять destructive reset.
- Следующее: получить результат baseline CI, классифицировать failures/skips, проверить отдельное PG16 окружение и read-only DB1; затем I01 только после критериев I00. Не считать пакет Verified без доказательств.

Исходники приложения, миграции, тесты и утверждённые HTML не менялись. Изменение CI trigger и permissions — инженерное решение по UX-09.6C.

## I00.2 — исходный CI и инвентаризация

Runs 45823859419 and 45823882661 completed success. Job 139922888682 logs: PostgreSQL 16.16, Alembic 0011_login_throttles (head), pytest 488 passed, 36 warnings, 157.15s; no skips reported in summary. Chromium installation, core tables, shell syntax and existing encrypted backup/restore check succeeded. Restore check is limited to existing two-table assertion; not target DB7/DB8 acceptance. Existing SQLite tests and superuser connection remain baseline only.

Commit f1cbb166c52d0bf8c9beeec08ed4eb4b180b9b6b adds read-only catalog inventory and dependency versions to isolated CI: role flags, owners, ACL, constraints, triggers, estimated row counts, migration revision, pip check/freeze. Results pending. Estimated rows are not exact counts; conflict reconciliation, bootstrap parity, effective runtime ACL and reproducible dependency lock remain open. No external PG target inspected. I00 remains In progress; I01 not started. No application, migration, test or prototype changes.

## I00.3 — inventory and dependency baseline

Runs 45825482358 and 45825499654 success; job 139928474645 inventory succeeded. Python 3.12.16, PG16, revision 0011_login_throttles, pip check OK, 488 passed / 36 warnings (165.07s). Current CI role medarchive has superuser/createdb/createrole/login flags. All 36 pytest warnings are httpx per-request cookies deprecations (archive 3, packing 1, transfer 32); separate pytest-asyncio unset loop scope and Node punycode warnings. Do not suppress; address with fixture/auth work. Captured 44 exact package versions in constraints/ci-python312.txt from actual freeze, CI now consumes constraints and Python 3.12.16. Verification pending; no hash lock, runner/image/build tools not fully pinned. DB1 exact counts/conflicts/bootstrap parity and effective runtime ACL still open; no external database accessed. I00 remains in progress. No application or migration changes.

## I00.4 — constrained baseline verified

Runs 45849104189, 45849123641, 45849131538 success. Latest logs confirm constraints used, Python 3.12.16, pip check OK, revision 0011_login_throttles head, 488 passed / 36 warnings. Seven successful runs so far. Read actual DB1 gate 5H, bootstrap service/test, migrations/env.py and initial SQL. Initial SQL has 15 CREATE TABLE statements and no CREATE FUNCTION/TRIGGER statements; it is not equivalent to head. Application singleton bootstrap uses advisory transaction lock and row lock, existing idempotency test passed in baseline. Added read-only function/trigger definitions and exact table counts to disposable CI inventory; new run pending. No external database access, no data reset. DB1 bootstrap SQL/Alembic detailed reconciliation and retained-data conflict inventory remain open. I00 not closed; I01 not started.

## I00.5 — DB1 clean baseline reconciliation

Runs 45851708162 and 45851713974 success; job 140019485032: 488 passed, 36 warnings, 211.21s. Exact counts: 17 application tables empty, alembic_version one row. Function and trigger definitions captured in CI logs. Compared initial SQL with frozen SCHEMA_SQL in 0001: identical after trimming surrounding whitespace (13599 characters). README already prescribes Alembic. Historical SQL draft now explicitly marked non-installable in bcf7a60d03dcf83ae5e3183d4317e2bc780d55c9; SQL statements unchanged. Historical migration unchanged. Clean DB1 baseline evidence collected; external/retained data conflicts not verified, no production access. Effective target runtime ACL remains I01 evidence, not established by superuser baseline. I00 clean-environment work complete subject to verification of documentation-only change; do not mark external DB1 or release gate passed. I01 implementation may proceed in isolated environment only, with affected retained-data migrations blocked pending preflight. No application code, tests or migration changes.

## I01.1 — principal provisioning started

47cd1aaec4da72cda91103d25a56fb9debc5216b adds transactional bootstrap outside Alembic, four technical principals, fail-closed attributes/membership validation, controlled migrator SET ROLE, public schema CREATE revoke. CI executes twice and checks effective role membership and runtime DDL denial via session authorization. CI pending. No passwords, production execution, ownership transfer, runtime DML grants/revocation, backup read grants or migration URL integration yet. Existing application tests still use baseline privileged connection. I01 in progress; I00 clean baseline complete, external DB1 gate remains. No business roles or prototypes changed.

## I01.1 follow-up — CI syntax failure

Runs 45852528278, 45852548097, 45853347615 success. Runs 45853375450 and 45853381823 failed in new I01 assertion step after both bootstrap invocations succeeded: workflow contained single-dollar SQL delimiters, PostgreSQL syntax error. Fixed with named $i01$ delimiters in 5320c34147c4f40406576852ef58eb56f53d3dad; fetched workflow again and verified both pairs. New CI pending; ACL assertions not yet proven. No weakening of assertions, no production changes. I01.1 remains open.

## I01.1 — bootstrap assertions passed; independent login checks added

Runs 45854212593 and 45854228135 success. Latest job confirms I01 bootstrap step success, 488 passed / 36 warnings in 173.77s and existing backup restore success. Added independent TCP login tests in 9a3f5bae5eb1440a58e04ecc7e5b9086aa7047e9: disposable CI-only passwords, identity assertions, migrator SET ROLE positive, runtime/backup SET ROLE negative and runtime public DDL negative. Re-read workflow after commit. New CI pending. No deployment credentials or external DB access. Ownership, migration URL, backup read grants and final DML restrictions remain open; baseline application tests still privileged.


## I01.1 — dedicated Alembic integration CI (2026-11-26)

At HEAD 23f0b68e0b36a6460907c6d0f52a17374ec23a5e the dedicated migration URL was implemented but not exercised by the baseline workflow. Commit 7502cc0ea52b4d9ffc1958fd1ef9295aa5979a2b adds a separate isolated PostgreSQL 16 workflow, .github/workflows/i01-dedicated-migrator.yml. It bootstraps technical principals, provisions disposable CI credentials, grants owner schema creation rights in the clean test database, executes Alembic through the dedicated migrator, checks revision and public relation/routine ownership, checks runtime privilege boundaries, and rejects an incorrect dedicated migration identity. Existing baseline workflow is unchanged. Initial workflow run: https://github.com/destorrr/medarchive/actions/runs/45860768921 (in progress at journal update; outcome not yet established). These checks do not establish ownership transfer for an existing database, backup privileges, offline migration policy, or I01.1 completion. No production environment accessed.

## I01.1 — dedicated migration verified; backup implementation started

Runs 45860768921 and 45860795990 succeeded. Job 140051032938 logs confirm all 11 upgrades, head 0011_login_throttles, relation/routine owner assertions and rejection of wrong migration identity. Baseline runs 45860768407 and 45860795923 succeeded; job 140050979599 logs confirm 488 passed, 36 warnings, 212.48s, baseline backup/restore and expected privilege denials. Checklist 8/10 verified (clean database only). Existing-object ownership transfer and offline policy remain open.

Commits cea5c48ec4d16ee61e9e16c9cb30d1f12750c998, e7e36ad214f6b4d37c6a24d0d0ccfd074d7d13ab and e23a479a333572ae2e758a398de290c23b0d10f9 add separate backup URL with transitional fallback, owner-scoped public schema read grants and default privileges, and CI backup login/denial/encrypted restore checks. Results pending. Restore currently checks revision, not complete data or ACL reconstruction; backup completeness with nonempty synthetic data and future object privileges remains to be verified. No production changes. I01.1 remains open.

### I01.1 — backup restore CI correction
Foundation #1617–1620 succeeded; #1620 reported 488 passed, 36 warnings, 187.76s. Dedicated integration #5/#6 failed at pg_restore because the decrypted archive was outside the wrapper's mounted backup directory. Commit cb49b8ceb5b77849923401766cd6c9dd30a1bea0 moves the temporary restore archive into the mounted directory and adds synthetic row and sequence checks for future-object default grants and restore. New CI evidence pending. Checklist remains 8/10; ACL reconstruction and existing ownership transfer remain unverified.

### I01.1 — backup verified, final acceptance started
Foundation #1621/#1622 and dedicated integration #7/#8 succeeded. Job 140056944684: 488 passed, 36 warnings, 187.29s. Job 140056944752: backup identity, future table/sequence reads, four expected denials, encrypted dump, restore, synthetic rows and sequence assertions succeeded. Checklist 9/10 verified. Commit fefdf27b3193600d30cfe1c73693e1a0d05f7560 adds restore under owner, relation/index/routine ownership checks and reapplication of versioned backup grants with positive read and negative DELETE checks. CI pending. This is backup ACL reconstruction only, not complete runtime/API ACL reconstruction; existing database ownership transfer and offline/fallback policy remain open. No production changes.

### I01.1 — restore ownership verified; final transition rehearsal
Foundation #1623/#1624 and dedicated integration #9/#10 succeeded. Job 140059834911: 488 passed, 36 warnings, 161.38s. Job 140059833632: restored relation/index/routine ownership, reapplication of backup grants, positive read and denied DELETE succeeded. Commits 7049a6e20dfdd030063574cb313550be710b3c7d and dfebb83da19a24eb24e102cc7aa715f49fa4f898 reject offline SQL in dedicated mode (identity cannot be verified), retain transitional fallback and add CI rehearsal of ownership transfer from an isolated synthetic legacy role. REASSIGN OWNED is used only for that disposable role, not a production transfer recipe; actual retained database inventory remains external. Checklist 9/10 pending final CI. Full future runtime/API ACL reconstruction remains later work. No production changes.

### I01.1 — accepted in isolated CI (10/10)
Foundation #1625–1627 and dedicated integration #11–13 succeeded at final evidence SHA 936882a003f941c7b42e866671de420cb91f6a59. Job 140062208112: 488 passed, 36 warnings, 219.79s. Job 140062208225: all dedicated migration, backup/restore, offline rejection, legacy fallback and disposable legacy ownership transfer steps succeeded; logs confirm INSERT 0 1 and REASSIGN OWNED. I01.1 accepted for isolated implementation; production inventory and actual retained database transfer remain external gates. Full runtime/API ACL and DML retirement remain later packages. No production changes. I01.2 may start; I01 as a whole remains open.

### I01.2 — guarded PostgreSQL fixture
Foundation #1629: 494 passed, 36 warnings, 211.59s. #1631/#1632 failed at collection (ModuleNotFoundError: pg_fixture_guard); integration #16–18 succeeded but did not exercise the new fixture. Commits f16d3eaabedaec1b8180c242fad747bcf02a870a, 4767697e5f6e97f69df1eb3ff3acc92a7d9d493c, 68f49d3de15f3d820f629ce4c596e358ec92ee3a and 4c64a089b4a783b97e4186a9464c78976cff1c25 fix package import, add opt-in runtime fixture, real identity tests and dedicated CI database. Positive runtime and negative admin/migrator/backup/wrong database cases pending CI. Existing SQLite fixtures retained. No destructive cleanup or production access. Progress 2/8 verified; server identity not yet accepted.

### I01.2 — actual identity accepted; rollback fixture started
Foundation #1634–1637 and integration #20–23 succeeded. Job 140349116274: 495 passed, 5 dedicated-only skips, 36 warnings, 210.79s. Job 140349116332: 12 passed in 0.24s, including all five actual identity cases. Progress 3/8 (37.5%). Commits 671554f29acb42943d80afe3ea2d10e7953717ea, 684712d11c9a0fde1859b7d73a4532e94233abfe and 1ae3db0b96939ef83e00e9e6671880b9c5066bee add rollback-only fixture, synthetic probe tests and scoped CI grants plus final empty-table assertion. Pending CI; not a complete application-schema fixture or committed/concurrent fixture. No production changes, no destructive cleanup.

### I01.2 — rollback probe verified; migrated fixture database
Foundation #1638–1641 succeeded. Integration #25 failed with missing i01_fixture_probe (intermediate commit before CI provisioning); #26–27 succeeded. Job 140351238208: 14 passed in 0.27s. Rollback probe and final empty-state assertion verified. Commits aa28ba184db2baf8a41dc301efbcdc8e06ef9c48 and 9f9eb44abde7b4e846b83f273703016cf3f3a7cd migrate the disposable fixture database through dedicated migrator and add scoped runtime reads of cards, shipments, domain_events. Pending CI. Progress remains 3/8 until migrated fixture evidence; no broad runtime DML grants or production changes.

### I01.2 — migrated fixture accepted; independent sessions started
Foundation #1642–1644 and integration #28–30 succeeded. Job 140353832557: 495 passed, 8 dedicated-only skips, 36 warnings, 212.58s. Job 140353832730: 15 passed in 0.33s. Migrated fixture reads and rollback probe verified; progress 4/8 (50%). Commits bad5594b6ff37d29802ac74ef6fcc40d75df7c41 and ee4ef74e8c86c8b97c4fc58a2bbcb02d1c5f2f02 add independently guarded session pair and backend PID/uncommitted visibility/rollback assertions. Pending CI. This does not yet prove committed concurrency, lock contention or both winner orders. No production changes.

### I01.2 — independent sessions verified; committed contention pending
Foundation #1645–1647 and integration #31–33 succeeded. Job 140355885140: 495 passed, 9 dedicated-only skips, 36 warnings, 175.87s. Job 140355885287: 16 passed in 0.33s. Independent backend PIDs, uncommitted visibility and rollback verified. Commits 809825a487b1cc4e9f26582678ee223476d8d181 and 92a89285ece0d58f61c16d813c17def4e53e22ac add synthetic-probe-only DELETE grant and both session orders for bounded lock timeout (55P03), committed visibility, duplicate key (23505), targeted finally cleanup. Pending CI; progress 4/8. No application-table DELETE grants, no production changes. This is infrastructure contention, not business-command concurrency acceptance.

### I01.2 — concurrency accepted; synthetic application seed started
Foundation #1648–1650 and integration #34–36 succeeded. Job 140359117378: 495 passed, 11 dedicated-only skips, 36 warnings, 190.56s. Job 140359117590: 18 passed in 0.94s. Both session orders, bounded contention, committed visibility and probe cleanup verified. Progress 5/8 (62.5%). Commits ac93ba96407dc1589e213dc8b6077983e03e9cc4 and ef99e214cf33a97dc75d20bed27b26d81b9743c2 add deterministic synthetic branch, location, disabled employee and card, with runtime read-only relationship assertion. Pending CI. Seed is limited to current historical schema, not target business workflows; no real patient data or production changes.

### I01.2 — initial synthetic seed verified; reference entities expanded
Foundation #1651–1653 succeeded. Integration #37 failed on intermediate empty-card assertion; #38–39 succeeded. Job 140360961551: 495 passed, 12 dedicated-only skips, 36 warnings, 176.37s. Job 140360962252: 19 passed in 1.00s. Commits 8eb649ae58cbf4da097d8e2d0d2ccbd969fe5379 and d36cf23f8e0a93bfd8d42e134c9c3ceea1079aeb expand synthetic seed to all four disabled business-role identities, three location kinds, courier and box, and add runtime reference assertions. Pending CI; progress remains 5/8. Shipment and request seed not yet implemented. No production changes.

### I01.2 — reference seed verified; manifest and request seed added
Foundation #1654–1656 and integration #40–42 succeeded. Job 140365664638: 495 passed, 13 dedicated-only skips, 36 warnings, 190.25s. Job 140365664399: 20 passed in 0.86s. Commits b38b2cb20af85325a907b11e9fb7b58f65a28e61 and 83042de4ae906691e1a665a14dfb53055e08cbd6 add synthetic box cycle/membership, draft branch-to-archive manifest and separate archive card with historical single-card CO request. Runtime read assertions pending CI. Historical request shape is not target P-09 acceptance. Progress remains 5/8 until evidence. No production changes.

### I01.2 — synthetic seed accepted; final-state verification
Foundation #1657–1659 succeeded. Integration #43 failed on intermediate empty-shipment assertion; #44–45 succeeded. Job 140368475789: 495 passed, 14 dedicated-only skips, 36 warnings, 210.97s. Job 140368475663: 21 passed in 0.90s. Synthetic manifest and historical request verified; progress 6/8 (75%). Added always-run CI final-state check for empty probe, preserved baseline and no residual runtime connections, bounded statement timeout. No destructive database cleanup: disposable CI service lifecycle removes environment. Pending CI; local committed fixture cleanup and mandatory test manifest acceptance remain open.

### I01.2 — final-state CI and mandatory manifest verified
Foundation #1660–1663 and integration #46–49 succeeded. Job 140377976030: 495 passed, 14 dedicated-only skips, 36 warnings, 210.34s. Job 140377976151: 21 passed in 1.18s; mandatory manifest verified 21 passing cases. Final-state checks passed, including seeded table counts. Commits 59b6d31009b47e86b7b7fdaddc7cbdacc02d1138 and 7adc66392a82e043aad7bec5bc4c7fbf75b21082 recorded. Added eight report checker regression cases and strict xfail policy, pending CI. Local committed fixture lifecycle remains unverified; CI disposable cleanup accepted, progress 7/8 for CI scope only. No production changes.

### I01.2 — CI acceptance evidence and remaining seed gaps
Foundation #1664–1666 and integration #50–52 succeeded. Job 140384003295: 503 passed, 14 dedicated-only skips, 36 warnings, 212.64s. Job 140384003340: 8 checker tests passed, 21 mandatory tests passed, manifest and final state verified. CI checklist complete for prior scope; overall I01.2 remains open. Commits 9232c972f95cb7b3c323b11c06e0e7817030ffdb and 61fff2901c5e0980b54815da8fedee136ec927c2 add second branch, active synthetic principal with invalid login hash, third card, active/inactive shelves and runtime relationship checks. Pending CI. These are seed checks, not authorization acceptance. Local committed fixture lifecycle and target grouped request tests remain open/deferred respectively. No production changes.

### I01.2 — expanded seed and bounded cleanup regression evidence
Foundation #1667–1669 and integration #53–55 succeeded: expanded two-branch seed and shelf checks accepted. Commits 6e523e460e6f12d4a3a3d15a968383b3fd4f82b5, 71133e419dab1891359471e19a2b9f06359388f4 and 199f5b4f2b0220af14e00799295ee1d0437ad2ac restore fixture environment, bound runtime connection/command time and centralize scoped probe cleanup. Foundation #1670–1672 and integration #56–58 succeeded. Job 140390293331: 503 passed, 14 dedicated-only skips, 36 warnings, 208.92s. Job 140390293129: 8 checker tests passed, 21 mandatory cases passed, manifest and final state verified. Both concurrency orders exercised cleanup. Negative cleanup guard and environment restoration tests remain pending; actual local workstation execution not verified. Grouped request fixtures deferred to I02 schema. I01.2 remains open. No production changes.


### I01.2 — negative fixture regression acceptance (2026-11-27)

Verified GitHub Actions run 45965445578 (Foundation #1677, job 140392901984) and run 45965445609 (dedicated integration #63, job 140392902814): both completed successfully, including application tests, guarded runtime fixtures and disposable fixture final-state checks. Reported test totals: Foundation 505 passed, 18 skipped, 36 warnings; integration infrastructure 10 passed; mandatory PostgreSQL integration 25 passed; mandatory manifest verified. The 18 Foundation skips correspond to tests run in dedicated integration; these are not proof of local workstation execution.

Integration #60 failed at an intermediate mandatory-manifest mismatch after four new tests were added; subsequent #61–63 passed following manifest correction. I01.2 CI acceptance: PASS. Overall I01.2 remains open pending reproducible local PostgreSQL 16 setup and documentation acceptance. Deferred: grouped P-09 request fixtures (I02); role-scoped authorization checks (later packages); SQLite removal (I10); external I00 inventory; physical local workstation verification. The 36 Foundation warnings have not been separately classified for this final run. No production data or approved HTML prototypes changed.

### I01.2 — shared disposable local/CI runner

Commits b1b76f6abf4132c1950f41bd3437fb39596ebe0d and 4a115711865bb8c37f02a49fcec66501e2990b83 introduce scripts/run-i01-local.sh and replace the duplicated I01.2 workflow block with that runner. I01.1 workflow steps remain unchanged. Local prerequisites: Bash, local Unix-socket Docker engine, psql, Python 3.12 environment with dependencies installed using constraints/ci-python312.txt and editable dev extras. From repository root run `bash scripts/run-i01-local.sh`. The runner uses a new disposable postgres:16 container with loopback dynamic port, fixed synthetic-only credentials, no external database URL, guarded runtime fixtures, mandatory manifest and final-state assertions; it prints the temporary JUnit report directory. No production database or patient data is required. Native Windows support is not established. The new runner has not yet been executed or accepted; CI and actual workstation evidence remain pending. Do not mark I01.2 closed based on this documentation commit.


### I01.2 — shared runner CI acceptance (2026-11-27)

Verified push runs via GitHub Actions REST API at d550e4d04fa815edfbebf60ce5203d162650d1e7: Foundation #1681 (45970673804, job 140410054528) and Integration #67 (45970674242, job 140410056118), both success; all listed steps succeeded, including the shared guarded runtime runner. Integration #66 (45970646362) at 4a115711 also succeeded. Shared runner CI acceptance: PASS. Actual developer workstation execution remains unverified; native Windows support not established. Detailed test counts for these latest runs not extracted; previous counts remain historical evidence. I02 preparation may proceed in the isolated branch; overall I01.2 local acceptance remains open. No production changes.


### I01 — implementation gate accepted; workstation check deferred (2026-11-27)

Owner approved deferral of actual local workstation execution to I11. I01.1 and I01.2 accepted for isolated implementation based on recorded successful CI, including shared runner #67 and Foundation #1681. I01 implementation gate closed; I02 may proceed. I11 must verify the documented runner on the actual workstation and record evidence before final acceptance. External retained database inventory, final runtime/API ACL and DML retirement remain separate gates; no production readiness claim. No production changes.


### I02 — owner-confirmed custody rule (2026-11-27)

Owner explicitly confirmed that actual handover transfers responsibility to the courier on every supported route: branch to archive, archive to central office, central office to archive. Assignment/draft alone does not transfer responsibility. Receipt transfers responsibility to destination for the received objects (ordinary box and contents, P09 individual card); unreceived objects remain courier responsibility. This clarification supersedes conflicting earlier interpretations that archive retains responsibility during P09 transit. Courier remains a directory entity, not an application role. Reconcile affected design contracts before implementing custody guards. No schema changes in this commit.

### I02.1 — additive request item storage accepted (2026-11-27)

Revision 0012_archive_request_items, commit 653cee9e546a2445587877fca37ad546dad4a6a7, introduces transitional item storage. Seven constraint tests are wired into the guarded synthetic runner; upgrade from 0011 with retained synthetic legacy rows, empty downgrade and rejection of populated downgrade are rehearsed. Foundation run 46000514018 and dedicated integration run 46000514064 at 2d85262867d714065e17e2b7e931ee78ec80618a completed successfully. Acceptance is limited to additive storage; no grouped application writer, cross-model reservation enforcement, reason catalog, reconciliation or production cutover is approved. I02 remains open. Next: additive shipment links and receipt storage.


### I02.2 — additive shipment links and receipt storage accepted (2026-11-27)

Revision 0013_shipment_receipt_links (9f822bae19839528ba520e0e5712073df8b02511) adds nullable unique fulfillment links and paired receipt metadata to card and box manifest rows. Verified Foundation #1704 (46025982827) and dedicated integration #90 (46025982682) at d3f9dcfe6016f781fe2267e4db726d3bcec4952a: both success. Integration logs report 10 infrastructure, 25 mandatory I01 and 16 I02 cases passed (51 total); I01 manifest verified. Nine I02.2 cases cover nullable legacy links, receipt pairs, foreign keys, catalog uniqueness and actual duplicate inserts. Rehearsals verify empty downgrade/re-upgrade with retained legacy rows and refusal of downgrade with populated request link or card/box receipt metadata. Independent populated request_item_id downgrade case remains untested. Intermediate failures were corrected by test-only fixture grants; no production ACL change. Acceptance is limited to additive storage, not route-aware receipt commands, grouped request writers, reconciliation, custody or final guards. I02 remains open. Strict I02 JUnit manifest and dynamic head checks remain follow-up infrastructure work.
