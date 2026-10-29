# API reference — v1

All `/v1` endpoints require `Authorization: Bearer YOUR_API_KEY`. They serve stored data. The only route that requests a refresh is `POST /v1/checks`, which queues asynchronous work. `GET /docs` and `/openapi.json` provide the generated schema and do not expose stored fleet data.

- `GET /v1/trucks`: active trucks, insurance/inspection/offence source observations, freshness and open issues. Add `?include_inactive=true` to include deactivated trucks.
- `GET /v1/trucks/{registration}`: truck detail, including retained individual offences. Unknown registrations return 404.
- `PUT /v1/fleet`: replace membership with `{"registrations":["T123ABC","T456DEF"]}`. Normalize spaces/case and deduplicate; reject invalid registrations or more than 20 unique values with 422. An empty array deactivates all trucks. Existing history remains.
- `GET /v1/issues`: open active-fleet issues, sorted by severity, deadline and registration.
- `POST /v1/checks`: HTTP 202, returning the new or existing active job. An empty fleet returns 409. No browser is launched in the API process.
- `GET /v1/checks/{id}`: job status, snapshotted registrations, completed/total source count, individual outcomes and source failures.
- `GET /v1/exports/results.csv`, `/offences.csv`, `/inspections.csv`: current-generation CSV. A missing export or unsupported filename returns 404.
- `GET /v1/evidence/{job-id}/{filename}`: protected JSON/PNG evidence. Evidence references in source responses start with `evidence/`; prefix `/v1/` to retrieve them. Expired evidence returns 404.
- `GET /v1/health`: database connectivity, worker heartbeat, last batch and email failure/retry counts. Monitor heartbeat age and batch results; health data is authenticated.

## Response meanings

Source `reported_record` contains normalized dates/amounts alongside original portal fields in `reported`. Dates are ISO `YYYY-MM-DD`; original displayed dates are preserved. Timestamps include timezone offsets. Monetary values are decimal strings in Tanzanian shillings; no floating-point currency calculations are used.

`last_attempt` records the latest attempted lookup. `last_successful_check_at` includes explicit no-record responses. `data_verified_at` records when the retained actual record was last confirmed. `current_lookup_confirmed=false` means that retained data was not confirmed by the latest lookup. `stale=true` means no successful response for more than 36 hours, measured from truck registration if there has never been one.

`evidence` points to the latest attempted lookup. `verified_record_evidence` retains the evidence for the last actual verified record, even when the latest attempt failed. Both are subject to the 30-day file retention.

Source outcomes: `UNKNOWN`, `FOUND`, `NO_DEBT`, `NOT_FOUND`, `ERROR`, `BLOCKED`, `SKIPPED`. NOT_FOUND is an observation, not proof of no insurance/inspection, and not payment confirmation. A first missing response does not overwrite a previously verified record.

Issue types: `insurance_expiry`, `inspection_expiry`, `inspection_failed`, `inspection_restriction`, `pending_offence`, `offence_review`, `source_stale`, `record_missing`. Severity is `critical`, `warning`, or `operational`. Every issue includes a source, reference/key, suggested action, dates where applicable, and verification details.

`calculated` holds derived values, including `days_remaining`, `age_days`, `days_to_estimated_deadline`, and `deadline_estimated`. `reported_charge` and `reported_penalty` within that object are explicitly named source amounts and are never projected penalty increases. Pending offence deadlines use the configurable seven-day rule and are labelled estimated in responses and emails.

Job statuses: `queued`, `running`, `completed`, `completed_with_errors`, `failed`. Errors do not clear old records. Completed source results are transactionally committed with progress, allowing a restarted worker to resume without repeating completed searches.

An individual offence includes the original `reported_record`, an `effective_status`, and a `resolution`. A no-debt response may change effective status to CLEARED with `portal_no_debt`; it never changes original portal history to claim PAID.

## Example issue fields

```json
{
  "registration": "T123ABC",
  "source": "offence",
  "type": "pending_offence",
  "severity": "critical",
  "record_key": "EXAMPLE-REFERENCE",
  "due_date": "2026-10-31",
  "calculated": {
    "issued_date": "2026-10-24",
    "age_days": 5,
    "deadline_estimated": true,
    "deadline_rule_days": 7,
    "days_to_estimated_deadline": 2,
    "reported_charge": "30000.00",
    "reported_penalty": "0.00"
  }
}
```

This is an illustrative subset; actual responses also contain IDs, suggested actions, verification details and update timestamps. Use the generated OpenAPI document for request schemas.
