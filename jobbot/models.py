"""Core data types."""

from __future__ import annotations

from dataclasses import dataclass, field

from .util import normalize, short_hash


@dataclass
class Job:
    source: str
    external_id: str
    title: str
    company: str
    location: str = ""
    url: str = ""
    apply_url: str = ""
    description: str = ""
    posted_at: str | None = None  # ISO 8601, UTC
    remote: bool = False
    salary: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return short_hash(self.source, str(self.external_id))

    @property
    def dedupe_key(self) -> str:
        """Same company + same title = same role, even when found via different sources."""
        return short_hash(normalize(self.company), normalize(self.title))


@dataclass
class TailoredExperience:
    company: str
    title: str
    location: str
    start: str
    end: str
    bullets: list[str]


@dataclass
class TailoredCV:
    headline: str
    summary: str
    skills: dict[str, list[str]]
    experience: list[TailoredExperience]
    keywords_targeted: list[str] = field(default_factory=list)
    missing_keywords: list[str] = field(default_factory=list)
    cover_letter: str = ""
    warnings: list[str] = field(default_factory=list)
    method: str = "keyword"  # "llm" or "keyword"


@dataclass
class ApplyResult:
    status: str  # "applied" | "needs_manual" | "failed" | "skipped"
    channel: str
    detail: str = ""
    screenshot: str | None = None
