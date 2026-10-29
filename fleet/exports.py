"""Publish a complete CSV generation with one atomic manifest replacement."""

import csv
import json
import os
import shutil
import uuid

from sqlalchemy import select

from .db import Offence, SourceState, Truck

FILENAMES = ("results.csv", "offences.csv", "inspections.csv")


def safe(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def write_csv(path, rows, initial_fields):
    fields = list(initial_fields)
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: safe(value) for key, value in row.items()} for row in rows)
        stream.flush()
        os.fsync(stream.fileno())


def publish(session, data_dir):
    root = data_dir / "exports"
    root.mkdir(parents=True, exist_ok=True)
    identity = str(uuid.uuid4())
    target = root / identity
    target.mkdir()
    summaries, offences, inspections = [], [], []
    for truck in session.scalars(select(Truck).where(Truck.active.is_(True)).order_by(Truck.registration)):
        row = {"registration": truck.registration}
        for state in session.scalars(
            select(SourceState).where(SourceState.registration == truck.registration)
        ):
            row[f"{state.source}_lookup"] = state.outcome
            row[f"{state.source}_last_success"] = state.last_success
            row[f"{state.source}_data_verified_at"] = state.data_verified_at
            if state.source == "insurance" and state.data:
                for key in ("status", "expiry_date", "insurer", "reference"):
                    row[f"insurance_{key}"] = state.data[key]
            if state.source == "inspection" and state.data:
                for key in ("inspection_date", "expiry_date", "status", "final_result", "restriction"):
                    row[f"inspection_{key}"] = state.data["latest"][key]
                inspections.extend(
                    {"registration": truck.registration, **record["reported"]}
                    for record in state.data["records"]
                )
        summaries.append(row)
        for offence in session.scalars(select(Offence).where(Offence.registration == truck.registration)):
            offences.append(
                {
                    "registration": truck.registration,
                    **offence.data["reported"],
                    "Effective Status": offence.status,
                    "Resolution": offence.resolution,
                    "Last Verified": offence.verified_at,
                }
            )
    write_csv(target / "results.csv", summaries, ["registration"])
    write_csv(target / "offences.csv", offences, ["registration"])
    write_csv(target / "inspections.csv", inspections, ["registration"])
    temporary = root / "current.tmp"
    temporary.write_text(json.dumps({"generation": identity}), encoding="utf-8")
    os.replace(temporary, root / "current.json")

    # Keep one previous generation so in-flight downloads can finish.
    def generated_directory(path):
        try:
            uuid.UUID(path.name)
            return path.is_dir() and not path.is_symlink() and path.resolve().is_relative_to(root.resolve())
        except ValueError:
            return False

    generations = sorted(
        (p for p in root.iterdir() if generated_directory(p)), key=lambda p: p.stat().st_mtime, reverse=True
    )
    for previous in generations[2:]:
        if previous.name != identity and previous.parent == root and not previous.is_symlink():
            shutil.rmtree(previous)
    return identity


def current_file(data_dir, filename):
    if filename not in FILENAMES:
        raise FileNotFoundError(filename)
    root = data_dir / "exports"
    manifest = json.loads((root / "current.json").read_text(encoding="utf-8"))
    identity = str(uuid.UUID(manifest["generation"]))
    path = root / identity / filename
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise FileNotFoundError(filename)
    return path
