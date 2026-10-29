from datetime import datetime, timedelta, timezone

from conftest import make_job

from jobbot.config import DEFAULTS
from jobbot.matching import apply_filters, score_job, title_similarity

SEARCH = {**DEFAULTS["search"], "keywords": ["Data Analyst"]}


def test_uae_location_passes_and_others_fail(profile):
    assert apply_filters(make_job(location="Abu Dhabi"), profile, SEARCH)[0]
    assert apply_filters(make_job(location="Remote - UAE"), profile, SEARCH)[0]
    ok, reason = apply_filters(make_job(location="Amman, Jordan"), profile, SEARCH)
    assert not ok and "not in the UAE" in reason


def test_remote_only_when_allowed(profile):
    job = make_job(location="Remote", remote=True)
    assert not apply_filters(job, profile, SEARCH)[0]
    assert apply_filters(job, profile, {**SEARCH, "allow_remote": True})[0]


def test_empty_location_falls_back_to_description(profile):
    assert apply_filters(make_job(location="", description="Based in our Dubai office."), profile, SEARCH)[0]


def test_title_exclude_and_include(profile):
    assert not apply_filters(make_job(title="Data Analyst Intern"), profile, SEARCH)[0]
    assert not apply_filters(make_job(title="Sales Manager"), profile, {**SEARCH, "title_include": ["analyst"]})[0]


def test_old_postings_are_skipped(profile):
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    ok, reason = apply_filters(make_job(posted_at=old), profile, SEARCH)
    assert not ok and "older than" in reason


def test_nationals_only_roles(profile):
    job = make_job(description="This position is open to UAE Nationals only.")
    assert not apply_filters(job, profile, SEARCH)[0]
    profile.personal["nationality"] = "Emirati"
    assert apply_filters(job, profile, SEARCH)[0]


def test_excluded_company(profile):
    assert not apply_filters(make_job(company="ACME payments"), profile,
                             {**SEARCH, "exclude_companies": ["Acme Payments"]})[0]


def test_title_similarity_ignores_generic_words():
    assert title_similarity("Senior Data Analyst", ["Data Analyst"]) == 100
    assert title_similarity("Creator Partnerships Manager", ["Analytics Manager"]) == 0
    assert title_similarity("Analytics Manager, Growth", ["Analytics Manager"]) == 100


def test_score_job_rewards_fit_and_flags_arabic(profile, job):
    good = score_job(job, profile, SEARCH)
    assert good.passed and good.keyword_score > 60
    assert "SQL" in good.keywords
    profile.languages = ["English (Native)"]
    arabic = score_job(make_job(description=job.description + "\nFluent Arabic is required."), profile, SEARCH)
    assert arabic.flags and arabic.keyword_score < good.keyword_score
    unrelated = score_job(make_job(title="Chef de Partie", description="Cook food in a busy kitchen."),
                          profile, SEARCH)
    assert unrelated.keyword_score < 30
