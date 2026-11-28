# UX-10 — журнал реализации

Разрешение владельца проекта получено в чате: «даю разрешение». Ветка feature/ux-implementation создана от 86bfa30971815e078484694c8cd0f554e37de4d9. Ветка проектирования сохранена. Разрешение на production не предоставлено.

## Пакеты

| Пакет | Статус |
|---|---|
| I00 | Clean CI accepted; external inventory open |
| I01 | Accepted for isolated implementation; workstation check deferred to I11 |
| I02 | In progress: I02.1, I02.2 and I02.5 historical storage accepted; I02.3/I02.4 downgrade gates open; E3/E4 pending |
| I03–I11 | Planned |

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


### I02.3 — additive writeoff request storage; acceptance pending (2026-11-27)

Revision 0014_card_writeoff_requests (eb3a49c703cf8a2840908d55284209f6e59fda52) adds transitional storage; 2dbcb8a3e79ac9fbfbad83b8cbb96e0806fc4477 adds 11 constraint cases. Integration #101 (46033721509, ec64db26762039b15e9ef0b7ef635629dfc7396f) failed at guarded runtime fixture verification with Mandatory manifest mismatch: manifest required the new cases before runner inclusion. Corrected by 3ef3f3b8acf7ba1dbf846f50e58857068627fdc9; Integration #102 and #103 succeeded. Verified Actions metadata for #103 (46034467144) and Foundation #1717 (46034467160), both success at 23e304cdad68cc1022dbf42f2ecf9121654d1e8f. Prior recorded integration evidence: 19 infrastructure, 25 I01 and 27 I02 cases passed, 71 total. Owner terminology: writeoff request is distinct from P09 archive request. Migration source contains a populated-table downgrade guard, but its independent execution and preservation of the saved row/revision remain unverified. I02.3 remains open. No workflow cutover, production access or business-rule changes. Local execution unavailable in current environment (no PostgreSQL server or Docker). Overall isolated package acceptance: 2/12.


### I02.4 — discrepancy storage checkpoint

Revision 0015 added in d313da8; Integration #105 failed on unconditional downgrade rejection. Fix 1e2ee6a exposed malformed SQL dollar quoting in Integration #106. Corrected in 7b5b4e6; Integration #107 and Foundation #1721 passed. Eight discrepancy storage tests added in d2a4a39; Integration #108 and Foundation #1722 passed, but the new tests are not yet confirmed in the mandatory PostgreSQL suite. I02.4 remains open. Populated downgrade rehearsal remains outstanding. No production changes.

### I02.4 — mandatory integration evidence (2026-11-28)

Integration #110 (46043326668) failed at intermediate commit 551f3d578ab914b94814f77d6f891cc41cb1fa5c: 27 I02 cases passed, eight discrepancy cases missing from runner. Corrected in 709ae7f8d14d2244a4f4189b3f6fc129dccba563. Verified exact-SHA Integration #111 (46043335697, job 140656310610): 19 infrastructure, 25 mandatory I01 and 35 mandatory I02 cases passed (79 total); both mandatory manifests verified, all steps successful. Foundation #1725 (46043335932, job 140656310736): 514 passed, 53 skipped, 36 warnings; all steps successful. Discrepancy storage tests now verified in dedicated CI. Populated downgrade rehearsals for 0014 and 0015 remain outstanding; neither package is fully accepted. Overall isolated package acceptance remains 2/12. No production changes.

### I02 — open acceptance gates and support escalation (2026-11-28)

Owner approved continuing independent implementation work while OpenAI support investigates the blocked tool operation. GitHub HTTP 400 on tree retrieval resolved by resolving commit and tree SHA first; both migration sources 0014 and 0015 read successfully. The proposed populated downgrade regression update to scripts/run-i01-local.sh was blocked before commit by the tool safety system. No new runner change or CI evidence resulted. Do not bypass the block or relax the migration guards. Support confirmed escalation to a human specialist; response pending, no case number supplied. I02.3 and I02.4 remain open pending independent populated downgrade evidence. Proceed with I02.5 additive receipt-correction storage only after reviewing approved contracts. I02 overall acceptance remains blocked by outstanding gates; isolated package acceptance 2/12. No production changes.

