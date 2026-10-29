from pathlib import Path

import pytest

from jobbot.apply.browser_apply import BrowserApplier, FormFiller, application_url, find_chromium, match_option
from jobbot.models import Job

FIXTURE = Path(__file__).parent / "fixtures" / "form.html"


def test_application_url_variants():
    gh = Job("greenhouse", "b:1", "T", "C", url="https://job-boards.eu.greenhouse.io/bybit/jobs/4975178101",
             apply_url="https://job-boards.eu.greenhouse.io/bybit/jobs/4975178101", extra={"board": "bybit"})
    assert application_url(gh) == "https://job-boards.eu.greenhouse.io/embed/job_app?for=bybit&token=4975178101"
    lever = Job("lever", "x", "T", "C", url="https://jobs.lever.co/acme/abc")
    assert application_url(lever) == "https://jobs.lever.co/acme/abc/apply"


def test_form_filler_answers(profile):
    f = FormFiller(profile, {}, "letter")
    assert f.text_value("First Name") == "Alex" and f.text_value("Last Name") == "Example"
    assert f.text_value("Company name") is None or f.text_value("Current company") == "Example Retail Group"
    assert f.text_value("Do you require visa sponsorship?") == "No"   # sponsor rule wins over visa rule
    assert f.text_value("Country") == "United Arab Emirates"
    assert f.text_value("What is your favourite colour?") is None
    assert f.choose_option("Gender", ["Select...", "Male", "Female", "Decline to self-identify"]) == "Decline to self-identify"
    assert match_option("Bachelor's Degree", ["High school", "Bachelor’s Degree"]) == "Bachelor’s Degree"
    assert match_option("United Arab Emirates", ["United Arab Emirates +971"]) == "United Arab Emirates +971"


def _have_browser():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            kwargs = {"executable_path": find_chromium()} if find_chromium() else {}
            p.chromium.launch(**kwargs).close()
        return True
    except Exception:
        return False


browser = pytest.mark.skipif(not _have_browser(), reason="playwright/chromium not available")


def _fill(tmp_path, profile, html, submit):
    from playwright.sync_api import sync_playwright

    page_file = tmp_path / "form.html"
    page_file.write_text(html)
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 test")
    applier = BrowserApplier({"timeout_seconds": 10})
    with sync_playwright() as p:
        b = applier._launch(p, True)
        page = b.new_page()
        page.set_default_timeout(10000)
        result = applier.fill_and_submit(page, page_file.as_uri(), profile, {"cv_pdf": cv}, "letter", submit,
                                         tmp_path / "shot_form.png")
        b.close()
    return result


@browser
def test_browser_fills_and_submits_complete_form(tmp_path, profile):
    result = _fill(tmp_path, profile, FIXTURE.read_text(), submit=True)
    assert result.status == "applied", result.detail
    assert Path(result.screenshot).exists()


@browser
def test_browser_stops_on_unknown_required_question(tmp_path, profile):
    html = FIXTURE.read_text().replace(
        "<!--EXTRA-->", '<label for="x">Describe your favourite SQL window function *</label><textarea id="x" required></textarea>')
    result = _fill(tmp_path, profile, html, submit=True)
    assert result.status == "needs_manual" and "favourite SQL window function" in result.detail


@browser
def test_browser_does_not_submit_when_told_not_to(tmp_path, profile):
    result = _fill(tmp_path, profile, FIXTURE.read_text(), submit=False)
    assert result.status == "needs_manual" and "not submitted" in result.detail
