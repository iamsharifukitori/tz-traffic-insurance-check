import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from conftest import inspection, insurance, offence
from sqlalchemy import select

from fleet.db import DeliveryAttempt, Job, Notification, SourceState
from fleet.mail import dispatch, queue_digest
from fleet.portals import Result
from fleet.service import enqueue_job, ingest
from fleet.worker import Worker


def seed(system, source="offence", result=None):
    with system.factory() as session:
        ingest(session, "T999ZZZ", source, result or offence(), system.settings, system.now)
        session.commit()


def test_dry_run_does_not_send_and_is_deduplicated(system):
    seed(system)
    calls = []
    dispatch(system.factory, system.settings, system.now, calls.append)
    dispatch(system.factory, system.settings, system.now, calls.append)
    with system.factory() as session:
        messages = list(session.scalars(select(Notification)))
        assert len(messages) == 1 and messages[0].status == "dry_run"
    assert not calls
    preview = next((system.settings.data_dir / "mail-previews").glob("*.eml")).read_text()
    assert "Estimated deadline" in preview and "T999ZZZ" in preview


def test_smtp_retries_at_1_5_30_minutes_then_exposes_failure(system):
    seed(system)
    settings = replace(
        system.settings,
        dry_run=False,
        smtp_host="mail.example.invalid",
        sender="owner@example.com",
        recipients=("owner@example.com",),
    )

    def fail(message):
        raise OSError("connection failed")

    moment = system.now
    for attempt, minutes in enumerate((1, 5, 30, None), 1):
        dispatch(system.factory, settings, moment, fail)
        with system.factory() as session:
            row = session.scalar(select(Notification))
            assert row.attempts == attempt
            if minutes:
                assert row.status == "retry"
                from fleet.config import aware

                assert aware(row.next_attempt) == moment + timedelta(minutes=minutes)
                moment += timedelta(minutes=minutes)
            else:
                assert row.status == "failed"
    with system.factory() as session:
        attempts = list(session.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.number)))
        assert [row.number for row in attempts] == [1, 2, 3, 4]
        assert all(row.finished_at is not None for row in attempts)
        assert attempts[-1].status == "failed"


def test_successful_transport_and_digest_excludes_urgent(system):
    seed(system)
    settings = replace(
        system.settings,
        dry_run=False,
        smtp_host="mail.example.invalid",
        sender="owner@example.com",
        recipients=("owner@example.com",),
    )
    sent = []
    dispatch(system.factory, settings, system.now, sent.append)
    assert len(sent) == 1
    with system.factory() as session:
        assert queue_digest(session, system.now) is None
        ingest(session, "T999ZZZ", "insurance", insurance(14), settings, system.now)
        digest = queue_digest(session, system.now)
        assert len(digest.issue_ids) == 1 and "insurance" in digest.body
        assert queue_digest(session, system.now).id == digest.id
        session.commit()


def test_cancel_email_when_issue_resolved(system):
    seed(system)
    seed(system, result=Result("NO_DEBT", {"records": []}))
    dispatch(system.factory, system.settings, system.now)
    with system.factory() as session:
        assert session.scalar(select(Notification)).status == "cancelled"


