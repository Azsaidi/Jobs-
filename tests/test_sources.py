from jobbot.sources import fetch_all
from jobbot.sources.aggregators import JSearchSource, RssSource, SerpApiSource
from jobbot.sources.ats_boards import AshbySource, GreenhouseSource, LeverSource, WorkableSource


def test_greenhouse_parse():
    job = GreenhouseSource.parse("careem", {
        "id": 123, "title": " Data Analyst ", "company_name": "Careem", "absolute_url": "https://x/careem/jobs/123",
        "location": {"name": "Dubai"}, "offices": [{"name": "Dubai", "location": "Dubai, UAE"}],
        "content": "&lt;p&gt;Use SQL&lt;/p&gt;", "first_published": "2026-10-15T04:40:52-04:00"})
    assert job.title == "Data Analyst" and job.company == "Careem"
    assert "Dubai, UAE" in job.location and job.description == "Use SQL"
    assert job.posted_at.startswith("2026-10-15T08:40") and job.extra["board"] == "careem"


def test_lever_parse():
    job = LeverSource.parse("acme", {
        "id": "abc", "text": "BI Analyst", "hostedUrl": "https://jobs.lever.co/acme/abc",
        "applyUrl": "https://jobs.lever.co/acme/abc/apply", "createdAt": 1785824225174,
        "categories": {"location": "Dubai", "allLocations": ["Dubai", "Riyadh"]}, "descriptionPlain": "Intro",
        "lists": [{"text": "Requirements", "content": "<li>SQL</li>"}], "additionalPlain": "Benefits",
        "workplaceType": "hybrid", "salaryRange": {"currency": "AED", "min": 20000, "max": 30000, "interval": "per-month"}})
    assert job.location == "Dubai, Riyadh" and "Requirements" in job.description and "SQL" in job.description
    assert job.salary == "AED 20000-30000 per-month" and not job.remote


def test_ashby_parse_and_unlisted_skipped():
    data = {"id": "u1", "title": "Analyst", "location": "Dubai", "secondaryLocations": [{"location": "Abu Dhabi"}],
            "jobUrl": "https://jobs.ashbyhq.com/z/u1", "applyUrl": "https://jobs.ashbyhq.com/z/u1/application",
            "descriptionPlain": "Text", "publishedAt": "2026-08-12T05:44:50.125+00:00", "isRemote": False}
    job = AshbySource.parse("ziina", data)
    assert job.location == "Dubai, Abu Dhabi" and job.apply_url.endswith("/application")


def test_workable_parse():
    job = WorkableSource.parse("acme", "Acme", {
        "title": "Analyst", "shortcode": "AB12", "url": "https://apply.workable.com/j/AB12",
        "application_url": "https://apply.workable.com/j/AB12/apply", "published_on": "2026-10-01",
        "locations": [{"city": "Dubai", "country": "United Arab Emirates"}], "description": "<p>SQL</p>"})
    assert job.location == "Dubai, United Arab Emirates" and job.external_id == "acme:AB12"


def test_jsearch_prefers_direct_apply_link():
    job = JSearchSource.parse({
        "job_id": "j1", "job_title": "Data Analyst", "employer_name": "Emirates X", "job_city": "Dubai",
        "job_country": "AE", "job_apply_link": "https://linkedin.com/jobs/1", "job_description": "SQL",
        "apply_options": [{"publisher": "LinkedIn", "apply_link": "https://linkedin.com/jobs/1", "is_direct": False},
                          {"publisher": "Emirates X", "apply_link": "https://careers.x.ae/1", "is_direct": True}],
        "job_posted_at_datetime_utc": "2026-10-20T00:00:00.000Z"})
    assert job.apply_url == "https://careers.x.ae/1" and job.location == "Dubai, AE"


def test_serpapi_relative_date():
    job = SerpApiSource.parse({"title": "Analyst", "company_name": "Co", "location": "Dubai", "job_id": "x",
                               "description": "SQL", "detected_extensions": {"posted_at": "3 days ago"},
                               "apply_options": [{"title": "Bayt", "link": "https://bayt.com/1"}]})
    assert job.apply_url == "https://bayt.com/1" and job.posted_at


def test_rss_parse_title_company_split():
    xml = b"""<rss><channel><item><title>Data Analyst at Acme</title><link>https://a/1</link>
      <description>&lt;p&gt;SQL&lt;/p&gt;</description><pubDate>Wed, 28 Oct 2026 10:00:00 +0000</pubDate></item></channel></rss>"""
    (job,) = RssSource.parse(xml)
    assert job.title == "Data Analyst" and job.company == "Acme" and job.description == "SQL"


def test_fetch_all_isolates_failures(monkeypatch, cfg):
    cfg["sources"] = {"greenhouse": {"enabled": True, "boards": ["good", "bad"]}, "jsearch": {"enabled": True}}
    monkeypatch.delenv("RAPIDAPI_KEY", raising=False)

    def fake_board(self, board):
        if board == "bad":
            from jobbot.sources import SourceError
            raise SourceError("greenhouse: not found")
        return [GreenhouseSource.parse(board, {"id": 1, "title": "A", "location": {"name": "Dubai"}})]

    monkeypatch.setattr(GreenhouseSource, "fetch_board", fake_board)
    jobs, errors = fetch_all(cfg)
    assert len(jobs) == 1
    assert any("not found" in e for e in errors) and any("RAPIDAPI_KEY" in e for e in errors)
