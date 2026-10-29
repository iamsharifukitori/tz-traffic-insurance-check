import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from fleet.config import Settings
from fleet.db import Base, Truck, database
from fleet.portals import Result
from fleet.service import replace_fleet

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 7, tzinfo=timezone.utc)


def migration_config(connection):
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", str(connection.engine.url).replace("%", "%%"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture
def system(tmp_path):
    url = os.getenv("TEST_DATABASE_URL", "sqlite:///" + (tmp_path / "fleet.db").as_posix())
    engine, factory = database(url)
    schema = None
    if engine.dialect.name == "postgresql":
        schema = "fleet_test_" + uuid.uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine.dispose()
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
        factory = sessionmaker(engine, expire_on_commit=False)
        with engine.begin() as connection:
            command.upgrade(migration_config(connection), "head")
    else:
        Base.metadata.create_all(engine)
    settings = Settings(
        database_url=url,
        api_key="test-key-" + "a" * 40,
        data_dir=tmp_path / "data",
        delay_min=0,
        delay_max=0,
        retry_delay=0,
    )
    with factory() as session:
        replace_fleet(session, ["T999ZZZ"])
        session.get(Truck, "T999ZZZ").created_at = NOW
        session.commit()
    yield SimpleNamespace(settings=settings, engine=engine, factory=factory, now=NOW)
    engine.dispose()
    if schema:
        cleanup, _ = database(url)
        with cleanup.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        cleanup.dispose()


def insurance(days=60, status="ACTIVE", reference="COVER-1"):
    end = (NOW.date() + timedelta(days=days)).isoformat()
    data = dict(
        reference=reference,
        status=status,
        insurer="Test Insurer",
        start_date="2026-01-01",
        expiry_date=end,
        reported={"End Date": end},
    )
    return Result("FOUND", data)


def inspection(days=60, inspected="2026-10-24", final="PASS", restriction="No", status="VALID"):
    data = dict(
        reference=inspected + ":Road Worthiness",
        inspection_date=inspected,
        expiry_date=(NOW.date() + timedelta(days=days)).isoformat(),
        final_result=final,
        status=status,
        restriction=restriction,
        reported={"Inspection Date": inspected, "Status": status},
    )
    return Result("FOUND", {"records": [data], "latest": data})


def offence(age=1, penalty="0.00", status="PENDING", reference="TEST-OFFENCE-001"):
    data = dict(
        reference=reference,
        description="Defective tires",
        location="KAHAMA",
        issued_date=(NOW.date() - timedelta(days=age)).isoformat(),
        charge="30000.00",
        penalty=penalty,
        status=status,
        reported={"Reference": reference, "Charge": "30,000.00", "Penalty": penalty, "Status": status},
    )
    return Result("FOUND", {"records": [data]})
