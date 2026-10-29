import argparse
import json
import secrets
from pathlib import Path

from .config import Settings
from .db import database
from .service import enqueue_job, fleet_registrations, replace_fleet


def read_trucks(path):
    values = [line.split("#", 1)[0].strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    return fleet_registrations(value for value in values if value)


def main():
    parser = argparse.ArgumentParser(description="Fleet monitoring administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("generate-key", help="Print a random API key; keep it private")
    fleet = sub.add_parser("import", help="Replace active fleet membership from a text file")
    fleet.add_argument("file", type=Path)
    sub.add_parser("check", help="Queue a check without starting another worker")
    args = parser.parse_args()
    if args.command == "generate-key":
        print(secrets.token_urlsafe(32))
        return
    settings = Settings.load()
    _, factory = database(settings.database_url)
    if args.command == "import":
        plates = read_trucks(args.file)
        with factory() as session:
            replace_fleet(session, plates)
            session.commit()
        print(json.dumps({"active_count": len(plates), "registrations": plates}))
    else:
        job = enqueue_job(factory)
        print(json.dumps({"job_id": job.id if job else None}))


if __name__ == "__main__":
    main()