### I02.5 — receipt correction contract and mapping (2026-11-28)

Existing DomainEvent correction_of_event_id self-reference (RESTRICT) and append-only trigger confirmed in revisions 0001/0002; legacy shipment.received is whole-shipment evidence, not a per-box receipt. Storage proposal be25c38; structural regression e743f95; pure typed payload and tests d76ca7c/1319ffa; original receipt identity validator and tests 4b1d346/ef551f6. Exact-SHA CI at ef551f67fd14f419a582c0b5a7801e36b733a061: Foundation #1735 (46102118945, job 140846663266), 544 passed, 53 skipped, 36 warnings; Integration #121 (46102118895, job 140846663079), 19 infrastructure, 25 I01, 35 I02 passed; mandatory manifests verified. New adapter 3d11b2e and 11 test cases 203e050 construct a transient DomainEvent with original reference and independent actor, without persistence or state changes. CI for these latest changes pending. Test-only catalogs/types are not approved product vocabulary. Actual reason catalog, event type/version, authorization, manifest validation, compensation and protected command remain open. No additional migration selected; no claim of complete E2 acceptance. I02.3/I02.4 populated downgrade evidence still outstanding; overall accepted packages 2/12. No production changes.

### CI verification rule — owner instruction (2026-11-28)

For each implementation step, enumerate every workflow run for every commit created during that step, including intermediate source/test/documentation commits. Verify exact SHA, conclusion, job logs and test counts/mandatory manifests where applicable. Record failures and subsequent fixes explicitly; never infer success from the final two runs alone. Do not advance to the next implementation step until the current step's runs are accounted for and required checks are successful. Pending runs are not acceptance evidence.

### I02.5 — adapter CI verified; Foundation persistence test added

All six runs from the previous step completed successfully: 3d11b2e Foundation #1736 (46102537433, 544 passed), Integration #122 (46102537446); 203e050 Foundation #1737 (46102545990, 555 passed), Integration #123 (46102546624); 8a1e7d9 Foundation #1738 (46102560641, 555 passed), Integration #124 (46102560636). All Foundation runs had 53 skipped and 36 warnings; each dedicated run had 19 infrastructure, 25 I01 and 35 I02 passing cases, both manifests verified. Added a rollback-only synthetic PostgreSQL Foundation storage test in 8a8be15dd15ef90f993a31549f062e9bf65668d0, using existing fixture permissions; validates independent event ID, original event reference, reason, original metadata and two stored events. This is generic storage evidence only, not proof of actual manifest membership, route, permissions, approved reason codes or business-command execution. CI pending. No change to migrations, production data or runtime ACL. I02.3/I02.4 rollback evidence remains outstanding. Overall acceptance 2/12.

### I02.5 — Foundation storage verified; integrity regression checkpoint

All four runs from the previous step verified by exact SHA and job logs: 8a8be15 Foundation #1739 (46102989202, job 140849416095), Integration #125 (46102989235, job 140849416298); 542cf65 Foundation #1740 (46103000253, job 140849448665), Integration #126 (46103000359, job 140849449318). Both Foundation runs: 556 passed, 53 skipped, 36 warnings. Both dedicated runs: 19 infrastructure, 25 I01, 35 I02 passing; mandatory manifests verified. All job steps successful. Synthetic correction storage test passed. Added three Foundation regression cases for missing original FK and append-only protection of original/correction events in 6ad5c8650524b017c9c9717039f22bb4983d1b05. Latest CI pending; verify every run for both this step's commits. Existing schema reused, no DDL or ACL changes. Storage evidence does not establish actual box manifest membership, finalized product reason/event vocabulary or protected I05 command behavior; these gates remain open. I02.3/I02.4 populated downgrade rehearsals remain outstanding. Overall accepted packages 2/12.

### I02.5 — generic storage verified; final contract gate remains open

