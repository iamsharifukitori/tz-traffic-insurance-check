# Fleet Deadline and Offence Monitoring API

An independent, community-maintained service for one truck owner and up to 20 active trucks. It checks public TIRA insurance records and TMS inspection/offence records, stores verified observations, calculates reminder deadlines and sends actionable emails. There is no dashboard, payment processing, SMS, or multi-owner account system in this release.

**Email starts in dry-run mode.** Messages are saved as `.eml` files rather than sent. This project is not affiliated with or endorsed by TIRA or the Tanzania Police Force. Use public portals responsibly and follow their applicable terms. It does not provide an official government API.

## What needs attention

- Insurance or inspection expires within 30 days: reminders at 30, 14, 7, 3 and 1 day remaining, on the expiry date, and after expiry. The displayed expiry date is inclusive unless the portal explicitly reports EXPIRED.
- New pending offence, reported penalty increase, failed inspection, or restriction on use: urgent notification after detection.
- Pending offence nearing the estimated issue-date-plus-seven-day deadline: days 5, 6 and 7, then overdue. Change `OFFENCE_DEADLINE_DAYS` to change that interval; the two preceding reminder days shift accordingly. No penalty percentage is calculated.
- A source has no successful check for more than 36 hours, or returns no record on two consecutive calendar days: operational issue.

Insurance far from expiry and explicit no-debt responses generate no issues. An active insurance policy nearing expiry still needs a reminder. Inspection validity uses the portal's actual Valid Until date, not an assumed one-year period. The latest inspection is selected by inspection date. A failed lookup never becomes an insurance clearance or a paid offence.

## Prerequisites and configuration

Use an always-on Linux server with Docker Engine and Docker Compose v2, a hostname pointing to it, and inbound ports 80/443 for HTTPS. A browser-capable server with at least 2 GB RAM is a reasonable starting point for this small sequential workload. The API and PostgreSQL ports are internal to the Docker network.

Copy this project folder to the server. From its directory:

```sh
cp .env.example .env
chmod 600 .env
python3 -c 'import secrets; print(secrets.token_hex(32))'
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Put the first generated value in `POSTGRES_PASSWORD` and the second in `API_KEY`. Keep the database password hexadecimal so it is safe in the database URL. Set `API_HOST` and `TLS_EMAIL`. Do not paste secrets into chat or commit `.env`. Single-quote SMTP passwords in `.env` if they contain dollar signs. The application rejects placeholder/short API keys.

For SMTP, set `SMTP_HOST`, `SMTP_PORT`, `SMTP_MODE`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, and comma-separated `EMAIL_TO`. STARTTLS on port 587 is the default; SSL is also supported. `plain` is for explicitly configured local test servers only. Use the credentials required by your email provider. Keep `EMAIL_DRY_RUN=true` initially.

## Start and import your trucks

Copy `trucks.example.txt` to `trucks.txt` and enter your registrations locally. Never commit your real truck list. Put one registration per line. Spaces and lowercase letters are normalized, duplicates are removed, and blank lines/comments beginning with `#` are ignored. Importing an empty list deactivates all trucks. The sample file contains only comments.

```sh
docker compose build
docker compose up -d db
docker compose run --rm migrate
docker compose run --rm --no-deps -v "$PWD/trucks.txt:/import/trucks.txt:ro" api python -m fleet.cli import /import/trucks.txt
docker compose up -d
docker compose logs -f worker
```

The worker checks at **06:00 Africa/Dar_es_Salaam**. If it starts after 06:00 and today's scheduled batch has not run, it queues that batch once. It resumes a running batch after a restart, skipping source results already committed. It does not replay every missed historical day. Deadline/freshness calculations run every 30 seconds using stored observations; portal lookups remain once daily unless manually requested.

Once imported, the database is authoritative. Changing Desktop `trucks.txt` alone does not change the server fleet. To import an updated file, rerun the import command. Removed registrations become inactive; their records and history remain stored.

## Use the API

Set `FLEET_API_KEY` in your own terminal to the configured key. Never put it in a URL. For example:

```sh
export FLEET_URL=https://fleet.example.com
curl -H "Authorization: Bearer $FLEET_API_KEY" "$FLEET_URL/v1/issues"
curl -H "Authorization: Bearer $FLEET_API_KEY" "$FLEET_URL/v1/trucks"
curl -X POST -H "Authorization: Bearer $FLEET_API_KEY" "$FLEET_URL/v1/checks"
curl -H "Authorization: Bearer $FLEET_API_KEY" "$FLEET_URL/v1/exports/results.csv" -o results.csv
```

PowerShell example after setting your key in the terminal:

```powershell
$headers = @{ Authorization = "Bearer $env:FLEET_API_KEY" }
Invoke-RestMethod "https://fleet.example.com/v1/issues" -Headers $headers
```

See [API reference](docs/API.md) for all endpoints. Interactive schema documentation is at `/docs` and `/openapi.json`; it contains no fleet data. All `/v1` routes require the bearer key. No endpoint performs an arbitrary unregistered vehicle lookup.

Manual and scheduled checks share one database-backed queue. If a check is already queued/running, `POST /v1/checks` returns its existing ID. Manual checks do not replace the daily schedule. Each job snapshots active registrations at enqueue time; a truck removed during a job may finish that job, but will not appear in active-fleet issues or future scheduled batches.

## Email review and activation

