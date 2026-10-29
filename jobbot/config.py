"""Configuration loading: config.yaml merged over defaults, secrets from the environment / .env."""

from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

from .util import load_dotenv

UAE_LOCATIONS = [
    "United Arab Emirates", "UAE", "U.A.E", "Dubai", "Abu Dhabi", "Sharjah", "Ajman",
    "Ras Al Khaimah", "Fujairah", "Umm Al Quwain", "Al Ain", "DIFC", "ADGM",
]

DEFAULTS: dict = {
    "search": {
        "keywords": [],  # target job titles, also used as aggregator search queries
        "title_include": [],  # if set, a title must contain at least one of these
        "title_exclude": ["intern", "internship"],
        "locations": UAE_LOCATIONS,
        "allow_remote": False,  # accept "Remote" roles that don't mention the UAE
        "max_age_days": 30,
        "exclude_companies": [],
        "skip_nationals_only": True,  # skip Emiratisation roles unless your nationality is Emirati
    },
    "sources": {
        "greenhouse": {"enabled": True, "boards": []},
        "lever": {"enabled": False, "sites": []},
        "ashby": {"enabled": True, "orgs": []},
        "workable": {"enabled": False, "accounts": []},
        "jsearch": {"enabled": False, "pages": 1, "date_posted": "week"},
        "serpapi": {"enabled": False, "location": "United Arab Emirates"},
        "rss": {"enabled": False, "feeds": []},
    },
    "matching": {
        "min_keyword_score": 35,  # 0-100, cheap first-pass filter
        "use_llm": True,
        "min_llm_score": 65,  # 0-100, Claude's fit assessment
        "max_llm_assessments_per_run": 40,
    },
    "tailoring": {
        "use_llm": True,
        "cover_letter": True,
        "formats": ["pdf", "docx"],
        "max_per_run": 15,
        "show_personal_details": True,  # nationality / visa / notice period line, common on UAE CVs
    },
    "apply": {
        "mode": "review",  # review = prepare + ask you; auto = submit automatically; off = never submit
        "channels": ["email", "browser"],
        "daily_limit": 10,
        "min_score_to_auto_apply": 80,
        "browser": {"headless": True, "executable_path": None, "timeout_seconds": 45},
        "email": {"cc_self": True},
    },
    "notify": {
        "email": {"enabled": False, "to": ""},
        "telegram": {"enabled": False},
        "ntfy": {"enabled": False, "topic": "", "server": "https://ntfy.sh"},
        "notify_when_nothing_new": False,
    },
    "llm": {"model": "claude-opus-5-5", "effort": "medium"},
    "storage": {"db_path": "data/jobbot.db", "output_dir": "output", "profile": "profile.yaml"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config(dict):
    """Dict with a few conveniences; `base_dir` resolves relative paths in the config."""

    base_dir: Path

    def path(self, *keys: str) -> Path:
        node = self
        for k in keys:
            node = node[k]
        p = Path(os.path.expanduser(str(node)))
        return p if p.is_absolute() else self.base_dir / p

    @staticmethod
    def env(name: str, default: str | None = None) -> str | None:
        value = os.environ.get(name)
        return value if value not in (None, "") else default


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    base_dir = path.parent.resolve() if path.parent != Path("") else Path.cwd()
    load_dotenv(base_dir / ".env")
    raw = {}
    if path.is_file():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config(_deep_merge(DEFAULTS, raw))
    cfg.base_dir = base_dir
    return cfg
