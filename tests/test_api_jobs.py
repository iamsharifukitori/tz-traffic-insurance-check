from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from alembic import command
from conftest import migration_config, offence
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from fleet.api import create_app
from fleet.db import Job, database
from fleet.exports import current_file, publish
from fleet.service import enqueue_job, ingest, replace_fleet, schedule_due


@pytest.fixture
def client(system):
    app = create_app(system.settings, (system.engine, system.factory))
    with TestClient(app) as client:
        yield client


def auth(system):
    return {"Authorization": "Bearer " + system.settings.api_key}


@pytest.mark.parametrize(
    "path",
    [
        "/v1/trucks",
        "/v1/trucks/T999ZZZ",
        "/v1/issues",
        "/v1/health",
        "/v1/exports/results.csv",
        "/v1/checks/unknown",
        "/v1/evidence/unknown/a.json",
    ],
)
def test_authentication(client, path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong-key"}).status_code == 401


def test_api_fleet_validation_and_soft_remove(system, client):
    headers = auth(system)
    assert (
        client.put(
            "/v1/fleet", json={"registrations": ["T 999 ZZZ", "t999zzz", "T123ABC"]}, headers=headers
        ).json()["active_count"]
        == 2
    )
    assert client.put("/v1/fleet", json={"registrations": ["1. T123ABC"]}, headers=headers).status_code == 422
    assert (
        client.put(
            "/v1/fleet", json={"registrations": [f"T{x:03}ABC" for x in range(21)]}, headers=headers
        ).status_code
        == 422
    )
    assert client.post("/v1/checks").status_code == 401
    assert client.put("/v1/fleet", json={"registrations": []}).status_code == 401
    with system.factory() as session:
        ingest(session, "T999ZZZ", "offence", offence(), system.settings, system.now)
        session.commit()
    client.put("/v1/fleet", json={"registrations": ["T123ABC"]}, headers=headers)
    assert client.get("/v1/issues", headers=headers).json()["issues"] == []
    assert client.get("/v1/trucks/T999ZZZ", headers=headers).json()["active"] is False
    assert len(client.get("/v1/trucks", headers=headers).json()["trucks"]) == 1


def test_job_queue_is_idempotent_and_reports_progress(system, client):
    first = client.post("/v1/checks", headers=auth(system))
    second = client.post("/v1/checks", headers=auth(system))
    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert client.get("/v1/checks/" + first.json()["id"], headers=auth(system)).json()["total_sources"] == 3
    assert client.get("/v1/checks/missing", headers=auth(system)).status_code == 404


def test_scheduled_catchup_and_same_day_no_duplicate(system):
    before_six = system.now.replace(hour=2)  # 05:00 Tanzania.
    assert schedule_due(system.factory, before_six) is None
    first = schedule_due(system.factory, system.now)
    assert first.schedule_day == system.now.date()
    with system.factory() as session:
        session.get(Job, first.id).status = "completed"
        session.commit()
    assert schedule_due(system.factory, system.now + timedelta(hours=6)).id == first.id
    next_day = schedule_due(system.factory, system.now + timedelta(days=1))
    assert next_day.id != first.id


def test_competing_enqueues_share_one_job(system):
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: enqueue_job(system.factory), range(8)))
    assert len({job.id for job in jobs}) == 1


def test_empty_fleet_cannot_queue_and_protected_health(system, client):
    client.put("/v1/fleet", json={"registrations": []}, headers=auth(system))
    assert client.post("/v1/checks", headers=auth(system)).status_code == 409
    health = client.get("/v1/health", headers=auth(system)).json()
    assert health["email_dry_run"] is True and health["database"] == "ok"


def test_csv_generations_replace_atomically_and_download(system, client):
    with system.factory() as session:
        first = publish(session, system.settings.data_dir)
        assert current_file(system.settings.data_dir, "results.csv").parent.name == first
        replace_fleet(session, ["T123ABC"])
        session.commit()
        second = publish(session, system.settings.data_dir)
        assert second != first
    response = client.get("/v1/exports/results.csv", headers=auth(system))
    assert response.status_code == 200 and "T123ABC" in response.text and "T999ZZZ" not in response.text
    assert client.get("/v1/exports/secret.env", headers=auth(system)).status_code == 404


def test_issue_response_distinguishes_estimates(system, client):
    with system.factory() as session:
        ingest(session, "T999ZZZ", "offence", offence(), system.settings, system.now)
        session.commit()
    issue = client.get("/v1/issues", headers=auth(system)).json()["issues"][0]
    assert issue["calculated"]["deadline_estimated"] is True
    assert issue["calculated"]["reported_charge"] == "30000.00"
    assert "last_successful_check_at" in issue["verification"]


def test_migration_roundtrip(tmp_path):
    engine, _ = database("sqlite:///" + (tmp_path / "migrate.db").as_posix())
    with engine.begin() as connection:
        config = migration_config(connection)
        command.upgrade(config, "head")
        assert "jobs" in inspect(connection).get_table_names()
        indexes = connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        assert ("one_active_fleet_job",) in indexes
        command.downgrade(config, "base")
        assert "jobs" not in inspect(connection).get_table_names()
        command.upgrade(config, "head")
    engine.dispose()