All four runs for 6ad5c8650524b017c9c9717039f22bb4983d1b05 and 4417da99abc1a5c91a4226ff6526298d185c7797 verified by SHA and logs: Foundation #1741 (46103681322, job 140851559648), #1742 (46103694078, job 140851598414), both 559 passed, 53 skipped, 36 warnings; dedicated #127 (46103681330, job 140851559990), #128 (46103694081, job 140851598730), both 19 infrastructure +25 I01 +35 I02 passed and mandatory manifests verified. All job steps successful. Generic storage, self-FK and append-only integrity verified; no new DDL needed for that component. Detailed checkpoint 47317df54eed40d680fc4b41de921ff561be3666. E2 specification requires final affected-field contracts, so I02.5 is not formally accepted: receipt event vocabulary/version, reason catalog, actual manifest binding and command/compensation boundaries remain unresolved. Next resolve from existing approved specifications without inventing business rules. E3/E4 not accepted or started on this evidence. I02.3/I02.4 rollback rehearsals remain outstanding; 2/12 accepted overall. Verify every CI run for this step's documentation commits. No production changes.

### I02.5 — actual manifest binding resolver (2026-11-28)

All four CI runs from the preceding step verified by exact SHA, job logs and successful steps: 47317df Foundation #1743 (46104151952, job 140853016927), Integration #129 (46104151944, job 140853017050); 12cdcd4 Foundation #1744 (46104164951, job 140853059038), Integration #130 (46104164943, job 140853059167). Foundation each 559 passed, 53 skipped, 36 warnings; dedicated each 19 infrastructure, 25 I01, 35 I02 passed, both mandatory manifests verified. Added read-only actual shipment/box membership and incoming archive route resolver 6a4a9d0, four Foundation integration cases 2304fc5. No autoflush, write or commit in resolver; authorization and transactional locks/rechecks remain duties of future protected command. Tests use real manifest but synthetic receipt vocabulary. Production event names/version, reason catalog, compensation matrix, authorization and concurrency remain open. No new DDL/ACL, no production changes. Verify all runs for all three commits before advancing. Accepted packages 2/12.

### CI continuation rule — owner instruction (2026-11-28)

After verifying all CI runs of the current step, immediately proceed to the next eligible implementation step without asking for routine confirmation. If a run fails, inspect its exact logs, correct the underlying defect without weakening safeguards, launch CI again and verify every intermediate and corrective commit until required checks succeed. Record failed runs and their resolution rather than hiding them. Pending CI, unavailable tools and unresolved business decisions are explicit gates, not reasons to report completion. This rule applies within available execution time and does not imply background execution.

### I02.5 — manifest resolver verified; fixed event vocabulary

All six preceding CI runs verified by SHA and job logs: 6a4a9d0 Foundation #1745 (46104645619, job 140854559686, 559 passed), Integration #131 (46104645610, job 140854559789); 2304fc5 Foundation #1746 (46104664088, job 140854614940, 563 passed), Integration #132 (46104664086, job 140854614851); 2b19abe Foundation #1747 (46104675905, job 140854652154, 563 passed), Integration #133 (46104675898, job 140854652095). All Foundation runs 53 skipped, 36 warnings; all dedicated runs 19 infrastructure +25 I01 +35 I02, both manifests verified. All job steps successful. Four real-manifest/synthetic-event cases passed. Engineering choice for new per-box event names: shipment.box_received and shipment.box_receipt_corrected, payload version 1, distinct from legacy shipment.received. Implemented constants 88d834a, server-bound read-only resolver c1d5606, one unit test 2dbd84d. No production producer switched; reason catalog remains server-supplied, permission/compensation/transaction contract and final event envelope remain open. Verify all CI for these commits and this journal commit. I02.5 not accepted, 2/12 accepted overall. No DDL/ACL/production changes.

### I02.5 — fixed event vocabulary CI verified; incoming route regression

All eight preceding runs verified by exact SHA and logs: 88d834a Foundation #1748 (46105063751, 563 passed), Integration #134 (46105063757); c1d5606 Foundation #1749 (46105069368, 563 passed), Integration #135 (46105069370); 2dbd84d Foundation #1750 (46105074992, 564 passed), Integration #136 (46105075029); 1f2f7bb Foundation #1751 (46105089501, 564 passed), Integration #137 (46105089488). All Foundation runs 53 skipped, 36 warnings; dedicated runs each 19 infrastructure, 25 I01, 35 I02, both manifests verified; all steps successful. Added four Foundation cases ef9f438 exercising fixed vocabulary on branch and CO to archive routes, including legacy whole-shipment event rejection and no implicit write. Original receipt remains synthetic. Production reason catalog and compensation/permission contracts not specified by inspected sources; no fabricated business decisions. No producer cutover or DDL/ACL changes. Verify all runs for both this step's commits. I02.5 remains open, 2/12 packages accepted.


