# FACEIT release, September 2026

This is a coordinated source-release checklist, not authorization to deploy.
The complete acceptance and rollback guide lives in the extension repository:
[RELEASE-0.2.1.md](https://github.com/soloser/getreplay-faceit-extension/blob/codex/faceit-history-release/docs/RELEASE-0.2.1.md).

Source PRs: [migrations #7](https://github.com/soloser/getreplay-migrations/pull/7),
[Go #19](https://github.com/soloser/getreplay-go/pull/19),
[PHP #10](https://github.com/soloser/getreplay-php/pull/10),
[frontend #26](https://github.com/soloser/getreplay-front/pull/26),
[extension #2](https://github.com/soloser/getreplay-faceit-extension/pull/2).
This checklist/configuration change is [infra #53](https://github.com/soloser/getreplay-infra/pull/53).

## Before pressing deploy

1. Merge the reviewed migrations, Go, PHP, frontend and extension source PRs.
2. Use **Prepare release candidate** for MySQL migrations, Go, PHP and frontend;
   choose the reviewed revision contained in each source `main`. Review/merge
   candidate PRs before deployment. This documentation change intentionally does
   not pin unmerged source branches or modify the current `candidate.json`.
3. Review actual MySQL schema and ledger. FACEIT fields from 0021, team keys from
   0052, owner lookup index 0055 and nullable JSON metadata 0056 are prerequisites.
   **0053 deletes generated highlights**. Do not apply every pending migration
   or force the ledger to hide missing predecessors. If migration deployment would
   include unapproved data changes, stop for a DB-owner-approved targeted procedure.
   See the migrations repository's `docs/faceit-history-metadata.md`.
4. Supply Go's `FACEIT_API_KEY` in the existing private runtime env. In PHP verify
   `MATCH_UPDATER_URL`, the common `JWT_SECRET` and `MAIL_FROM_ADDRESS`.
   Templates are not runtime configuration; do not commit or print real secrets.
5. Once schema is ready, use component buttons in order **Go → PHP → frontend**.
   Do not use the all-components button for this release; it has a different order
   and would include Node GC, which does not need changing here.
6. Run live acceptance, including manual upload identity and server-side extension
   import from a different public IP. Only then submit extension 0.2.1 to Chrome.

Keep protected environment approvals and the release gateway intact. Do not push
to `prod`, merge source PRs automatically, or dispatch deployments as part of
preparation. A rollback normally leaves the nullable metadata column/index intact.

Contact changes to `admin@getreplay.gg` in Caddy and PHP require the corresponding
owner-controlled runtime configuration/email-dashboard updates. They do not change
the Google publisher account or verify its contact address automatically.
