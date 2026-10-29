"""Pure deadline calculations: portal amounts are never estimated."""

import hashlib
from datetime import date, timedelta

from .config import TZ, aware

SOURCES = ("insurance", "inspection", "offence")
SEVERITY = {"critical": 0, "warning": 1, "operational": 2}


def issue_id(registration, kind, key):
    return hashlib.sha256(f"{registration}:{kind}:{key}".encode()).hexdigest()


def reminder(days):
    if days < 0:
        return "expired"
    return next((str(t) for t in (0, 1, 3, 7, 14, 30) if days <= t), None)


def build_issues(truck, states, offences, now, offence_days=7):
    today = now.astimezone(TZ).date()
    result = []

    def add(source, kind, key, severity, due, calculated, action, milestone, urgent=False):
        result.append(
            dict(
                id=issue_id(truck.registration, kind, key),
                registration=truck.registration,
                source=source,
                kind=kind,
                record_key=key,
                severity=severity,
                due_date=due,
                calculated=calculated,
                action=action,
                milestone=milestone,
                urgent=urgent,
            )
        )

    for source in SOURCES:
        state = states.get(source)
        successful = aware(state.last_success) if state and state.last_success else aware(truck.created_at)
        if now - successful > timedelta(hours=36):
            add(
                source,
                "source_stale",
                source,
                "operational",
                None,
                {"hours_since_success": round((now - successful).total_seconds() / 3600, 1)},
                f"Check {source} availability; the current record may be out of date.",
                "stale",
            )
        if state and state.missing_streak >= 2 and state.outcome == "NOT_FOUND":
            add(
                source,
                "record_missing",
                source,
                "operational",
                None,
                {"consecutive_daily_missing": state.missing_streak},
                f"Confirm the {source} record manually; TMS/TIRA returned no record on consecutive days.",
                "missing",
            )

    for source in ("insurance", "inspection"):
        state = states.get(source)
        if not state or not state.data:
            continue
        data = state.data if source == "insurance" else state.data["latest"]
        end = date.fromisoformat(data["expiry_date"])
        key = f"{data['reference']}:{end}"
        days = (end - today).days
        reported_expired = data["status"].upper() == "EXPIRED"
        milestone = "expired" if reported_expired else reminder(days)
        if milestone:
            expired = milestone == "expired"
            add(
                source,
                f"{source}_expiry",
                key,
                "critical" if expired else "warning",
                end,
                {
                    "days_remaining": days,
                    "portal_reported_expired": reported_expired,
                    "last_verified_at": aware(state.data_verified_at).isoformat()
                    if state.data_verified_at
                    else None,
                    "latest_lookup_outcome": state.outcome,
                },
                f"Renew {source}" + (" immediately." if expired else f" by {end.isoformat()}."),
                milestone,
                expired,
            )
        if source == "inspection":
            if data["final_result"].upper() in ("FAIL", "FAILED"):
                add(
                    source,
                    "inspection_failed",
                    key,
                    "critical",
                    None,
                    {},
                    "Review the failed inspection and arrange corrective action.",
                    "failed",
                    True,
                )
            restriction = data["restriction"].strip()
            if restriction.lower() not in ("", "no", "none", "n/a", "-", "nil"):
                add(
                    source,
                    "inspection_restriction",
                    key,
                    "critical",
                    None,
                    {"restriction": restriction},
                    "Review and address the reported restriction on use.",
                    "restriction",
                    True,
                )
    for offence in offences:
        if offence.status in ("PAID", "CLEARED"):
            continue
        data = offence.data
        issued = date.fromisoformat(data["issued_date"])
        deadline = issued + timedelta(days=offence_days)
        age = (today - issued).days
        if offence.status != "PENDING":
            add(
                "offence",
                "offence_review",
                offence.reference,
                "operational",
                deadline,
                {"reported_status": offence.status},
                "Review the unrecognized offence status.",
                "review",
            )
            continue
        milestone = "overdue" if age > offence_days else f"day{age}" if age >= offence_days - 2 else "new"
        add(
            "offence",
            "pending_offence",
            offence.reference,
            "critical" if age >= offence_days - 2 else "warning",
            deadline,
            {
                "issued_date": issued.isoformat(),
                "age_days": age,
                "deadline_estimated": True,
                "deadline_rule_days": offence_days,
                "days_to_estimated_deadline": (deadline - today).days,
                "reported_charge": data["charge"],
                "reported_penalty": data["penalty"],
                "reported_status": data["status"],
                "last_verified_at": aware(offence.verified_at).isoformat(),
                "description": data["description"],
                "location": data["location"],
            },
            "Review and settle or dispute the pending offence; estimated deadline "
            + deadline.isoformat()
            + ".",
            milestone,
            True,
        )
    return result
