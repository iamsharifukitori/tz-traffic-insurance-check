import argparse
import logging
import random
import shutil
import time
import uuid
from datetime import timedelta

from sqlalchemy import select, text

from .config import Settings, utcnow
from .db import DeliveryAttempt, Job, Notification, Runtime, Truck, database
from .exports import publish
from .mail import dispatch, queue_digest
from .portals import Collector, Result
from .rules import SOURCES
from .service import ingest, schedule_due, sync_issues

log = logging.getLogger(__name__)
LOCK_ID = 481902


class Worker:
    def __init__(
        self,
        settings,
        factory,
        collector_factory=Collector,
        clock=utcnow,
        sleeper=time.sleep,
        lease_check=lambda: None,
    ):
        self.settings, self.factory = settings, factory
        self.collector_factory, self.clock, self.sleep = collector_factory, clock, sleeper
        self.lease_check = lease_check

    def heartbeat(self, session):
        row = session.get(Runtime, 1)
        if row is None:
            session.add(Runtime(id=1, heartbeat=self.clock()))
        else:
            row.heartbeat = self.clock()

    def recover(self):
        # Called only after taking the process-wide PostgreSQL advisory lock.
        with self.factory() as session:
            for job in session.scalars(select(Job).where(Job.status == "running")):
                job.status = "queued"
            for message in session.scalars(select(Notification).where(Notification.status == "sending")):
                message.status = "retry" if message.attempts < 4 else "failed"
                message.next_attempt = self.clock()
                message.last_error = "Worker restarted during delivery; SMTP acceptance may be unknown."
            for attempt in session.scalars(
                select(DeliveryAttempt).where(DeliveryAttempt.status == "sending")
            ):
                attempt.status = "unknown"
                attempt.finished_at = self.clock()
                attempt.error = "Worker restarted; provider acceptance was not confirmed."
            session.commit()

    def claim(self):
        with self.factory() as session:
            job = session.scalar(
                select(Job)
                .where(Job.status == "queued")
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if not job:
                return None
            job.status, job.started_at = "running", job.started_at or self.clock()
            session.commit()
            return job.id

    def run_job(self, identity):
        with self.factory() as session:
            job = session.get(Job, identity)
            plates, previous = job.registrations, job.results
        done = {(row["registration"], row["source"]) for row in previous}
        blocked = {
            "tira" if row["source"] == "insurance" else "tms"
            for row in previous
            if row["outcome"] == "BLOCKED"
        }
        searches = 0
        try:
            with self.collector_factory(self.settings) as collector:
                for registration in plates:
                    for source in SOURCES:
                        self.lease_check()
                        if (registration, source) in done:
                            continue
                        portal = "tira" if source == "insurance" else "tms"
                        if portal in blocked:
                            result = Result(
                                "SKIPPED", message="This portal was blocked earlier in the batch."
                            )
                        else:
                            if searches:
                                self.sleep(random.uniform(self.settings.delay_min, self.settings.delay_max))
                            result = collector.lookup(registration, source, identity)
                            searches += 1
                            if result.transient and result.outcome == "ERROR":
                                self.sleep(self.settings.retry_delay)
                                result = collector.lookup(registration, source, identity, attempt=2)
                            if result.outcome == "BLOCKED":
                                blocked.add(portal)
                        now = self.clock()
                        self.lease_check()
                        with self.factory() as session:
                            ingest(session, registration, source, result, self.settings, now)
                            current = session.get(Job, identity)
                            current.results = [
                                *current.results,
                                dict(
                                    registration=registration,
                                    source=source,
                                    outcome=result.outcome,
                                    message=result.message,
                                    checked_at=now.isoformat(),
                                ),
                            ]
                            self.heartbeat(session)
                            session.commit()
                        log.info("Job %s: %s %s %s", identity, registration, source, result.outcome)
                        dispatch(self.factory, self.settings, self.clock())
            with self.factory() as session:
                for registration in session.scalars(select(Truck.registration).where(Truck.active.is_(True))):
                    sync_issues(session, registration, self.settings, self.clock())
                session.commit()
                publish(session, self.settings.data_dir)
                job = session.get(Job, identity)
                if job.schedule_day:
                    queue_digest(session, self.clock())
                errors = any(row["outcome"] in ("ERROR", "BLOCKED", "SKIPPED") for row in job.results)
                job.status = "completed_with_errors" if errors else "completed"
                job.finished_at = self.clock()
                session.commit()
            dispatch(self.factory, self.settings, self.clock())
        except Exception as exc:
            self.lease_check()
            log.exception("Batch %s failed", identity)
            with self.factory() as session:
                job = session.get(Job, identity)
                job.status, job.error, job.finished_at = (
                    "failed",
                    f"{type(exc).__name__}: batch failed; inspect worker logs.",
                    self.clock(),
                )
                session.commit()

    def cleanup(self):
        root = self.settings.data_dir / "evidence"
        if not root.exists():
            return
        cutoff = (self.clock() - timedelta(days=30)).timestamp()
        for directory in root.iterdir():
            try:
                uuid.UUID(directory.name)
            except ValueError:
                continue
            if (
                directory.is_dir()
                and not directory.is_symlink()
                and directory.resolve().is_relative_to(root.resolve())
                and directory.stat().st_mtime < cutoff
            ):
                shutil.rmtree(directory)

    def tick(self):
        now = self.clock()
        with self.factory() as session:
            self.heartbeat(session)
            for registration in session.scalars(select(Truck.registration).where(Truck.active.is_(True))):
                sync_issues(session, registration, self.settings, now)
            session.commit()
        schedule_due(self.factory, now)
        dispatch(self.factory, self.settings, now)
        identity = self.claim()
        if identity:
            self.run_job(identity)
        self.cleanup()
        return identity


def main():
    parser = argparse.ArgumentParser(description="Single-worker fleet scheduler")
    parser.add_argument("--once", action="store_true", help="Run one scheduler tick/batch, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.load()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine, factory = database(settings.database_url)
    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            "The deployed worker requires PostgreSQL. SQLite is supported only for isolated tests."
        )
    with engine.connect() as lock:
        if not lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_ID}):
            raise RuntimeError("Another fleet worker already holds the worker lock.")
        lock.commit()

        def lease_check():
            if lock.invalidated or lock.closed:
                raise RuntimeError("Lost worker lock connection; stopping worker.")
            held = lock.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
                    "AND pid=pg_backend_pid() AND objid=:key AND granted)"
                ),
                {"key": LOCK_ID},
            )
            lock.commit()
            if not held:
                raise RuntimeError("Lost worker lock; stopping worker.")

        worker = Worker(settings, factory, lease_check=lease_check)
        worker.recover()
        while True:
            try:
                # Keep the advisory-lock connection alive; losing it must stop this worker.
                lease_check()
                identity = worker.tick()
                if args.once:
                    with factory() as session:
                        job = session.get(Job, identity) if identity else None
                        return 1 if job and job.status in ("failed", "completed_with_errors") else 0
            except KeyboardInterrupt:
                break
            except Exception:
                log.exception("Worker tick failed")
                # A lost lock is fatal, preventing two workers from proceeding after reconnection.
                lease_check()
            time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
