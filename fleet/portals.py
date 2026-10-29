"""Fresh-page public lookups. Selectors originate in the tested Desktop tools."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

BLOCK = re.compile(r"too many requests|rate limit|access denied|verify you are human|captcha", re.I)
NOT_FOUND = re.compile(
    r"no (?:matching )?(?:records?|results?|cover notes?|insurance)(?:\s+(?:was|were))?\s+found|(?:record|cover note|insurance)\s+(?:was\s+)?not found",
    re.I,
)
TIRA_READ = r"""() => {
    const text = document.body.innerText;
    const status = text.match(/Insurance Cover Note Status Is\s+([^\n]+)/i);
    const fields = {};
    for (const label of document.querySelectorAll('label')) {
        const value = label.parentElement.querySelector('p');
        if (value) fields[label.innerText.trim()] = value.innerText.trim();
    }
    return {text, status: status ? status[1].trim() : '', fields};
}"""
TIRA_WAIT = r"""() => /Insurance Cover Note Status Is|no (?:matching )?(?:records?|results?|cover notes?|insurance)(?:\s+(?:was|were))?\s+found|(?:record|cover note|insurance)\s+(?:was\s+)?not found|too many requests|rate limit|access denied|verify you are human|captcha|service unavailable|something went wrong|try again later/i.test(document.body.innerText)"""
TMS_READ = r"""() => ({
    text: document.body.innerText,
    popup: document.querySelector('#swal2-title')?.closest('[role="dialog"]')?.innerText
        || document.querySelector('#swal2-title')?.parentElement?.innerText || '',
    headings: [...document.querySelectorAll('h1,h2,h3')].map(e => e.innerText.trim()),
    tables: [...document.querySelectorAll('table.transaction-table, table.bill-table')].map(table => ({
        headers: [...table.querySelectorAll('thead th')].map(e => e.innerText.trim()),
        rows: [...table.querySelectorAll('tbody tr')].map(tr => [...tr.querySelectorAll('td')].map(e => e.innerText.trim()))
    }))
})"""
TMS_WAIT = r"""() => !!document.querySelector('#swal2-title')
    || !!document.querySelector('table.transaction-table tbody tr, table.bill-table tbody tr')
    || /too many requests|rate limit|access denied|verify you are human|captcha/i.test(document.body.innerText)"""


@dataclass
class Result:
    outcome: str
    data: dict = field(default_factory=dict)
    message: str = ""
    evidence: str | None = None
    transient: bool = False


def normalize_plate(value):
    plate = re.sub(r"\s+", "", value).upper()
    if not re.fullmatch(r"[A-Z0-9-]{3,20}", plate):
        raise ValueError(
            "Use a registration of 3–20 letters, digits or hyphens, without commas or list numbering."
        )
    return plate


def parse_date(value):
    return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()


def amount(value):
    try:
        number = Decimal(value.replace(",", "").strip())
        if not number.is_finite() or number < 0:
            raise ValueError("Invalid amount")
        return str(number)
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("Invalid portal amount") from exc


def parse_tira(raw, plate):
    if BLOCK.search(raw.get("text", "")):
        return Result("BLOCKED", message="Portal challenge or rate limit.")
    if raw.get("status"):
        fields = raw["fields"]
        if normalize_plate(fields.get("Registration Number", "")) != plate:
            raise ValueError("Returned registration does not match the query.")
        reference = fields.get("Cover Note Reference", "").strip()
        if not reference:
            raise ValueError("Missing cover note reference")
        return Result(
            "FOUND",
            dict(
                reference=reference,
                status=raw["status"].upper(),
                insurer=fields.get("Insurer", ""),
                start_date=parse_date(fields["Start Date"]),
                expiry_date=parse_date(fields["End Date"]),
                reported=fields,
            ),
        )
    if NOT_FOUND.search(raw.get("text", "")):
        return Result("NOT_FOUND", message="Portal returned no matching cover note.")
    return Result("ERROR", message="Unrecognized TIRA response.", transient=True)


def parse_tms(raw, plate, source):
    if BLOCK.search(raw.get("text", "")):
        return Result("BLOCKED", message="Portal challenge or rate limit.")
    popup = raw.get("popup", "")
    plate_pattern = r"(?<![A-Z0-9-])" + r"\s*".join(re.escape(x) for x in plate) + r"(?![A-Z0-9-])"
    matches_popup = re.search(plate_pattern, popup, re.I)
    if source == "offence" and re.search("Hakuna Deni", popup, re.I) and matches_popup:
        return Result("NO_DEBT", {"records": []}, popup.strip())
    if (
        re.search(r"no (?:inspection )?(?:records?|results?) found|hakuna (?:taarifa|ukaguzi)", popup, re.I)
        and matches_popup
    ):
        return Result("NOT_FOUND", message=popup.strip())
    if not any(
        re.sub(r"\s+", "", heading).upper() == "VEHICLE" + plate for heading in raw.get("headings", [])
    ):
        return Result("ERROR", message=popup.strip() or "No matching vehicle result heading.", transient=True)
    records = []
    seen = {}
    for table in raw.get("tables", []):
        headers = table["headers"]
        if len(set(headers)) != len(headers):
            raise ValueError("Duplicate table headers")
        for cells in table["rows"]:
            if len(cells) != len(headers):
                raise ValueError("Incomplete result row")
            row = dict(zip(headers, cells))
            if source == "inspection":
                inspected = parse_date(row["Inspection Date"])
                expires = parse_date(row["Valid Until"])
                if expires < inspected:
                    raise ValueError("Inspection expiry predates inspection")
                reference = inspected + ":" + row.get("Reason", "")
                value = dict(
                    reference=reference,
                    inspection_date=inspected,
                    expiry_date=expires,
                    final_result=row["Final Result"],
                    status=row["Status"],
                    restriction=row["Restriction on Use"],
                    reported=row,
                )
            else:
                reference = row["Reference"].strip()
                if not reference or len(reference) > 100:
                    raise ValueError("Invalid offence reference")
                value = dict(
                    reference=reference,
                    description=row["Offence"],
                    location=row["Location"],
                    issued_date=parse_date(row["Issued Date"]),
                    charge=amount(row["Charge"]),
                    penalty=amount(row["Penalty"]),
                    status=row["Status"].strip().upper(),
                    reported=row,
                )
            if reference in seen and seen[reference] != value:
                raise ValueError("Conflicting rows for the same reference")
            if reference not in seen:
                records.append(value)
                seen[reference] = value
    if not records:
        return Result("ERROR", message="Result table missing or empty.", transient=True)
    data = {"records": records}
    if source == "inspection":
        data["latest"] = max(records, key=lambda record: record["inspection_date"])
    return Result("FOUND", data)


class Collector:
    def __init__(self, settings):
        self.settings = settings

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.playwright = sync_playwright().start()
        try:
            self.browser = self.playwright.chromium.launch(
                headless=True, chromium_sandbox=self.settings.browser_sandbox
            )
            self.context = self.browser.new_context(locale="en-GB", viewport={"width": 1400, "height": 1000})
        except Exception:
            self.playwright.stop()
            raise
        return self

    def __exit__(self, *args):
        self.browser.close()
        self.playwright.stop()

    def lookup(self, plate, source, job_id, attempt=1):
        from playwright.sync_api import TimeoutError as PlaywrightTimeout

        page = self.context.new_page()
        page.set_default_timeout(20000)
        raw = {}
        result = Result("ERROR", message="Lookup did not finish.", transient=True)
        read_js = TIRA_READ if source == "insurance" else TMS_READ
        try:
            url = "https://tiramis.tira.go.tz/#/" if source == "insurance" else "https://tms.tpf.go.tz/"
            response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            status = response.status if response else 200
            if status in (403, 429):
                result = Result("BLOCKED", message=f"Portal returned HTTP {status}.")
            elif status >= 500:
                result = Result("ERROR", message=f"Portal returned HTTP {status}.", transient=True)
            else:
                if source == "insurance":
                    radio = page.locator('input[formcontrolname="paramType"][value="2"]')
                    radio.locator("xpath=..").click()
                    if not radio.is_checked():
                        raise ValueError("Registration Number search could not be selected")
                    page.get_by_placeholder("Enter your search details", exact=True).fill(plate)
                    page.get_by_role("button", name="Verify", exact=True).click()
                    wait_js = TIRA_WAIT
                else:
                    page.get_by_role(
                        "button",
                        name=re.compile("Check Offence" if source == "offence" else "Check Inspection"),
                    ).click()
                    page.get_by_role("button", name=re.compile("Search By Registration")).click()
                    page.get_by_placeholder("e.g. T359DTT", exact=True).fill(plate)
                    page.get_by_role("button", name=re.compile(r"Search$")).click()
                    wait_js = TMS_WAIT
                try:
                    page.wait_for_function(wait_js, timeout=45000)
                except PlaywrightTimeout:
                    pass
                raw = page.evaluate(read_js)
                result = parse_tira(raw, plate) if source == "insurance" else parse_tms(raw, plate, source)
        except (ValueError, KeyError, TypeError) as exc:
            result = Result("ERROR", message=f"Invalid portal data: {exc}")
        except Exception as exc:
            result = Result("ERROR", message=f"{type(exc).__name__}: {str(exc)[:400]}", transient=True)
        try:
            if not raw:
                try:
                    raw = page.evaluate(read_js)
                except Exception:
                    raw = {"text": ""}
            if BLOCK.search(raw.get("text", "")):
                result = Result("BLOCKED", message="Portal challenge or rate limit.")
            relative = Path("evidence") / job_id / f"{plate}_{source}_{attempt}.json"
            path = self.settings.data_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "registration": plate,
                        "source": source,
                        "outcome": result.outcome,
                        "message": result.message,
                        "raw": raw,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            result.evidence = relative.as_posix()
            try:
                page.screenshot(path=str(path.with_suffix(".png")), full_page=True, timeout=10000)
            except Exception:
                result.message += " Screenshot unavailable."
        finally:
            page.close()
        return result