class FakeCollector:
    calls = []
    results = {}

    def __init__(self, settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def lookup(self, registration, source, identity, attempt=1):
        self.calls.append((registration, source, attempt))
        return self.results.get(
            (source, attempt),
            {
                "insurance": insurance(),
                "inspection": inspection(),
                "offence": Result("NO_DEBT", {"records": []}),
            }[source],
        )


def worker(system):
    FakeCollector.calls, FakeCollector.results = [], {}
    return Worker(system.settings, system.factory, FakeCollector, lambda: system.now, lambda _: None)


def test_worker_block_is_isolated_to_one_portal(system):
    current = worker(system)
    FakeCollector.results[("inspection", 1)] = Result("BLOCKED")
    job = enqueue_job(system.factory)
    assert current.claim() == job.id
    current.run_job(job.id)
    with system.factory() as session:
        row = session.get(Job, job.id)
        assert row.status == "completed_with_errors"
        assert {x["source"]: x["outcome"] for x in row.results} == {
            "insurance": "FOUND",
            "inspection": "BLOCKED",
            "offence": "SKIPPED",
        }
        assert session.get(SourceState, ("T999ZZZ", "insurance")).data


def test_worker_retries_transient_failure_once(system):
    current = worker(system)
    FakeCollector.results[("insurance", 1)] = Result("ERROR", transient=True)
    FakeCollector.results[("insurance", 2)] = insurance()
    job = enqueue_job(system.factory)
    current.claim()
    current.run_job(job.id)
    assert ("T999ZZZ", "insurance", 2) in FakeCollector.calls
    with system.factory() as session:
        assert session.get(Job, job.id).status == "completed"


def test_worker_restarts_resume_completed_sources(system):
    current = worker(system)
    job = enqueue_job(system.factory)
    current.claim()
    with system.factory() as session:
        row = session.get(Job, job.id)
        row.results = [{"registration": "T999ZZZ", "source": "insurance", "outcome": "FOUND"}]
        session.commit()
    current.recover()
    assert current.claim() == job.id
    current.run_job(job.id)
    assert not any(source == "insurance" for _, source, _ in FakeCollector.calls)
    with system.factory() as session:
        assert len(session.get(Job, job.id).results) == 3


def test_evidence_retention_only_generated_directories(system):
    current = worker(system)
    root = system.settings.data_dir / "evidence"
    target = root / str(uuid.uuid4())
    target.mkdir(parents=True)
    (target / "evidence.json").touch()
    unrelated = root / "operator-notes"
    unrelated.mkdir()
    import os

    past = (system.now - timedelta(days=31)).timestamp()
    os.utime(target, (past, past))
    current.cleanup()
    assert not target.exists() and unrelated.exists()


def test_lost_worker_lease_prevents_record_commit(system):
    current = worker(system)
    calls = []

    def lease():
        calls.append(True)
        if len(calls) >= 2:
            raise RuntimeError("Lost worker lock")

    current.lease_check = lease
    job = enqueue_job(system.factory)
    current.claim()
    with pytest.raises(RuntimeError, match="Lost worker lock"):
        current.run_job(job.id)
    with system.factory() as session:
        assert session.get(SourceState, ("T999ZZZ", "insurance")) is None


def test_postgres_worker_lock_cannot_be_claimed_twice(system):
    if system.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL advisory-lock integration test")
    from sqlalchemy import text

    from fleet.worker import LOCK_ID

    with system.engine.connect() as first, system.engine.connect() as second:
        assert first.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID}) is True
        try:
            assert second.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID}) is False
        finally:
            first.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_ID})
            first.commit()


def test_smtp_starttls_and_partial_recipient_failure(system):
    import smtplib
    from email.message import EmailMessage
    from unittest.mock import patch

    from fleet.mail import send_smtp

    settings = replace(
        system.settings,
        smtp_host="mail.example.invalid",
        smtp_user="test-user",
        smtp_password="test-password",
        recipients=("owner@example.com",),
    )
    with patch("fleet.mail.smtplib.SMTP") as smtp:
        connection = smtp.return_value.__enter__.return_value
        connection.send_message.return_value = {"bad@example.com": (550, b"rejected")}
        with pytest.raises(smtplib.SMTPRecipientsRefused):
            send_smtp(EmailMessage(), settings)
        connection.starttls.assert_called_once()
        connection.login.assert_called_once_with("test-user", "test-password")


def test_restart_keeps_unknown_delivery_attempt(system):
    seed(system)
    with system.factory() as session:
        message = session.scalar(select(Notification))
        message.status, message.attempts = "sending", 1
        session.add(
            DeliveryAttempt(notification_id=message.id, number=1, status="sending", started_at=system.now)
        )
        session.commit()
    worker(system).recover()
    with system.factory() as session:
        assert session.scalar(select(Notification)).status == "retry"
        attempt = session.scalar(select(DeliveryAttempt))
        assert attempt.status == "unknown" and attempt.finished_at is not None
