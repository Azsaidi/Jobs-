"""Public job-board APIs of the common applicant tracking systems.

These list every opening a company has published; location filtering to the UAE
happens later in the matching step. Each board is fetched independently so one
bad company name doesn't stop the rest.
"""

from __future__ import annotations

import logging

from ..models import Job
from ..util import html_to_text, parse_date
from .base import Source, SourceError

log = logging.getLogger(__name__)


def _iso(value) -> str | None:
    dt = parse_date(value)
    return dt.isoformat() if dt else None


class _MultiBoardSource(Source):
    list_key = ""

    def fetch(self) -> list[Job]:
        jobs: list[Job] = []
        self.errors: list[str] = []
        for board in self.cfg.get(self.list_key) or []:
            try:
                jobs.extend(self.fetch_board(str(board)))
            except SourceError as exc:
                log.warning("%s", exc)
                self.errors.append(str(exc))
        return jobs

    def fetch_board(self, board: str) -> list[Job]:
        raise NotImplementedError


class GreenhouseSource(_MultiBoardSource):
    name = "greenhouse"
    list_key = "boards"

    def fetch_board(self, board: str) -> list[Job]:
        data = self.get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
        return [self.parse(board, j) for j in data.get("jobs", [])]

    @staticmethod
    def parse(board: str, j: dict) -> Job:
        location = (j.get("location") or {}).get("name") or ""
        offices = ", ".join(o.get("location") or o.get("name") or "" for o in j.get("offices") or [])
        if offices and offices.lower() not in location.lower():
            location = f"{location} ({offices})" if location else offices
        url = j.get("absolute_url") or ""
        return Job(
            source="greenhouse", external_id=f"{board}:{j['id']}", title=j.get("title", "").strip(),
            company=j.get("company_name") or board, location=location, url=url, apply_url=url,
            description=html_to_text(j.get("content")), posted_at=_iso(j.get("first_published") or j.get("updated_at")),
            remote="remote" in location.lower(), extra={"board": board, "ats": "greenhouse"},
        )


class LeverSource(_MultiBoardSource):
    name = "lever"
    list_key = "sites"

    def fetch_board(self, site: str) -> list[Job]:
        data = self.get_json(f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"})
        return [self.parse(site, j) for j in data]

    @staticmethod
    def parse(site: str, j: dict) -> Job:
        cats = j.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location") or ""]
        parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
        for lst in j.get("lists") or []:
            parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content'))}")
        parts.append(j.get("additionalPlain") or "")
        return Job(
            source="lever", external_id=f"{site}:{j['id']}", title=j.get("text", "").strip(), company=site,
            location=", ".join(x for x in locations if x), url=j.get("hostedUrl", ""), apply_url=j.get("applyUrl", ""),
            description="\n\n".join(p for p in parts if p).strip(), posted_at=_iso(j.get("createdAt")),
            remote=(j.get("workplaceType") == "remote"), salary=_lever_salary(j.get("salaryRange")),
            extra={"site": site, "ats": "lever", "commitment": cats.get("commitment")},
        )


def _lever_salary(rng: dict | None) -> str:
    if not rng:
        return ""
    return f"{rng.get('currency', '')} {rng.get('min', '')}-{rng.get('max', '')} {rng.get('interval', '')}".strip()


class AshbySource(_MultiBoardSource):
    name = "ashby"
    list_key = "orgs"

    def fetch_board(self, org: str) -> list[Job]:
        data = self.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}",
                             params={"includeCompensation": "true"})
        return [self.parse(org, j) for j in data.get("jobs", []) if j.get("isListed", True)]

    @staticmethod
    def parse(org: str, j: dict) -> Job:
        locations = [j.get("location") or ""] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
        comp = (j.get("compensation") or {}).get("compensationTierSummary") or ""
        return Job(
            source="ashby", external_id=f"{org}:{j['id']}", title=j.get("title", "").strip(), company=org,
            location=", ".join(x for x in locations if x), url=j.get("jobUrl", ""), apply_url=j.get("applyUrl", ""),
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            posted_at=_iso(j.get("publishedAt")), remote=bool(j.get("isRemote")), salary=comp,
            extra={"org": org, "ats": "ashby", "employment_type": j.get("employmentType")},
        )


class WorkableSource(_MultiBoardSource):
    """Workable's public careers-widget feed. Workable rate-limits aggressively; keep the account list short."""

    name = "workable"
    list_key = "accounts"

    def fetch_board(self, account: str) -> list[Job]:
        data = self.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{account}", params={"details": "true"})
        company = data.get("name") or account
        return [self.parse(account, company, j) for j in data.get("jobs", [])]

    @staticmethod
    def parse(account: str, company: str, j: dict) -> Job:
        locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
        location = "; ".join(", ".join(x for x in (loc.get("city"), loc.get("country")) if x) for loc in locs)
        url = j.get("url") or j.get("shortlink") or ""
        return Job(
            source="workable", external_id=f"{account}:{j.get('shortcode') or j.get('id')}",
            title=(j.get("title") or "").strip(), company=company, location=location, url=url,
            apply_url=j.get("application_url") or url, description=html_to_text(j.get("description")),
            posted_at=_iso(j.get("published_on") or j.get("created_at")), remote=bool(j.get("telecommuting")),
            extra={"account": account, "ats": "workable"},
        )