### I02.5 — individual box receipt metadata binding

Previous four runs reported verified in handoff: Foundation #1752/#1753 (568 passed, 53 skipped, 36 warnings each), Integration #138/#139 (19 infrastructure, 25 I01, 35 I02 each; both manifests). Map existing 0013 receipt columns and pair constraint on ShipmentBox; read stored pair directly in resolver and reject absent metadata or mismatched original actor. Timestamp equality deferred until individual receipt producer contract is established; pre-compensation validation only, not historical replay. Existing route fixtures now populate actual receipt metadata in isolated tests; add missing and mismatched actor cases and structural mapping test. No new DDL, producer cutover, ACL or compensation rules. CI pending; I02.5 not accepted. Overall 2/12.


### I02.5 — E2 manifest guard conflict discovered

Exact SHA 05db3c38bfefb9a2aca2ceb499280927d155de57: Foundation #1754 (46106617860, job 140860715533) failed application tests: 8 failed, 563 passed, 53 skipped, 36 warnings; backup/restore step skipped. Dedicated #140 (46106617894, job 140860715716) successful, all steps; 19 infrastructure +25 I01 +35 I02, both manifests verified. All eight failures were fixture writes to shipment_boxes after handover, rejected by medarchive_guard_manifest_mutation (23514). UX-09-05E explicitly places replacement of receipt/manifest guards in coupled E4. Preserve the guard, no fabricated pre-handover receipt or trigger bypass. Extract and test pure metadata validator (valid, absent time/actor, wrong actor); E2 integration checks actual manifest rejection paths, both routes, legacy vocabulary rejection and explicit post-handover write denial with unchanged metadata. Positive database resolver with real post-handover receipt and mismatched persisted actor remain mandatory E4 integration gates, not passing E2 evidence; I02.5 remains open. No DDL/ACL or production producer changes. Verify all CI for corrective commit. Overall 2/12.


### I02.5 — corrective CI verified; storage contract hardening

Exact SHA 9ae29cf13b3ef50acb52834572af36e740078fc2: Foundation #1755 (46107109205, job 140862206529), 574 passed, 53 skipped, 36 warnings; Integration #141 (46107109154, job 140862206499), 19 infrastructure +25 I01 +35 I02, both manifests verified; all jobs/steps successful including Foundation backup/restore. All runs of failed and corrective commits accounted for. Proceeded directly to next eligible step: explicit rejection of non-object original JSONB payloads and five unit regressions; updated I02-05 storage contract with E2/E4/I05 boundaries, authoritative relational reference, versioned payload and remaining compensation/envelope gates. No DDL/ACL/production changes. I02.5 remains open; 2/12 overall. Verify every CI of this commit before dependent work. Owner reiterated mandatory immediate continuation after successful CI within the same response; do not stop for routine permission or merely announce next step when executable work remains.


### I02.5 — historical correction read integrity

Verified all CI at exact SHA 92f4d0ed32fe22540d9a424404b8b6fbcf485aea: Foundation #1756 (46107983250, job 140864919624) 579 passed, 53 skipped, 36 warnings; dedicated #142 (46107983198, job 140864919564) 19 infrastructure +25 I01 +35 I02, both manifests verified; all steps successful including backup/restore. Implemented stored-pair integrity reader, 24 unit cases for valid pair and malformed historical references/envelopes/payloads/originals. Historical reader does not depend on current reason catalog or receipt projection. No DDL, ACL, production or business outcome changes. CI pending for this commit; I02.5 not accepted; overall 2/12. After CI, prioritize remaining actual acceptance gates rather than expanding isolated helpers indefinitely.


### Independent shared frontend foundation — UX-09.6A

