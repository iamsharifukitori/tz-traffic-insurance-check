import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Dar_es_Salaam")


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    data_dir: Path = Path("/data")
    offence_days: int = 7
    dry_run: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_mode: str = "starttls"
    smtp_user: str = ""
    smtp_password: str = ""
    sender: str = "fleet@example.invalid"
    recipients: tuple[str, ...] = ()
    delay_min: float = 5
    delay_max: float = 9
    retry_delay: float = 60
    browser_sandbox: bool = True

    def __post_init__(self):
        if not self.database_url or len(self.api_key) < 32 or self.api_key.startswith("CHANGE_"):
            raise ValueError("Set DATABASE_URL and a randomly generated API_KEY of at least 32 characters.")
        if not 1 <= self.offence_days <= 365:
            raise ValueError("OFFENCE_DEADLINE_DAYS must be between 1 and 365.")
        if self.smtp_mode not in ("starttls", "ssl", "plain"):
            raise ValueError("SMTP_MODE must be starttls, ssl, or plain.")
        if not self.dry_run and (
            not self.smtp_host or not self.recipients or self.sender.endswith(".invalid")
        ):
            raise ValueError("Real email requires SMTP_HOST, EMAIL_FROM and EMAIL_TO.")
        if self.delay_min < 0 or self.delay_max < self.delay_min:
            raise ValueError("Invalid portal delay configuration.")

    @classmethod
    def load(cls):
        return cls(
            database_url=os.getenv("DATABASE_URL", ""),
            api_key=os.getenv("API_KEY", ""),
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            offence_days=int(os.getenv("OFFENCE_DEADLINE_DAYS", "7")),
            dry_run=os.getenv("EMAIL_DRY_RUN", "true").lower() == "true",
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_mode=os.getenv("SMTP_MODE", "starttls"),
            smtp_user=os.getenv("SMTP_USER", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            sender=os.getenv("EMAIL_FROM", "fleet@example.invalid"),
            recipients=tuple(x.strip() for x in os.getenv("EMAIL_TO", "").split(",") if x.strip()),
            browser_sandbox=os.getenv("BROWSER_SANDBOX", "true").lower() == "true",
        )
