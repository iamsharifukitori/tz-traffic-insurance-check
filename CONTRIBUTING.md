# Contributing

Open an issue describing the problem before proposing major changes. Keep pull requests focused and include reproduction steps and relevant tests.

Use Python 3.11. Install `requirements-dev.txt`, install Chromium with `python -m playwright install chromium`, then run `pytest -q`, `ruff check .`, and `ruff format --check .`.

Set `RUN_BROWSER_TESTS=1` to run local HTML/Chromium fixture tests. Set `TEST_DATABASE_URL` to an isolated PostgreSQL database to test migrations and locking; tests create and remove temporary schemas. Never point tests at production.

Use synthetic registrations and records in fixtures. Do not submit real truck lists, portal screenshots, offence references, financial records, credentials, databases or email previews. Live portal checks must remain opt-in and must not run in CI. Never bypass challenges or rate limits.

Preserve last-good observations on failures, distinguish estimated deadlines from portal-reported values, and keep default email delivery in dry-run mode. New database changes require additive Alembic migrations. Run all relevant tests before submitting a pull request.

By contributing, you agree to license your contributions under the project's MIT license. Retain all third-party attribution.
