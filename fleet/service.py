from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from .config import TZ, aware, utcnow
from .db import Change, Issue, Job, Offence, SourceState, Truck
from .mail import enqueue
from .portals import normalize_plate
from .rules import SEVERITY, SOURCES, build_issues, issue_id


def fleet_registrations(values):
    plates = list(dict.fromkeys(normalize_plate(value) for value in values))
    if len(plates) > 20:
        raise ValueError("At most 20 unique active registrations are supported.")
    return plates


def replace_fleet(session, values):
    plates = fleet_registrations(values)
    if session.bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(481901)"))
    for truck in session.scalars(select(Truck)):
        truck.active = truck.registration in plates
    for plate in plates:
        truck = session.get(Truck, plate)
        if not truck:
            truck = Truck(registration=plate)
            session.add(truck)
        truck.active = True
    session.flush()
    inactive = select(Truck.registration).where(Truck.active.is_(False))
    for issue in session.scalars(select(Issue).where(Issue.registration.in_(inactive), Issue.open.is_(True))):
        issue.open = False
        issue.resolved_at = utcnow()
    return plates


def enqueue_job(factory, schedule_day=None, now=None):
    now = now or utcnow()
    with factory() as session:
        if schedule_day:
            existing = session.scalar(select(Job).where(Job.schedule_day == schedule_day))
            if existing:
                return existing
        existing = session.scalar(select(Job).where(Job.status.in_(("queued", "running"))))
        if existing:
            return existing
        plates = list(
            session.scalars(
                select(Truck.registration).where(Truck.active.is_(True)).order_by(Truck.registration)
            )
        )
        if not plates:
            return None
        job = Job(
            registrations=plates, schedule_day=schedule_day, created_at=now, status="queued", results=[]
        )
        session.add(job)
        try:
            session.commit()
            return job
        except IntegrityError:
            session.rollback()
            # Database partial unique index serializes competing API/worker enqueue requests.
            return session.scalar(select(Job).where(Job.status.in_(("queued", "running")))) or (
                session.scalar(select(Job).where(Job.schedule_day == schedule_day)) if schedule_day else None
            )


def schedule_due(factory, now):
    local = now.astimezone(TZ)
    if local.hour >= 6:
        return enqueue_job(factory, local.date(), now)
    return None


def record_change(session, registration, source, key, before, after, now):
    if before != after:
        session.add(
            Change(
                registration=registration,
                source=source,
                record_key=key,
                before=before,
                after=after,
                created_at=now,
            )
        )


def sync_issues(session, registration, settings, now):
    truck = session.get(Truck, registration)
    if not truck or not truck.active:
        return
    states = {
        row.source: row
        for row in session.scalars(select(SourceState).where(SourceState.registration == registration))
    }
    offences = list(session.scalars(select(Offence).where(Offence.registration == registration)))
    desired = build_issues(truck, states, offences, now, settings.offence_days)
    existing = {
        row.id: row for row in session.scalars(select(Issue).where(Issue.registration == registration))
    }
    wanted = {spec["id"] for spec in desired}
    for spec in desired:
        urgent = spec.pop("urgent")
        row = existing.get(spec["id"])
        new = row is None or not row.open
        previous_milestone = row.milestone if row else None
        if row is None:
            row = Issue(**spec, generation=1, open=True, first_seen=now, updated_at=now)
            session.add(row)
        else:
            if not row.open:
                row.generation += 1
                row.first_seen = now
            for key, value in spec.items():
                setattr(row, key, value)
            row.open, row.updated_at, row.resolved_at = True, now, None
        session.flush()
        # Pending offences are urgent on discovery only; their age milestones go in the digest.
        if urgent and (new or (row.kind.endswith("_expiry") and previous_milestone != row.milestone)):
            enqueue(
                session,
                f"issue:{row.id}:{row.generation}:{row.milestone}",
                "urgent",
                [row],
                now,
                f"Fleet alert: {registration} — {row.kind.replace('_', ' ')}",
            )
    for identity, row in existing.items():
        if identity not in wanted and row.open:
            row.open = False
            row.resolved_at = now