Urgent emails are queued on discovery of a pending offence, an increased portal-reported penalty, a newly expired document, a failed inspection, or a restriction. After the scheduled daily batch, one digest includes remaining actionable issues, excluding issues already emailed urgently that Tanzania day. Nothing is sent when there are no issues. Unchanged open issues may appear in the next day's digest.

Inspect previews on the server:

```sh
docker compose exec worker sh -c 'ls -1 /data/mail-previews'
docker compose exec worker cat /data/mail-previews/NOTIFICATION-ID.eml
```

After reviewing the contents and setting real sender/recipients, set `EMAIL_DRY_RUN=false` and recreate API/worker:

```sh
docker compose up -d --force-recreate api worker
```

Previously previewed messages are not sent retroactively. Future urgent events and daily digests use real SMTP. Failed deliveries retry after 1, 5 and 30 minutes, then appear in `/v1/health` as failed. Stable Message-ID values and event deduplication reduce duplicates. SMTP cannot guarantee exactly-once delivery if a worker crashes after the provider accepts a message but before the database records success, or accepts only part of a recipient list before a retry. Resolved/inactive issues are cancelled before pending messages are sent.

## Stored data, exports and evidence

PostgreSQL retains current records, source outcomes, record changes, issues, jobs, notification history and each individual delivery attempt. A restart during sending marks that attempt's provider acceptance as unknown. The private `fleet_data` Docker volume stores screenshots, raw portal JSON, CSV generations and email previews. Evidence is retained for 30 days; the API returns 404 when old evidence has been pruned. Other database history is retained until an explicit maintenance policy is added.

CSV downloads always reference the current completed generation. All three files switch together after a batch, rather than exposing half-written exports. One previous generation is kept internally for in-flight downloads. Offence exports distinguish the original reported status from effective local clearance; an explicit no-debt response means `CLEARED`, not proof of payment.

On errors the last verified data is retained and labelled uncertain. Missing responses count once per distinct calendar day, reset after an intervening failure/success or a skipped day, and only create a missing-record issue after two consecutive days. A missing offence row alone does not close its pending issue. Only explicit paid/cleared status or a successful no-debt response does that.

Requests are sequential, with 5–9 seconds between portal searches. A transient error is retried once after 60 seconds. A challenge or HTTP 403/429 stops that portal for the current batch; the other portal continues. No challenge bypass is attempted. The browser runs as UID 1000 with Chromium's sandbox enabled and the included upstream seccomp profile. If the host blocks user namespaces, select/configure a host that supports sandboxed Chromium; do not disable the sandbox to hide a deployment problem.

## Backups and restore

The backup service writes a PostgreSQL custom-format dump daily to `./backups` with a 14-day default retention. It starts with an initial backup and checks every five minutes for a new Tanzania date. Change `BACKUP_RETENTION_DAYS` if needed. Inspect `docker compose ps backup` and `docker compose logs backup`; its health check fails if no backup has succeeded within 36 hours. Copy backups to separate protected storage; same-server copies do not cover server loss.

Restore a chosen dump on the Linux server:

```sh
docker compose stop api worker backup
docker compose exec -T db pg_restore --username=fleet --dbname=fleet --clean --if-exists --no-owner < backups/fleet-YYYY-MM-DD.dump
docker compose run --rm migrate
docker compose up -d
```

Database dumps do not contain screenshot files or `.env`. Keep `.env` securely backed up separately. To archive evidence/previews when needed:

```sh
docker compose run --rm --no-deps --entrypoint tar api -C /data -czf - evidence mail-previews > backups/private-files.tar.gz
```

After restoring only the database, historical evidence URLs may be unavailable; the next batch regenerates current exports/evidence. Never run `docker compose down -v` unless intentionally deleting persistent data.

## Development and verification

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock -r requirements-dev.txt
pytest -q
ruff check .
```

SQLite is used only for isolated tests. To run the same suite against an isolated PostgreSQL database, set `TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:PORT/TEST_DB`. Each test uses and removes its own temporary schema. Enable real local Chromium DOM fixture tests with `RUN_BROWSER_TESTS=1`; install Chromium first with `python -m playwright install chromium`.

`python -m fleet.cli generate-key` prints a key without needing database configuration. For non-Docker development, set `DATABASE_URL`, `API_KEY`, and `DATA_DIR`, then run `alembic upgrade head`, `uvicorn fleet.api:create_app --factory`, and `python -m fleet.worker` in separate terminals. The deployed worker requires PostgreSQL and holds a PostgreSQL advisory lock, so a second worker exits rather than duplicating jobs.

Before enabling real email on the actual target server, run a complete fleet check in dry-run mode, inspect job failures/retained evidence, and review messages. Local Docker success does not establish that the government portals accept the target server's IP. Server address, hostname, SMTP credentials and recipients are deployment inputs; no hosting purchase or account creation is performed by this project.

For an explicitly queued one-shot validation batch, stop the normal worker, queue with `python -m fleet.cli check` inside the API container, then use `docker compose run --rm worker python -m fleet.worker --once`. Restart the normal worker afterward. The same exclusive worker lock applies; do not run a second worker alongside it.

See [validation notes](docs/VALIDATION.md) for the checks completed during implementation and remaining deployment checks.

## Open source

Licensed under [MIT](LICENSE). The vendored Playwright sandbox profile retains its upstream license; see [third-party notices](deploy/THIRD_PARTY_NOTICES.md). Read [CONTRIBUTING.md](CONTRIBUTING.md) before submitting changes and [SECURITY.md](SECURITY.md) for vulnerability reporting.
