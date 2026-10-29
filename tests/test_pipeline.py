import pytest
from conftest import JD, make_job

from jobbot import mailer
from jobbot.apply.email_apply import compose, find_application_emails, subject_reference
from jobbot.db import Tracker
from jobbot.notify import Notifier, RunSummary, digest_text
from jobbot.pipeline import Pipeline
from jobbot.report import build_dashboard

EMAIL_JD = JD + "\nInterested candidates should send their CV to careers@acme-payments.ae quoting Ref: DA-2026-07."


def jobs_fixture():
    return [
        make_job(external_id="1"),
        make_job(external_id="2", source="other"),                                   # same role, other source
        make_job(external_id="3", title="Senior Data Analyst, Risk", company="Beta Bank", description=EMAIL_JD),
        make_job(external_id="4", title="Data Analyst", company="Gamma", location="London, UK"),
        make_job(external_id="5", title="Head Chef", company="Delta Hotels", description="Run the kitchen."),
    ]


@pytest.fixture
def tracker(tmp_path):
    return Tracker(tmp_path / "t.db")


class RecordingNotifier(Notifier):
    def __init__(self):
        super().__init__({"notify": {}})
        self.sent = []

    def send_digest(self, summary):
        self.sent.append(summary)
        return []


def run(cfg, profile, tracker, jobs=None, notifier=None):
    p = Pipeline(cfg, profile, tracker, llm=None, notifier=notifier, fetcher=lambda c: (jobs or jobs_fixture(), []))
    return p, p.run()


def test_review_mode_prepares_and_dedupes(cfg, profile, tracker):
    notifier = RecordingNotifier()
    _, summary = run(cfg, profile, tracker, notifier=notifier)
    assert summary.fetched == 5 and summary.new == 4 and summary.duplicates == 1
    titles = sorted(a["title"] for a in summary.prepared)
    assert titles == ["Senior Data Analyst", "Senior Data Analyst, Risk"]
    assert summary.applied == [] and notifier.sent
    app = tracker.applications()[0]
    assert app["status"] == "prepared" and app["cv_pdf"].endswith("_CV.pdf") and app["ats_after"] > 0
    # second run: nothing new
    _, again = run(cfg, profile, tracker)
    assert again.new == 0 and again.prepared == []


def test_auto_mode_applies_by_email_within_daily_limit(cfg, profile, tracker, monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body, **kw: sent.append((to, subject, kw)) or "id")
    cfg["apply"].update(mode="auto", min_score_to_auto_apply=0, daily_limit=5, channels=["email"])
    _, summary = run(cfg, profile, tracker)
    assert [a["company"] for a in summary.applied] == ["Beta Bank"]
    to, subject, kw = sent[0]
    assert to == "careers@acme-payments.ae" and "(Ref: DA-2026-07)" in subject
    assert [p.suffix for p in kw["attachments"]] == [".pdf", ".pdf"]   # CV + cover letter
    assert kw["reply_to"] == profile.personal["email"]
    # the posting without an email address can't be auto-submitted by email -> left for the user
    assert [a["company"] for a in summary.needs_manual] == ["Acme Payments"]
    assert tracker.applied_since(24) == 1


def test_daily_limit_stops_auto_apply(cfg, profile, tracker, monkeypatch):
    monkeypatch.setattr(mailer, "smtp_configured", lambda: True)
    monkeypatch.setattr(mailer, "send_email", lambda *a, **k: "id")
    cfg["apply"].update(mode="auto", min_score_to_auto_apply=0, daily_limit=0, channels=["email"])
    _, summary = run(cfg, profile, tracker)
    assert summary.applied == [] and len(summary.prepared) == 2


def test_manual_status_updates_and_report(cfg, profile, tracker, tmp_path):
    run(cfg, profile, tracker)
    app_id = tracker.applications()[0]["id"]
    tracker.update_application(app_id, status="interview")
    tracker.log(app_id, "note", "phone screen Tuesday")
    assert [e["kind"] for e in tracker.events(app_id)][-2:] == ["status", "note"]
    with pytest.raises(ValueError):
        tracker.update_application(app_id, status="hired-maybe")
    html = build_dashboard(tracker, tmp_path / "d.html").read_text()
    assert "Senior Data Analyst" in html and "interview" in html


def test_email_detection():
    text = ("Apply now! Send your CV to jobs@acme.ae. For privacy questions contact privacy@acme.ae. "
            "Do not reply to noreply@acme.ae.")
    assert find_application_emails(text) == ["jobs@acme.ae"]
    assert find_application_emails("Our office email is hello@acme.ae") == ["hello@acme.ae"]
    assert find_application_emails("Visit www.acme.ae") == []
    assert subject_reference("Please quote reference no. HR/2026/114 in the subject") == "HR/2026/114"
    assert subject_reference("Our reference customers include...") is None


def test_compose_email(profile):
    subject, body = compose(make_job(), profile, "Dear Hiring Team,\n\nHello.\n\nKind regards,\nAlex Example")
    assert subject == "Application for Senior Data Analyst - Alex Example"
    assert profile.personal["phone"] in body


def test_digest_text():
    s = RunSummary(fetched=10, new=3, prepared=[{"id": 1, "title": "Analyst", "company": "Co", "llm_score": 81,
                                                   "match_summary": "Strong SQL fit", "url": "https://x"}])
    text = digest_text(s)
    assert text.startswith("jobbot: 1 to review") and "#1 Analyst - Co (fit 81)" in text and "jobbot apply" in text
