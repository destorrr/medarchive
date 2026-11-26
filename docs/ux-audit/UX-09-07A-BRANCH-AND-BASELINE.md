# UX-09.7A — branch strategy and baseline
2026-11-26. Verified GitHub refs and untruncated recursive tree at audit checkpoint 0cb0c1085a63c89dc200d95b482bbfb83e6b2909. Original target architecture ref 767e0f10239d4404a60b82e8b827da581350cae3; compare ahead 1831, behind 0, merge base original ref. master 0cc607e0967a194691b2325b4ac4cfe6536a07c8; not selected as implementation base.
## Frozen reference objects
src 83e9d703df1e2b1b6815fc5eb6978667a154b089
tests 74ceb5c038bf73241b306bc1b86680f1b6f196
migrations 63a82acebc28aded4cb6773afdf9b65ee215de28
scripts cf31ec9f3c95c2f6a2f4a7e58c380550027fee78
db 22c174f3d36494eb21a5a9f5a5e909f2cdc145f5
.github 1bea804d59dfe7ad144c3b9c3a12915ff5075839
pyproject.toml 3a62196ade1e1b818ffc6e2b45ff5297ca1bcf89
docker-compose.yml b84d926fe040c46cd73a7e8eb1109f84a5700cd0
alembic.ini c80440a282a2c987926bf9616b16ebb536eac0a8
All five approved prototype blobs match UX-08-APPROVED-BASELINE.md exactly: branch V8 0c4ab84a3eec5b66f3b5963b543f30db010e08bd; CO V11 17d13316f2bf1da30fa4bd32f4dbd6bedc758f39; archive V14 106c4626f8b27c6cf949d6a0da9ad0869d60787c; admin V6 3e93e939066e02e410095c92ab255aca94654043; analytics V5 9fe55cbdbe06efd0e4bfd639aaac09a3187f5ac4.
## Proposed branch workflow
Finish 7B/7C on docs/ux-audit-project. At I00 reread final HEAD and verify reference objects and ancestry; pin exact final design commit (not today's interim checkpoint). Create proposed feature/ux-implementation from that SHA, only if absent; if exists inspect ancestry and contents, never overwrite. Preserve audit branch and original architecture branch, no force push, no reset, no merge to master without release authorization. Use implementation branch as integration target, optional short-lived package branches from its current HEAD with reviewed PRs; no mandatory branch proliferation. Keep progress and test evidence in implementation branch; audit baseline remains unchanged after freeze. Approved HTML files immutable unless separately approved; implementation templates elsewhere. Record package IDs in commits and PRs, compare actual diffs and tests, do not claim verification without evidence. Configure CI triggers and required aggregate check in I00/I01, subject to repository permissions. No production deployment by branch push.
## Limits
No branch created yet because final design SHA not available until 7C; branch name and PR policy engineering proposals. No source, migrations, workflow or prototype changes. No tests run. Branch protection and master ancestry not verified. Next 7B.
