from datetime import timedelta

import pytest
from conftest import inspection, insurance, offence
from sqlalchemy import select

from fleet.db import Change, Issue, Notification, Offence, SourceState
from fleet.portals import Result
from fleet.service import ingest, open_issues, source_view, sync_issues


def apply(system, source, result, now=None):
    with system.factory() as session:
        ingest(session, "T999ZZZ", source, result, system.settings, now or system.now)
        session.commit()


@pytest.mark.parametrize(
    "days,expected",
    [(31, None), (30, "30"), (25, "30"), (14, "14"), (7, "7"), (3, "3"), (1, "1"), (0, "0"), (-1, "expired")],
)
def test_expiry_boundaries(system, days, expected):
    apply(system, "insurance", insurance(days))
    with system.factory() as session:
        rows = open_issues(session)
        assert ([row.milestone for row in rows] or [None]) == [expected]
        if days == 0:
            assert rows[0].severity == "warning"
        if days < 0:
            assert rows[0].severity == "critical"


def test_explicit_expired_and_initial_urgency_and_renewal(system):
    apply(system, "insurance", insurance(20, status="EXPIRED"))
    apply(system, "insurance", insurance(20, status="EXPIRED"))
    with system.factory() as session:
        assert len(list(session.scalars(select(Notification)))) == 1
    apply(system, "insurance", insurance(100, reference="COVER-2"))
    with system.factory() as session:
        assert open_issues(session) == []
        assert len(list(session.scalars(select(Change)))) == 2


def test_six_month_inspection_and_latest_dates(system):
    apply(system, "inspection", inspection(177, inspected="2026-10-24"))
    with system.factory() as session:
        assert open_issues(session) == []
    apply(system, "inspection", inspection(7))
    with system.factory() as session:
        assert open_issues(session)[0].kind == "inspection_expiry"


def test_failed_inspection_and_restriction(system):
    apply(system, "inspection", inspection(final="FAIL", restriction="Goods only"))
    with system.factory() as session:
        assert {row.kind for row in open_issues(session)} == {"inspection_failed", "inspection_restriction"}
        assert len(list(session.scalars(select(Notification)))) == 2


@pytest.mark.parametrize("age,milestone", [(1, "new"), (5, "day5"), (6, "day6"), (7, "day7"), (8, "overdue")])
def test_offence_deadlines(system, age, milestone):
    apply(system, "offence", offence(age))
    with system.factory() as session:
        issue = open_issues(session)[0]
        assert issue.milestone == milestone
        assert issue.calculated["deadline_estimated"] is True
        assert issue.calculated["reported_penalty"] == "0.00"
        assert issue.calculated["days_to_estimated_deadline"] == 7 - age


def test_pending_survives_absence_and_failed_lookup(system):
    apply(system, "offence", offence())
    apply(system, "offence", Result("FOUND", {"records": []}))
    apply(system, "offence", Result("ERROR", message="timeout"))
    with system.factory() as session:
        assert open_issues(session)[0].kind == "pending_offence"
        assert session.get(Offence, ("T999ZZZ", "TEST-OFFENCE-001")).status == "PENDING"
        state = session.get(SourceState, ("T999ZZZ", "offence"))
        assert state.outcome == "ERROR"
    apply(system, "offence", Result("NO_DEBT", {"records": []}))
    with system.factory() as session:
        assert open_issues(session) == []
        row = session.get(Offence, ("T999ZZZ", "TEST-OFFENCE-001"))
        assert row.status == "CLEARED" and row.resolution == "portal_no_debt"


def test_explicit_paid_closes_issue_and_reappearing_pending_reopens(system):
    apply(system, "offence", offence())
    apply(system, "offence", offence(status="PAID"))
    with system.factory() as session:
        assert open_issues(session) == []
    apply(system, "offence", offence())
    with system.factory() as session:
        assert open_issues(session)[0].generation == 2
        assert len(list(session.scalars(select(Notification)))) == 2


def test_penalty_increase_once_per_change(system):
    apply(system, "offence", offence())
    apply(system, "offence", offence(penalty="7500.00"))
    apply(system, "offence", offence(penalty="7500.00"))
    with system.factory() as session:
        messages = list(session.scalars(select(Notification)))
        assert len(messages) == 2
        assert sum("penalty increased" in message.subject for message in messages) == 1
        assert open_issues(session)[0].calculated["reported_penalty"] == "7500.00"


def test_missing_requires_distinct_consecutive_days(system):
    apply(system, "insurance", insurance())
    for offset in (0, 0):
        apply(system, "insurance", Result("NOT_FOUND"), system.now + timedelta(days=offset))
    with system.factory() as session:
        assert not open_issues(session)
    apply(system, "insurance", Result("NOT_FOUND"), system.now + timedelta(days=1))
    with system.factory() as session:
        assert [row.kind for row in open_issues(session)] == ["record_missing"]
        state = session.get(SourceState, ("T999ZZZ", "insurance"))
        assert state.data["reference"] == "COVER-1"
    apply(system, "insurance", insurance(), system.now + timedelta(days=1))
    with system.factory() as session:
        assert not open_issues(session)


def test_gap_and_error_break_missing_streak(system):
    apply(system, "inspection", Result("NOT_FOUND"))
    apply(system, "inspection", Result("NOT_FOUND"), system.now + timedelta(days=2))
    apply(system, "inspection", Result("ERROR"), system.now + timedelta(days=2))
    with system.factory() as session:
        assert session.get(SourceState, ("T999ZZZ", "inspection")).missing_streak == 0


def test_stale_after_36_hours_preserves_last_good(system):
    apply(system, "insurance", insurance())
    later = system.now + timedelta(hours=36, seconds=1)
    apply(system, "insurance", Result("BLOCKED"), later)
    with system.factory() as session:
        state = session.get(SourceState, ("T999ZZZ", "insurance"))
        assert source_view(state, later, system.now)["stale"] is True
        assert state.data["reference"] == "COVER-1"
        assert any(row.kind == "source_stale" for row in open_issues(session))


def test_tanzania_midnight_changes_expiry_day(system):
    apply(system, "insurance", insurance(0))
    # 21:01 UTC is the next calendar day in Tanzania.
    next_day = system.now.replace(hour=21, minute=1)
    with system.factory() as session:
        sync_issues(session, "T999ZZZ", system.settings, next_day)
        session.commit()
        issue = session.scalar(select(Issue).where(Issue.open.is_(True)))
        assert issue.milestone == "expired"


def test_healthy_sources_are_not_issues(system):
    apply(system, "insurance", insurance())
    apply(system, "inspection", inspection())
    apply(system, "offence", Result("NO_DEBT", {"records": []}))
    with system.factory() as session:
        assert open_issues(session) == []
        assert list(session.scalars(select(Notification))) == []


def test_error_evidence_does_not_replace_verified_evidence(system):
    result = insurance()
    result.evidence = "evidence/first/T999ZZZ_insurance_1.json"
    apply(system, "insurance", result)
    apply(system, "insurance", Result("ERROR", evidence="evidence/later/T999ZZZ_insurance_1.json"))
    with system.factory() as session:
        state = session.get(SourceState, ("T999ZZZ", "insurance"))
        view = source_view(state, system.now, system.now)
        assert view["verified_record_evidence"] == result.evidence
        assert view["evidence"] != result.evidence
