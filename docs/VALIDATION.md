# Implementation validation — 29 October 2026

The service was built and tested locally. It has not been deployed to a private test fleet’s production server because its address, hostname, and SMTP settings have not been supplied.

## Automated checks

- PostgreSQL 16: **63 tests passed**, including real Chromium DOM fixtures, all migrations, competing job enqueue requests, exclusive worker locking, lost-lock handling, source outages, restart recovery, deadline boundaries, notification deduplication, SMTP retries, individual delivery-attempt history and CSV replacement.
- SQLite: **62 tests passed, 1 PostgreSQL-specific advisory-lock test skipped**, with real Chromium DOM fixtures enabled.
- Ruff lint and formatting checks, Python compilation, Docker Compose configuration validation, Caddy configuration validation and container dependency checks passed.
- The pinned Starlette test client emits one non-failing deprecation warning for its httpx adapter. Production API requests and the tested client work; future dependency upgrades should migrate the test adapter as appropriate.

## Docker and real portal checks

- Built the Linux image with pinned Python dependencies and Chromium.
- Ran the worker as UID 1000 with `chromium_sandbox=True`, the included upstream seccomp profile and the read-only container filesystem configuration.
- Imported a private test fleet’s 12 registrations into a separate validation database.
- Completed **36/36 real source checks**: TIRA insurance, TMS inspection and TMS offences for each registration. The batch completed without source failures.
- The real batch produced **8 actionable issues**: one pending offence, five approaching inspection expiries and two approaching insurance expiries. Healthy records outside reminder windows did not become issues.
- Generated **two dry-run email previews**: one urgent offence notification and one digest of the seven remaining issues. No email was sent to an external recipient.
- Verified unauthenticated fleet API requests return 401, authenticated requests return stored observations, and all three CSV files download through the API.
- Verified a later one-shot worker start does not repeat the completed daily batch.
- Applied the final migration head `c20f25da1021` to the validation database.
- Verified automatic database backup creation and restored a fresh custom-format dump into a separate database. The restored database contained all 12 truck registrations and the final migration revision.


## Deployment checks still required

1. Configure the real server, hostname, HTTPS/DNS, API key, SMTP sender and recipients.
2. Run the same full-fleet validation from that server's IP with `EMAIL_DRY_RUN=true`; local Docker success does not establish acceptance of a different server IP.
3. Review the generated messages and validate real SMTP delivery to the configured recipients before setting `EMAIL_DRY_RUN=false` for ongoing use.
4. Configure separate protected storage for off-server backups and test recovery there.

Validation did not submit payments or changes to the portals.
