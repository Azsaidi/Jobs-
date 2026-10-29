"""Search aggregators: broad coverage of UAE postings (LinkedIn, Bayt, Indeed, GulfTalent,
company sites...) via Google for Jobs indexes, plus generic RSS feeds.

These need an API key (both have free tiers) and only return job *listings* - jobbot
never logs into or scrapes LinkedIn/Bayt directly.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from ..config import Config
from ..models import Job
from ..util import html_to_text, parse_date
from .base import Source, SourceError

log = logging.getLogger(__name__)


def _queries(search: dict) -> list[str]:
    queries = [q for q in search.get("keywords") or [] if q]
    if not queries:
        raise SourceError("search.keywords is empty - add the job titles you are looking for")
    return queries


class JSearchSource(Source):
    """JSearch on RapidAPI (Google for Jobs). Env: RAPIDAPI_KEY."""

    name = "jsearch"
    host = "jsearch.p.rapidapi.com"

    def fetch(self) -> list[Job]:
        key = Config.env("RAPIDAPI_KEY")
        if not key:
            raise SourceError("jsearch: RAPIDAPI_KEY is not set")
        headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": self.host}
        jobs = []
        for q in _queries(self.search):
            data = self.get_json(f"https://{self.host}/search", headers=headers, params={
                "query": f"{q} in United Arab Emirates", "country": "ae", "page": 1,
                "num_pages": int(self.cfg.get("pages", 1)), "date_posted": self.cfg.get("date_posted", "week"),
            })
            jobs += [self.parse(j) for j in data.get("data") or []]
        return jobs

    @staticmethod
    def parse(j: dict) -> Job:
        location = ", ".join(x for x in (j.get("job_city"), j.get("job_state"), j.get("job_country")) if x)
        options = j.get("apply_options") or []
        direct = next((o["apply_link"] for o in options if o.get("is_direct") and o.get("apply_link")), None)
        apply_url = direct or j.get("job_apply_link") or ""
        salary = ""
        if j.get("job_min_salary") or j.get("job_max_salary"):
            salary = f"{j.get('job_salary_currency') or ''} {j.get('job_min_salary') or ''}-{j.get('job_max_salary') or ''} {j.get('job_salary_period') or ''}".strip()
        return Job(
            source="jsearch", external_id=str(j.get("job_id")), title=(j.get("job_title") or "").strip(),
            company=j.get("employer_name") or "Unknown", location=location,
            url=j.get("job_google_link") or apply_url, apply_url=apply_url,
            description=j.get("job_description") or "", posted_at=_iso(j.get("job_posted_at_datetime_utc")),
            remote=bool(j.get("job_is_remote")), salary=salary,
            extra={"publisher": j.get("job_publisher"), "apply_options": options[:6]},
        )


class SerpApiSource(Source):
    """SerpApi Google Jobs engine. Env: SERPAPI_KEY."""

    name = "serpapi"

    def fetch(self) -> list[Job]:
        key = Config.env("SERPAPI_KEY")
        if not key:
            raise SourceError("serpapi: SERPAPI_KEY is not set")
        jobs = []
        for q in _queries(self.search):
            params = {"engine": "google_jobs", "q": q, "location": self.cfg.get("location", "United Arab Emirates"),
                      "gl": "ae", "hl": "en", "api_key": key}
            for _ in range(int(self.cfg.get("pages", 1))):
                data = self.get_json("https://serpapi.com/search.json", params=params)
                jobs += [self.parse(j) for j in data.get("jobs_results") or []]
                token = (data.get("serpapi_pagination") or {}).get("next_page_token")
                if not token:
                    break
                params["next_page_token"] = token
        return jobs

    @staticmethod
    def parse(j: dict) -> Job:
        ext = j.get("detected_extensions") or {}
        options = j.get("apply_options") or []
        apply_url = options[0]["link"] if options else j.get("share_link", "")
        return Job(
            source="serpapi", external_id=str(j.get("job_id"))[:200], title=(j.get("title") or "").strip(),
            company=j.get("company_name") or "Unknown", location=j.get("location") or "",
            url=j.get("share_link") or apply_url, apply_url=apply_url, description=j.get("description") or "",
            posted_at=_relative_to_iso(ext.get("posted_at")), remote=bool(ext.get("work_from_home")),
            salary=ext.get("salary") or "", extra={"via": j.get("via"), "apply_options": options[:6]},
        )


class RssSource(Source):
    """Any RSS/Atom job feed. Config: feeds: [{url: ..., company: optional}]."""

    name = "rss"

    def fetch(self) -> list[Job]:
        jobs = []
        for feed in self.cfg.get("feeds") or []:
            feed = {"url": feed} if isinstance(feed, str) else feed
            try:
                resp = self.session.get(feed["url"], timeout=30)
                resp.raise_for_status()
                jobs += self.parse(resp.content, feed.get("company"))
            except Exception as exc:  # noqa: BLE001 - one broken feed must not stop the run
                log.warning("rss feed %s failed: %s", feed.get("url"), exc)
        return jobs

    @staticmethod
    def parse(content: bytes, company: str | None = None) -> list[Job]:
        root = ET.fromstring(content)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = root.findall(".//item") or root.findall(".//a:entry", ns)
        jobs = []
        for it in items:
            def text(tag: str) -> str:
                el = it.find(tag) if not tag.startswith("a:") else it.find(tag, ns)
                return (el.text or "").strip() if el is not None and el.text else ""

            title = text("title") or text("a:title")
            link = text("link")
            if not link:
                el = it.find("a:link", ns)
                link = el.get("href", "") if el is not None else ""
            comp = company or ""
            m = re.match(r"(.+?)\s+(?:at|@|-|–)\s+(.+)$", title)
            if not comp and m:
                title, comp = m.group(1), m.group(2)
            jobs.append(Job(
                source="rss", external_id=text("guid") or link, title=title, company=comp or "Unknown",
                location=text("location"), url=link, apply_url=link,
                description=html_to_text(text("description") or text("a:summary") or text("a:content")),
                posted_at=_iso(text("pubDate") or text("a:updated")),
            ))
        return jobs


def _iso(value) -> str | None:
    dt = parse_date(value)
    return dt.isoformat() if dt else None


def _relative_to_iso(text: str | None) -> str | None:
    """'3 days ago' / '20 hours ago' / '1 month ago' -> ISO timestamp."""
    if not text:
        return None
    m = re.search(r"(\d+)\+?\s*(minute|hour|day|week|month)", text)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    delta = {"minute": timedelta(minutes=n), "hour": timedelta(hours=n), "day": timedelta(days=n),
             "week": timedelta(weeks=n), "month": timedelta(days=30 * n)}[unit]
    return (datetime.now(timezone.utc) - delta).replace(microsecond=0).isoformat()
