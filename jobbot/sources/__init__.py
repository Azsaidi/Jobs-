from __future__ import annotations

import logging

from ..models import Job
from .aggregators import JSearchSource, RssSource, SerpApiSource
from .ats_boards import AshbySource, GreenhouseSource, LeverSource, WorkableSource
from .base import Source, SourceError

log = logging.getLogger(__name__)

SOURCES: dict[str, type[Source]] = {
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "workable": WorkableSource,
    "jsearch": JSearchSource,
    "serpapi": SerpApiSource,
    "rss": RssSource,
}


def fetch_all(cfg: dict) -> tuple[list[Job], list[str]]:
    """Run every enabled source. Returns (jobs, error messages); a failing source never aborts the run."""
    jobs: list[Job] = []
    errors: list[str] = []
    for name, source_cfg in (cfg.get("sources") or {}).items():
        if not (source_cfg or {}).get("enabled"):
            continue
        cls = SOURCES.get(name)
        if cls is None:
            errors.append(f"unknown source '{name}'")
            continue
        source = cls(source_cfg, cfg.get("search") or {})
        try:
            found = source.fetch()
            log.info("%s: %d postings", name, len(found))
            jobs.extend(found)
        except SourceError as exc:
            errors.append(str(exc))
        errors.extend(getattr(source, "errors", []))
    return jobs, errors


__all__ = ["SOURCES", "Source", "SourceError", "fetch_all"]
