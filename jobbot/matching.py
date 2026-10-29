"""Decide which postings are worth applying to: hard filters, keyword score, then Claude's fit assessment."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from . import ats
from .models import Job
from .profile import Profile
from .util import normalize, parse_date, truncate

_NATIONALS_ONLY = re.compile(
    r"(uae|emirati)\s+nationals?\s+only|emiratis?\s+only|only\s+(?:open\s+to\s+|for\s+)?(?:uae\s+nationals|emiratis)"
    r"|open\s+(?:only\s+)?to\s+uae\s+nationals|(?:reserved|exclusively)\s+for\s+(?:uae\s+nationals|emiratis)"
    r"|emiratisation\s+(?:role|position|vacancy)|\bnafis\b",
    re.I,
)
_ARABIC_REQUIRED = re.compile(
    r"arabic[^.\n]{0,60}(?:required|must|essential|mandatory|is a must)|(?:fluent|fluency|native|proficien\w*)"
    r"[^.\n]{0,25}arabic|bilingual[^.\n]{0,25}arabic|arabic[^.\n]{0,10}speak(?:er|ing)",
    re.I,
)
_TITLE_STOPWORDS = {"and", "of", "the", "for", "to", "in", "a", "an", "with", "-", "&", "i", "ii", "iii"}
# Seniority/role-level words: on their own they don't make two titles the same job.
_GENERIC_TITLE_WORDS = {"senior", "sr", "junior", "jr", "lead", "head", "manager", "specialist", "executive",
                        "associate", "officer", "director", "principal", "staff", "global", "regional", "assistant",
                        "chief", "vp", "intern", "team", "leader", "consultant", "coordinator", "supervisor"}


@dataclass
class MatchResult:
    passed: bool
    reason: str = ""
    keyword_score: float = 0.0
    keywords: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _has_term(term: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I) is not None


def location_ok(job: Job, search: dict) -> bool:
    locations = search.get("locations") or []
    haystack = job.location or ""
    if any(_has_term(loc, haystack) for loc in locations):
        return True
    if search.get("allow_remote") and (job.remote or "remote" in haystack.lower()):
        return True
    if not haystack.strip():
        # No structured location: look for the city in the opening of the ad.
        return any(_has_term(loc, job.description[:1500]) for loc in locations)
    return False


def is_emirati(profile: Profile) -> bool:
    nat = str(profile.personal.get("nationality") or "").lower()
    return any(x in nat for x in ("emirati", "uae", "united arab emirates"))


def speaks_arabic(profile: Profile) -> bool:
    return any("arabic" in lang.lower() for lang in profile.languages) or any(
        "arabic" in s.lower() for s in profile.all_skills())


def apply_filters(job: Job, profile: Profile, search: dict, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    title = job.title or ""
    if not location_ok(job, search):
        return False, f"location '{job.location}' is not in the UAE"
    for company in search.get("exclude_companies") or []:
        if normalize(company) and normalize(company) == normalize(job.company):
            return False, f"company {job.company} is excluded"
    for term in search.get("title_exclude") or []:
        if _has_term(term, title):
            return False, f"title contains excluded term '{term}'"
    include = search.get("title_include") or []
    if include and not any(_has_term(t, title) for t in include):
        return False, "title matches none of title_include"
    posted = parse_date(job.posted_at)
    max_age = search.get("max_age_days")
    if posted and max_age and posted < now - timedelta(days=int(max_age)):
        return False, f"posted {posted.date()} (older than {max_age} days)"
    if search.get("skip_nationals_only", True) and not is_emirati(profile):
        if _NATIONALS_ONLY.search(f"{title}\n{job.description}"):
            return False, "role is reserved for UAE nationals"
    return True, ""


def title_similarity(title: str, targets: list[str]) -> float:
    """0-100: best share of a target title's words found in the job title.

    Generic level words ("Senior", "Manager") count only once a core word ("Analytics") matches."""
    words = set(normalize(title).split()) - _TITLE_STOPWORDS
    best = 0.0
    for target in targets:
        tw = set(normalize(target).split()) - _TITLE_STOPWORDS
        core = tw - _GENERIC_TITLE_WORDS
        if not tw or (core and not words & core):
            continue
        best = max(best, len(words & tw) / len(tw))
    return round(100 * best, 1)


def score_job(job: Job, profile: Profile, search: dict) -> MatchResult:
    ok, reason = apply_filters(job, profile, search)
    if not ok:
        return MatchResult(False, reason)
    return keyword_match(job, profile, search)


def keyword_match(job: Job, profile: Profile, search: dict) -> MatchResult:
    """Keyword/title score without the hard filters (0-100)."""
    keywords = ats.extract_keywords(f"{job.title}\n{job.description}", profile.all_skills(), title=job.title)
    cov, _, _ = ats.coverage(profile.all_text(), keywords)
    targets = list(search.get("keywords") or []) + [profile.headline]
    title_score = title_similarity(job.title, targets)
    score = 0.6 * cov + 0.4 * title_score if len(keywords) >= 3 else title_score
    if title_score == 0:
        score *= 0.5  # skills overlap alone, in a differently titled job, is a weak signal
    flags = []
    if _ARABIC_REQUIRED.search(job.description) and not speaks_arabic(profile):
        flags.append("Arabic appears to be required")
        score *= 0.7
    return MatchResult(True, "", round(score, 1), keywords, flags)


# ---- Claude fit assessment ------------------------------------------------

class FitAssessment(BaseModel):
    score: int = Field(description="0-100 likelihood the candidate is a strong, credible applicant for this role")
    summary: str = Field(description="One or two sentences explaining the score")
    strengths: list[str] = Field(description="Requirements the candidate clearly meets, with evidence")
    gaps: list[str] = Field(description="Requirements the candidate does not demonstrably meet")
    dealbreakers: list[str] = Field(description="Hard blockers, e.g. nationals-only, mandatory licence or language the candidate lacks, far too senior/junior")


FIT_SYSTEM = """You screen job postings in the United Arab Emirates for one candidate, whose master CV follows.
Judge fit the way an experienced UAE recruiter would: role scope and seniority, must-have skills, domain,
required licences or certifications (e.g. DHA/DOH/MOH, RERA, SCA), language requirements (Arabic), and
restrictions such as roles reserved for UAE nationals. Be calibrated: 80+ means a recruiter would very likely
shortlist the candidate; 50-70 is a stretch; below 50 is a poor fit. Never assume experience that the CV does not show."""


def profile_context(profile: Profile) -> str:
    import yaml

    data = {
        "headline": profile.headline, "summary": profile.summary,
        "years_of_experience": profile.years_of_experience(),
        "location": profile.personal.get("location"), "nationality": profile.personal.get("nationality"),
        "visa_status": profile.personal.get("visa_status"), "languages": profile.languages,
        "skills": profile.skills,
        "experience": [
            {"id": f"e{i}", "title": e.title, "company": e.company, "location": e.location,
             "start": e.start, "end": e.end, **({"type": "academic (degree research/project)"} if e.academic else {}),
             "bullets": [{"id": f"e{i}b{j}", "text": b} for j, b in enumerate(e.bullets)]}
            for i, e in enumerate(profile.experience)
        ],
        "education": profile.education, "certifications": profile.certifications, "projects": profile.projects,
    }
    return "<master_cv>\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "</master_cv>"


def job_context(job: Job) -> str:
    return (f"<job>\nTitle: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
            f"Salary: {job.salary or 'not stated'}\n\n{truncate(job.description, 20000)}\n</job>")


def assess_fit(llm, profile: Profile, job: Job) -> FitAssessment:
    return llm.parse(
        system=FIT_SYSTEM, cached_context=profile_context(profile),
        prompt=job_context(job) + "\n\nAssess the candidate's fit for this job.",
        schema=FitAssessment, max_tokens=8000,
    )
