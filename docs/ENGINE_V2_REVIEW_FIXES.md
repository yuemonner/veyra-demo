# Investigation Engine v2 — ledger integrity fixes

This change fixes the three P1 findings from the review of `e047335`. It does not declare the product production-ready or migrate the current workspace from `/runtime` to `/engine`.

## Trusted execution receipts

The human-ledger API rejects client-supplied `automatic_read_only` entries. The public store append method also rejects fabricated automatic receipts and engine investigation records. Only the dedicated allowlisted execution path can create automatic receipts, and it checks approval, current context/evidence and import integrity before running a tool. Receipts bind the actual result digest, count, run/check IDs and input hashes. Edited or declined checks retain their restrictions.

## Atomic writes and hash-chain integrity

SQLite `BEGIN IMMEDIATE` now protects the read-head → validate-state → append sequence across connections. Investigation runs and their ledger entries commit together. Automatic checks are validated, executed and recorded within the same protected transaction. Failures roll back, and a damaged existing chain is refused rather than extended. These changes serialize SQLite writes; a production PostgreSQL/per-case locking and background-job design is still a separate milestone.

## Actual observation-window evidence

Evidence-attached outcomes require actual imported sample timestamps inside the reported observation window. A file's minimum/maximum timestamp span alone is insufficient. Evidence remains engineer-reported; a sample in the window does not independently attest recovery, prove continuous coverage or establish causality.

## Regression evidence

- Full backend suite: **146 passed**.
- Frontend connection suite: **9 passed**.
- Frontend production build and TypeScript: verified in an isolated copy with `next build --webpack`; the running local build was not replaced.
- New tests cover public/API forged receipts, genuine receipt digests, approval/staleness checks before tool execution, unsampled gap windows, positive sampled windows, rollback after rejected writes, concurrent connections and atomic run/ledger failure.
- Synthetic fixtures and disposable test databases only. Existing local case/source data was not modified by tests.

## Git synchronization without deployment

All prior engine work plus these fixes are retained on `engine-v2-machine-context-ledger`. No merge into the production branch is part of this change. Both repository-root and frontend-root `vercel.json` disable Git-triggered deployments for this exact branch, covering either Vercel project-root setting. The production branch remains unspecified and unchanged. No PR is opened, no deployment command is run, and the Render blueprint is unchanged.

Vercel documents the branch-scoped setting at https://vercel.com/docs/project-configuration/git-configuration . Deployment requires a separate explicit request.

## Remaining release gates

Authenticated customer/tenant authorization, durable production storage and migrations, background jobs, workspace integration of Engine v2, and authorized BuilderX data acceptance remain pending. Passing these regression tests does not close those gates.