def ingest(session, registration, source, result, settings, now):
    state = session.get(SourceState, (registration, source))
    if state is None:
        state = SourceState(registration=registration, source=source, data={}, missing_streak=0)
        session.add(state)
        session.flush()
    state.last_attempt = now
    state.outcome, state.message = result.outcome, result.message
    if result.evidence:
        state.evidence = result.evidence
    penalty_increases = []
    if result.outcome in ("FOUND", "NO_DEBT"):
        state.last_success = state.data_verified_at = now
        if result.evidence:
            state.verified_evidence = result.evidence
        state.missing_streak, state.missing_day = 0, None
        record_change(session, registration, source, "", state.data, result.data, now)
        state.data = result.data
        if source == "offence":
            old = {
                row.reference: row
                for row in session.scalars(select(Offence).where(Offence.registration == registration))
            }
            if result.outcome == "NO_DEBT":
                for row in old.values():
                    if row.status not in ("PAID", "CLEARED"):
                        before = {**row.data, "effective_status": row.status}
                        row.status, row.resolution, row.verified_at = "CLEARED", "portal_no_debt", now
                        record_change(
                            session,
                            registration,
                            source,
                            row.reference,
                            before,
                            {**row.data, "effective_status": "CLEARED", "resolution": row.resolution},
                            now,
                        )
            else:
                for data in result.data["records"]:
                    row = old.get(data["reference"])
                    if row is None:
                        row = Offence(
                            registration=registration, reference=data["reference"], data={}, revision=0
                        )
                        session.add(row)
                    before = row.data
                    if before != data or row.status != data["status"]:
                        row.revision += 1
                        record_change(session, registration, source, row.reference, before, data, now)
                    if (
                        before
                        and row.status == "PENDING"
                        and data["status"] == "PENDING"
                        and Decimal(data["penalty"]) > Decimal(before["penalty"])
                    ):
                        penalty_increases.append(row)
                    row.data, row.status, row.verified_at = data, data["status"], now
                    row.resolution = "portal_status" if row.status in ("PAID", "CLEARED") else None
    elif result.outcome == "NOT_FOUND":
        state.last_success = now  # A valid no-record response, not a replacement for last verified data.
        day = now.astimezone(TZ).date()
        if state.missing_day != day:
            state.missing_streak = (
                state.missing_streak + 1 if state.missing_day == day - timedelta(days=1) else 1
            )
            state.missing_day = day
    else:
        state.missing_streak, state.missing_day = 0, None
    session.flush()
    sync_issues(session, registration, settings, now)
    for offence in penalty_increases:
        issue = session.get(Issue, issue_id(registration, "pending_offence", offence.reference))
        if issue and issue.open:
            enqueue(
                session,
                f"penalty:{issue.id}:{offence.revision}",
                "urgent",
                [issue],
                now,
                f"Fleet alert: {registration} — reported penalty increased",
            )


def source_view(state, now, created_at):
    success = aware(state.last_success) if state and state.last_success else None
    return dict(
        source=state.source if state else None,
        outcome=state.outcome if state else "UNKNOWN",
        message=state.message if state else "",
        last_attempt=aware(state.last_attempt) if state else None,
        last_successful_check_at=success,
        data_verified_at=aware(state.data_verified_at) if state else None,
        stale=(now - (success or aware(created_at))) > timedelta(hours=36),
        current_lookup_confirmed=bool(state and state.outcome in ("FOUND", "NO_DEBT")),
        missing_streak=state.missing_streak if state else 0,
        reported_record=state.data if state else {},
        evidence=state.evidence if state else None,
        verified_record_evidence=state.verified_evidence if state else None,
    )


def issue_view(row):
    return dict(
        id=row.id,
        registration=row.registration,
        source=row.source,
        type=row.kind,
        severity=row.severity,
        record_key=row.record_key,
        due_date=row.due_date,
        calculated=row.calculated,
        suggested_action=row.action,
        first_seen=aware(row.first_seen),
        updated_at=aware(row.updated_at),
    )


def open_issues(session):
    rows = list(
        session.scalars(
            select(Issue)
            .join(Truck, Truck.registration == Issue.registration)
            .where(Issue.open.is_(True), Truck.active.is_(True))
        )
    )
    return sorted(
        rows,
        key=lambda row: (
            SEVERITY[row.severity],
            row.due_date.isoformat() if row.due_date else "9999",
            row.registration,
        ),
    )


def truck_view(session, truck, now, detail=False):
    states = {
        row.source: row
        for row in session.scalars(select(SourceState).where(SourceState.registration == truck.registration))
    }
    issues = [issue_view(row) for row in open_issues(session) if row.registration == truck.registration]
    view = dict(
        registration=truck.registration,
        active=truck.active,
        sources={
            source: {**source_view(states.get(source), now, truck.created_at), "source": source}
            for source in SOURCES
        },
        issues=issues,
    )
    if detail:
        view["offences"] = [
            dict(
                reference=row.reference,
                reported_record=row.data,
                effective_status=row.status,
                resolution=row.resolution,
                verified_at=aware(row.verified_at),
            )
            for row in session.scalars(select(Offence).where(Offence.registration == truck.registration))
        ]
    return view
