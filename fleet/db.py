import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import utcnow


def uid():
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Truck(Base):
    __tablename__ = "trucks"
    registration: Mapped[str] = mapped_column(String(20), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceState(Base):
    __tablename__ = "source_states"
    registration: Mapped[str] = mapped_column(String(20), primary_key=True)
    source: Mapped[str] = mapped_column(String(20), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(30), default="UNKNOWN")
    message: Mapped[str] = mapped_column(Text, default="")
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[str | None] = mapped_column(String(240))
    verified_evidence: Mapped[str | None] = mapped_column(String(240))
    missing_day: Mapped[date | None] = mapped_column(Date)
    missing_streak: Mapped[int] = mapped_column(Integer, default=0)


class Offence(Base):
    __tablename__ = "offences"
    registration: Mapped[str] = mapped_column(String(20), primary_key=True)
    reference: Mapped[str] = mapped_column(String(100), primary_key=True)
    status: Mapped[str] = mapped_column(String(40))
    resolution: Mapped[str | None] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Change(Base):
    __tablename__ = "record_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    registration: Mapped[str] = mapped_column(String(20), index=True)
    source: Mapped[str] = mapped_column(String(20))
    record_key: Mapped[str] = mapped_column(String(120), default="")
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "one_active_fleet_job",
            text("(1)"),
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    schedule_day: Mapped[date | None] = mapped_column(Date, unique=True)
    registrations: Mapped[list] = mapped_column(JSON)
    results: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")


class Issue(Base):
    __tablename__ = "issues"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    registration: Mapped[str] = mapped_column(String(20), index=True)
    source: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(50))
    record_key: Mapped[str] = mapped_column(String(160))
    severity: Mapped[str] = mapped_column(String(20))
    due_date: Mapped[date | None] = mapped_column(Date)
    calculated: Mapped[dict] = mapped_column(JSON)
    action: Mapped[str] = mapped_column(Text)
    milestone: Mapped[str] = mapped_column(String(40))
    generation: Mapped[int] = mapped_column(Integer, default=1)
    open: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("event_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_key: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(20))
    issue_ids: Mapped[list] = mapped_column(JSON)
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(Text, default="")


class Runtime(Base):
    __tablename__ = "runtime"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    heartbeat: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    notification_id: Mapped[str] = mapped_column(String(36), index=True)
    number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str] = mapped_column(Text, default="")


def database(url):
    options = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, pool_pre_ping=True, connect_args=options)
    return engine, sessionmaker(engine, expire_on_commit=False)
