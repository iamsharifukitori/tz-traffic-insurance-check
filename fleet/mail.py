import logging
import smtplib
import ssl
from datetime import datetime, time, timedelta, timezone
from email.message import EmailMessage

from sqlalchemy import select

from .config import TZ, aware, utcnow
from .db import DeliveryAttempt, Issue, Notification, SourceState, Truck, uid
from .rules import SEVERITY

log = logging.getLogger(__name__)


def render(session, issues, now, heading):
    issues = sorted(
        issues,
        key=lambda issue: (SEVERITY[issue.severity], str(issue.due_date or "9999"), issue.registration),
    )
    lines = [heading, "", "Checked information: " + now.astimezone(TZ).isoformat(), ""]
    for issue in issues:
        state = session.get(SourceState, (issue.registration, issue.source))
        lines.extend(
            [
                f"{issue.registration} — {issue.kind.replace('_', ' ')} [{issue.severity}]",
                issue.action,
                f"Record/reference: {issue.record_key}",
            ]
        )
        if issue.due_date:
            label = "Estimated deadline" if issue.calculated.get("deadline_estimated") else "Expiry/deadline"
            lines.append(f"{label}: {issue.due_date.isoformat()} (Tanzania date)")
        for key, value in issue.calculated.items():
            if value is not None:
                lines.append(f"{key.replace('_', ' ')}: {value}")
        if state:
            lines.append(
                f"Latest lookup: {state.outcome}; last successful check: {aware(state.last_success)}"
            )
            if state.outcome not in ("FOUND", "NO_DEBT"):
                lines.append("Current lookup is uncertain; retained information may be out of date.")
        lines.append("")
    lines.append(
        "Penalty amounts are reported by TMS. Seven-day offence deadlines are estimates, not calculated penalty charges."
    )
    return "\n".join(lines)


def enqueue(session, key, kind, issues, now, heading):
    existing = session.scalar(select(Notification).where(Notification.event_key == key))
    if existing:
        return existing
    row = Notification(
        id=uid(),
        event_key=key,
        kind=kind,
        issue_ids=[issue.id for issue in issues],
        subject=heading,
        body=render(session, issues, now, heading),
        status="pending",
        attempts=0,
        next_attempt=now,
        created_at=now,
    )
    session.add(row)
    session.flush()
    return row


def queue_digest(session, now):
    day = now.astimezone(TZ).date()
    start = datetime.combine(day, time.min, tzinfo=TZ).astimezone(timezone.utc)
    delivered = session.scalars(
        select(Notification).where(
            Notification.kind == "urgent",
            Notification.status.in_(("sent", "dry_run")),
            Notification.sent_at >= start,
        )
    ).all()
    already_sent = {identity for item in delivered for identity in item.issue_ids}
    issues = session.scalars(
        select(Issue)
        .join(Truck, Truck.registration == Issue.registration)
        .where(Issue.open.is_(True), Truck.active.is_(True))
    ).all()
    issues = [issue for issue in issues if issue.id not in already_sent]
    if issues:
        return enqueue(
            session,
            f"digest:{day}",
            "digest",
            issues,
            now,
            f"Fleet daily digest: {len(issues)} issue(s) — {day}",
        )
    return None


def dispatch(factory, settings, now=None, transport=None):
    now = now or utcnow()
    with factory() as session:
        identities = session.scalars(
            select(Notification.id)
            .where(Notification.status.in_(("pending", "retry")), Notification.next_attempt <= now)
            .order_by(Notification.created_at)
        ).all()
    for identity in identities:
        with factory() as session:
            row = session.get(Notification, identity)
            issues = session.scalars(
                select(Issue)
                .join(Truck, Truck.registration == Issue.registration)
                .where(Issue.id.in_(row.issue_ids), Issue.open.is_(True), Truck.active.is_(True))
            ).all()
            if not issues:
                row.status = "cancelled"
                session.commit()
                continue
            row.body = render(session, issues, now, row.subject)
            message = EmailMessage()
            message["Subject"] = row.subject
            message["From"] = settings.sender
            message["To"] = ", ".join(settings.recipients or ("preview@example.invalid",))
            message["Message-ID"] = f"<{row.id}@fleet-monitor.local>"
            message.set_content(row.body)
            row.status = "sending"
            row.attempts += 1
            attempt = DeliveryAttempt(
                notification_id=row.id, number=row.attempts, status="sending", started_at=now
            )
            session.add(attempt)
            session.commit()
            try:
                if settings.dry_run:
                    previews = settings.data_dir / "mail-previews"
                    previews.mkdir(parents=True, exist_ok=True, mode=0o700)
                    (previews / f"{identity}.eml").write_bytes(message.as_bytes())
                elif transport:
                    transport(message)
                else:
                    send_smtp(message, settings)
                row.status = "dry_run" if settings.dry_run else "sent"
                row.sent_at = now
                row.last_error = ""
            except Exception as exc:
                row.last_error = f"{type(exc).__name__}: email delivery failed; inspect SMTP configuration."
                log.warning("Email %s failed: %s", identity, type(exc).__name__)
                if row.attempts <= 3:
                    row.status = "retry"
                    row.next_attempt = now + timedelta(minutes=(1, 5, 30)[row.attempts - 1])
                else:
                    row.status = "failed"
            attempt.status = row.status
            attempt.finished_at = utcnow()
            attempt.error = row.last_error
            session.commit()


def send_smtp(message, settings):
    client = smtplib.SMTP_SSL if settings.smtp_mode == "ssl" else smtplib.SMTP
    kwargs = {"context": ssl.create_default_context()} if settings.smtp_mode == "ssl" else {}
    with client(settings.smtp_host, settings.smtp_port, timeout=30, **kwargs) as smtp:
        if settings.smtp_mode == "starttls":
            smtp.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        refused = smtp.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