All CI at b1809540fa5a07af214af648a4c743a8a8afe6fb verified: Foundation #1757 (46108967445, job 140867998429) 603 passed, 53 skipped, 36 warnings; dedicated #143 (46108967477, job 140867998577) 19+25+35 passed, both manifests, all steps successful. I02 remains open; I02.3/4 downgrade tool blocker retained, I02.5 compensation storage sufficiency unresolved. UX-09-06A explicitly allows shared frontend shell/components after I01 and stable contracts. Started independent opt-in design tokens and Jinja panel/feedback primitives with five semantic/escaping tests and five real Chromium workspace token/overflow tests. No live template switch, no role/route/permission changes, no prototype modifications. Analytics workspace is a presentation variant, not a role. CI pending. No I03 or I09 business acceptance claimed. Overall 2/12 accepted.


### I02.5 — return to storage closure

Owner directed frontend pause and I02.5 completion first. All CI for 41e530e66939306373bd5a51f0b0e2be4be03371 verified by exact SHA and logs: Foundation #1758 (46110071582, job 140871423163), 613 passed, 53 skipped, 36 warnings; dedicated #144 (46110071727, job 140871423253), 19 infrastructure +25 I01 +35 I02, both manifests verified; all steps successful including backup/restore. Frontend changes retained, no new frontend work. Reviewed approved repeat receipt and separate correction effects (UX06 readiness 2026-11-21). Final historical storage boundary uses existing append-only DomainEvent, separate IDs for repeated receipts and references to individual original receipt. Added real PostgreSQL rollback-only roundtrip for two synthetic receipt/correction pairs, fresh ORM load and cross-pair rejection. No production mutation, no guard bypass, no new DDL or reason/permission/outcome values. I02.5 historical additive storage closure candidate pending CI; E4 effective receipt projection and I05 exact compensation/authorization remain separate required gates. I02.3/4 blocked downgrade tests unchanged. Overall 2/12; no package acceptance claimed before CI.


### I02.5 — historical additive storage accepted

Verified every CI run at a636e37bad30893de13cbff0c588eb8ef1860b47: Foundation #1759 (46110773318, job 140873582881), 614 passed, 53 skipped, 36 warnings; dedicated #145 (46110773356, job 140873582952), 19 infrastructure +25 I01 +35 I02, both manifests verified; all steps successful including encrypted backup/restore. PostgreSQL repeated receipt/correction roundtrip passed. I02.5 historical additive storage accepted without new revision. Local I02 tracked substeps 3/5 (60%); overall accepted packages 2/12. E2/E3/E4 overall not accepted; I02.3/4 populated downgrade blocker remains, effective receipt projection/compensation/permissions are mandatory later gates. Frontend pause respected through this acceptance. No new business rules, migrations or production changes. Next eligible independent work must not bypass blocked acceptance or dependency gates. Verify CI of this documentation commit.


### I02/E3.1 — initial read-only reconciliation implementation

Verified all CI at b8b4ac74a806c84ccf5ed0cc0b07986bc415541d: Foundation #1760 (46111259900, job 140875083776), 614 passed, 53 skipped, 36 warnings; dedicated #146 (46111259901, job 140875084097), 19+25+35 passed, both manifests, all steps successful. I02.5 historical storage accepted. Owner authorized E3 then E4. Added count-only PostgreSQL inventory adapter and three rollback-only Foundation tests for stable counts, viewer/courier duplicates, unmapped request/individual placement. See I02-E3-RECONCILIATION.md. No migration, writes to retained data, production access, frontend changes or guard bypass. CI pending. E3.1 partial, E3/E4 not accepted; I02.3/4 blocked. Overall 2/12.


### I02/E3.1 — inventory integration coverage expanded

All CI for 7a92ee0be9ac74616006427390415bfb76688c65 verified: Foundation #1761 (46112178861, job 140877933964) 617 passed, 53 skipped, 36 warnings; dedicated #147 (46112178835, job 140877933981) 19+25+35 passed, both manifests, all steps successful including backup/restore. Added five parametrized PostgreSQL cases for four legacy incoming/excluded route combinations and mismatched additive item link; corrected diagnostic courier whitespace normalization and exercised tabs/newlines. CI pending. No guard bypass, no retained data access or production changes. E3.1 partial; I02.3/4 blocker unchanged; no package acceptance claimed.


