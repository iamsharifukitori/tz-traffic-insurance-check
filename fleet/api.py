import hmac
import re
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text

from .config import Settings, aware, utcnow
from .db import Job, Notification, Runtime, SourceState, Truck, database
from .exports import current_file
from .portals import normalize_plate
from .service import enqueue_job, issue_view, open_issues, replace_fleet, source_view, truck_view


class FleetInput(BaseModel):
    registrations: list[str] = Field(max_length=100)


def job_view(job):
    return dict(
        id=job.id,
        status=job.status,
        schedule_day=job.schedule_day,
        registrations=job.registrations,
        completed_sources=len(job.results),
        total_sources=len(job.registrations) * 3,
        results=job.results,
        source_failures=[
            row for row in job.results if row["outcome"] not in ("FOUND", "NO_DEBT", "NOT_FOUND")
        ],
        created_at=aware(job.created_at),
        started_at=aware(job.started_at),
        finished_at=aware(job.finished_at),
        error=job.error,
    )


def create_app(settings=None, database_pair=None):
    settings = settings or Settings.load()
    engine, factory = database_pair or database(settings.database_url)
    bearer = HTTPBearer(auto_error=False)

    def authorized(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode(), settings.api_key.encode()
        ):
            raise HTTPException(
                401, "A valid bearer API key is required.", headers={"WWW-Authenticate": "Bearer"}
            )

    app = FastAPI(
        title="Fleet Deadline and Offence Monitoring API",
        version="1.0.0",
        description="Single-owner fleet API. Records are cached portal observations; offence deadlines are estimates.",
    )
    app.state.engine, app.state.factory, app.state.settings = engine, factory, settings

    def session():
        with factory() as item:
            yield item

    @app.get("/v1/trucks", dependencies=[Depends(authorized)])
    def trucks(include_inactive: bool = False, db=Depends(session)):
        query = select(Truck).order_by(Truck.registration)
        if not include_inactive:
            query = query.where(Truck.active.is_(True))
        return {"trucks": [truck_view(db, truck, utcnow()) for truck in db.scalars(query)]}

    @app.get("/v1/trucks/{registration}", dependencies=[Depends(authorized)])
    def truck(registration: str, db=Depends(session)):
        try:
            registration = normalize_plate(registration)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        row = db.get(Truck, registration)
        if not row:
            raise HTTPException(404, "Truck not found.")
        return truck_view(db, row, utcnow(), detail=True)

    @app.put("/v1/fleet", dependencies=[Depends(authorized)])
    def fleet(body: FleetInput, db=Depends(session)):
        try:
            plates = replace_fleet(db, body.registrations)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        db.commit()
        return {"registrations": plates, "active_count": len(plates)}

    @app.get("/v1/issues", dependencies=[Depends(authorized)])
    def issues(db=Depends(session)):
        now = utcnow()
        values = []
        for row in open_issues(db):
            state = db.get(SourceState, (row.registration, row.source))
            truck = db.get(Truck, row.registration)
            verification = source_view(state, now, truck.created_at)
            verification.pop("reported_record")
            values.append({**issue_view(row), "verification": verification})
        return {"issues": values}

    @app.post("/v1/checks", status_code=202, dependencies=[Depends(authorized)])
    def check():
        job = enqueue_job(factory)
        if not job:
            raise HTTPException(409, "Import at least one active truck before requesting a check.")
        return job_view(job)

    @app.get("/v1/checks/{identity}", dependencies=[Depends(authorized)])
    def check_status(identity: str, db=Depends(session)):
        row = db.get(Job, identity)
        if not row:
            raise HTTPException(404, "Check job not found.")
        return job_view(row)

    @app.get("/v1/exports/{filename}", dependencies=[Depends(authorized)])
    def export(filename: str):
        try:
            path = current_file(settings.data_dir, filename)
        except (FileNotFoundError, ValueError, KeyError):
            raise HTTPException(404, "No completed export exists for this filename.")
        return FileResponse(path, media_type="text/csv", filename=filename)

    @app.get("/v1/evidence/{identity}/{filename}", dependencies=[Depends(authorized)])
    def evidence(identity: str, filename: str):
        try:
            identity = str(uuid.UUID(identity))
        except ValueError:
            raise HTTPException(404)
        if not re.fullmatch(r"[A-Z0-9-]{3,20}_(?:insurance|inspection|offence)_[12]\.(?:json|png)", filename):
            raise HTTPException(404)
        root = (settings.data_dir / "evidence").resolve()
        path = root / identity / filename
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "Evidence is unavailable or has passed its 30-day retention.")
        return FileResponse(
            path, media_type="image/png" if Path(filename).suffix == ".png" else "application/json"
        )

    @app.get("/v1/health", dependencies=[Depends(authorized)])
    def health(db=Depends(session)):
        db.execute(text("SELECT 1"))
        last = db.scalar(select(Job).order_by(Job.created_at.desc()).limit(1))
        heartbeat = db.get(Runtime, 1)
        failed = db.scalar(
            select(func.count()).select_from(Notification).where(Notification.status == "failed")
        )
        retrying = db.scalar(
            select(func.count()).select_from(Notification).where(Notification.status == "retry")
        )
        return dict(
            database="ok",
            worker_heartbeat=aware(heartbeat.heartbeat) if heartbeat else None,
            last_batch=job_view(last) if last else None,
            email_dry_run=settings.dry_run,
            email_failures=failed,
            email_retrying=retrying,
            timezone="Africa/Dar_es_Salaam",
        )

    return app
