import copy
import os
from pathlib import Path

import pytest

from fleet.portals import TMS_READ, TMS_WAIT, normalize_plate, parse_tira, parse_tms


def raw_table(source="offence"):
    if source == "offence":
        headers = ["Reference", "Location", "Offence", "Charge", "Penalty", "Status", "Issued Date"]
        rows = [["TEST-1", "KAHAMA", "Defective tires", "30,000.00", "0.00", "PENDING", "24/10/2026"]]
    else:
        headers = ["Inspection Date", "Valid Until", "Reason", "Restriction on Use", "Final Result", "Status"]
        rows = [["24/10/2026", "24/04/2027", "Road Worthiness", "No", "PASS", "VALID"]]
    return {
        "text": "",
        "popup": "",
        "headings": ["VEHICLE T123ABC"],
        "tables": [{"headers": headers, "rows": rows}],
    }


def test_pending_duplicate_references_and_amounts():
    raw = raw_table()
    raw["tables"][0]["rows"] *= 2
    result = parse_tms(raw, "T123ABC", "offence")
    assert len(result.data["records"]) == 1
    assert result.data["records"][0]["charge"] == "30000.00"
    raw["tables"][0]["rows"][1] = [
        "TEST-1",
        "KAHAMA",
        "Defective tires",
        "30001.00",
        "0",
        "PENDING",
        "24/10/2026",
    ]
    with pytest.raises(ValueError, match="Conflicting"):
        parse_tms(raw, "T123ABC", "offence")


def test_mismatched_registration_never_clears():
    assert parse_tms(raw_table(), "T456DEF", "offence").outcome == "ERROR"
    raw = {"text": "", "popup": "Hakuna Deni\nGari lenye namba T123ABCX halidaiwi."}
    assert parse_tms(raw, "T123ABC", "offence").outcome == "ERROR"
    raw["popup"] = "Hakuna Deni\nGari lenye namba T 123 ABC halidaiwi."
    assert parse_tms(raw, "T123ABC", "offence").outcome == "NO_DEBT"


def test_missing_and_challenge():
    raw = {"text": "", "popup": "Hakuna Taarifa za Ukaguzi\nGari lenye namba T123ABC halina taarifa."}
    assert parse_tms(raw, "T123ABC", "inspection").outcome == "NOT_FOUND"
    assert parse_tms({"text": "Too many requests"}, "T123ABC", "offence").outcome == "BLOCKED"
    assert parse_tira({"text": "No cover note found"}, "T123ABC").outcome == "NOT_FOUND"


@pytest.mark.parametrize(
    "source,column,bad", [("offence", 6, "32/10/2026"), ("offence", 3, "NaN"), ("inspection", 0, "bad-date")]
)
def test_bad_values_are_errors(source, column, bad):
    raw = raw_table(source)
    raw["tables"][0]["rows"][0][column] = bad
    with pytest.raises(ValueError):
        parse_tms(raw, "T123ABC", source)


def test_inspection_selects_latest_date_not_table_order():
    raw = raw_table("inspection")
    older = copy.deepcopy(raw["tables"][0]["rows"][0])
    older[0], older[1] = "21/05/2026", "21/11/2026"
    raw["tables"][0]["rows"].append(older)
    result = parse_tms(raw, "T123ABC", "inspection")
    assert result.data["latest"]["expiry_date"] == "2027-04-24"


def test_tira_registration_dates_and_status():
    raw = {
        "text": "",
        "status": "EXPIRED",
        "fields": {
            "Registration Number": "T 123 ABC",
            "Cover Note Reference": "TEST-COVER",
            "Start Date": "22/10/2025",
            "End Date": "21/10/2026",
        },
    }
    result = parse_tira(raw, "T123ABC")
    assert result.data["expiry_date"] == "2026-10-21"
    assert result.data["status"] == "EXPIRED"
    with pytest.raises(ValueError):
        parse_tira(raw, "T456DEF")
    assert normalize_plate(" t 123 abc ") == "T123ABC"
    with pytest.raises(ValueError):
        normalize_plate("1. T123ABC")


@pytest.mark.skipif(
    os.getenv("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1 for real Chromium fixture tests"
)
@pytest.mark.parametrize("source", ["inspection", "offence"])
def test_both_real_dom_table_layouts(source):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content((Path(__file__).parent / "fixtures" / f"tms_{source}.html").read_text())
        assert page.evaluate(TMS_WAIT) is True
        result = parse_tms(page.evaluate(TMS_READ), "T123ABC", source)
        assert result.outcome == "FOUND"
        browser.close()