### I02/E3.1 — fixture regression fixed

Verified all runs at 70f04cd62a687d816f156be2c4f5c58de601e730. Foundation #1762 (46112620630, job 140879304848) failed application tests: 1 failed, 621 passed, 53 skipped, 36 warnings; backup/restore skipped. Dedicated #148 (46112620608, job 140879304630) all 18 steps successful, 19+25+35 cases, both manifests verified. Root cause: Python fixture supplied literal escaped strings rather than actual whitespace. Corrected test data, without weakening assertion or changing SQL. New CI pending; E3.1 not yet accepted. No changes to migrations, runtime guards or production data.


### I02/E3.1 — remaining inventory coverage

Verified all CI at ae9ef3993b119a8d44bb3ef882f6d010dac0a6c5: Foundation #1763 (46113006168, job 140880496566), 622 passed, 53 skipped, 36 warnings; Dedicated #149 (46113006157, job 140880496424), 19+25+35 passed, both manifests, all 37 steps successful including backup/restore. Added four PostgreSQL cases for remaining four inventory categories, including two membership conflict variants rejected by existing deferred constraints. No guard bypass, actual retained data access or migration. E3.1 closure candidate pending CI; next E3.2 deterministic mapping classification. Overall 2/12; I02.3/4 blocker unchanged.


### I02/E3.1 accepted; E3.2 pending mapping planner

All CI at 2f108c8e503ae9992d94a02b2942f72d14421e5e verified: Foundation #1764 (46113584767, job 140882304089), 626 passed, 53 skipped, 36 warnings; dedicated #150 (46113584780, job 140882304203), 19+25+35, mandatory manifests verified; all 37 steps successful. E3.1 diagnostic inventory accepted. E3.2 implementation started with conservative deterministic read-only pending request planner, stable child UUIDv5, retained parent/actor/time, four integration cases. No fabricated timestamps, status updates, writes to retained data, guard bypass or new migrations. Existing items and ambiguous statuses still require classification/equivalence and atomic apply tests before E3.2 acceptance. CI pending. Overall 2/12; I02.3/4 blocker unchanged.


### I02/E3.2 — existing pending item equivalence

All CI at cfba102fe2e6a090a5f883b319a1e72aa9187eb2 verified: Foundation #1765 (46114018264, job 140883646903) 630 passed, 53 skipped, 36 warnings; dedicated #151 (46114018427, job 140883646938) 19+25+35, both manifests verified, all 37 steps successful. Planner now distinguishes exact existing deterministic pending child from conflicts, with five additional integration cases. Existing rows never rewritten. CI pending; no actual backfill, production data, guard changes or E4 cutover. Overall 2/12; I02.3/4 blocker unchanged.


### I02/E3.2 — historical evidence classification

All CI at 6649366cd566215eccb94c3799f7490ac5b08948 verified: Foundation #1766 (46118735268, job 140898287661) 635 passed, 53 skipped, 36 warnings; dedicated #152 (46118735564, job 140898288222) 19+25+35, both manifests, all 37 steps successful. Implemented historical fulfillment evidence counts and per-status review counts; added five synthetic PostgreSQL cases. No inferred receipt times or reason codes; no historical writes or migration. CI pending. I02.3/4 blocker unchanged, overall 2/12.


### I02/E3.2 — bounded additive backfill implementation

All CI at 8fa906f6d6033aafd8f9d8730baf1dbc5fdac5a2 verified: Foundation #1767 (46119135036, job 140899552116) 640 passed, 53 skipped, 36 warnings; dedicated #153 (46119135044, job 140899552215) 19+25+35, both manifests verified, all 37 steps successful. Implemented atomic maintenance-only pending child backfill, conflict refusal, repeat no-op and outer rollback tests. Limit further E3 depth to migration safety requirements; no general historical repair engine. New CI pending. No actual retained database access, no migration activation, no guard bypass. Overall 2/12, E3.1 accepted, E3.2 candidate pending CI, I02.3/4 blocker unchanged.
