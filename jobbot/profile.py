"""The master CV ("profile"): the single source of truth that every tailored CV is derived from."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


class ProfileError(ValueError):
    pass


@dataclass
class Experience:
    company: str
    title: str
    start: str
    end: str = "Present"
    location: str = ""
    bullets: list[str] = field(default_factory=list)


@dataclass
class Profile:
    personal: dict
    headline: str
    summary: str
    skills: dict[str, list[str]]
    experience: list[Experience]
    education: list[dict] = field(default_factory=list)
    certifications: list[dict] = field(default_factory=list)
    projects: list[dict] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    application_answers: list[dict] = field(default_factory=list)

    # ---- construction -------------------------------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "Profile":
        if not isinstance(data, dict):
            raise ProfileError("profile must be a YAML mapping")
        personal = data.get("personal") or {}
        for key in ("name", "email"):
            if not personal.get(key):
                raise ProfileError(f"personal.{key} is required in the profile")
        skills = data.get("skills") or {}
        if isinstance(skills, list):
            skills = {"Skills": skills}
        skills = {str(k): [str(s) for s in v or []] for k, v in skills.items()}
        experience = []
        for i, exp in enumerate(data.get("experience") or []):
            for key in ("company", "title", "start"):
                if not exp.get(key):
                    raise ProfileError(f"experience[{i}].{key} is required")
            experience.append(Experience(
                company=str(exp["company"]), title=str(exp["title"]), start=str(exp["start"]),
                end=str(exp.get("end") or "Present"), location=str(exp.get("location") or ""),
                bullets=[str(b) for b in exp.get("bullets") or []],
            ))
        if not experience:
            raise ProfileError("at least one experience entry is required")
        return cls(
            personal=personal,
            headline=str(data.get("headline") or experience[0].title),
            summary=str(data.get("summary") or "").strip(),
            skills=skills,
            experience=experience,
            education=list(data.get("education") or []),
            certifications=list(data.get("certifications") or []),
            projects=list(data.get("projects") or []),
            languages=[str(x) for x in data.get("languages") or []],
            application_answers=list(data.get("application_answers") or []),
        )

    @classmethod
    def load(cls, path: str | Path) -> "Profile":
        path = Path(path)
        if not path.is_file():
            raise ProfileError(f"profile not found: {path} (run `jobbot init` and fill it in)")
        return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")))

    # ---- helpers ------------------------------------------------------
    @property
    def name(self) -> str:
        return self.personal["name"]

    def all_skills(self) -> list[str]:
        seen, out = set(), []
        for items in self.skills.values():
            for s in items:
                if s.lower() not in seen:
                    seen.add(s.lower())
                    out.append(s)
        return out

    def bullet_ids(self) -> dict[str, tuple[int, str]]:
        """Stable ids for every experience bullet: 'e0b2' = experience 0, bullet 2."""
        return {f"e{i}b{j}": (i, b) for i, exp in enumerate(self.experience) for j, b in enumerate(exp.bullets)}

    def all_text(self) -> str:
        parts = [self.headline, self.summary, " ".join(self.all_skills()), " ".join(self.languages)]
        for exp in self.experience:
            parts += [exp.title, exp.company, *exp.bullets]
        for group in (self.education, self.certifications, self.projects):
            for item in group:
                parts += [str(v) for v in item.values() if isinstance(v, (str, int, float))]
                parts += [str(b) for b in item.get("bullets", []) or []]
        return "\n".join(p for p in parts if p)

    def years_of_experience(self, today: date | None = None) -> int:
        today = today or date.today()
        spans = []
        for exp in self.experience:
            start = parse_month(exp.start)
            end = today if exp.end.strip().lower() in ("present", "current", "now", "") else parse_month(exp.end)
            if start and end and end >= start:
                spans.append((start, end))
        if not spans:
            return 0
        # merge overlapping spans so concurrent roles aren't double counted
        spans.sort()
        total, (cur_s, cur_e) = 0, spans[0]
        for s, e in spans[1:]:
            if s <= cur_e:
                cur_e = max(cur_e, e)
            else:
                total += (cur_e - cur_s).days
                cur_s, cur_e = s, e
        total += (cur_e - cur_s).days
        return int(total // 365.25)

    def answer_for(self, question: str) -> str | None:
        """Find a canned answer for an application-form question (regex `match` keys)."""
        q = question.lower()
        for item in self.application_answers:
            pattern = str(item.get("match") or "")
            if pattern and re.search(pattern, q, re.I):
                return str(item.get("answer", ""))
        return None


def parse_month(value: str) -> date | None:
    """Parse 'Jan 2021', 'January 2021', '2021-01', '01/2021' or '2021'."""
    s = str(value).strip().lower()
    m = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{4})", s)
    if m and m.group(1) in MONTHS:
        return date(int(m.group(2)), MONTHS[m.group(1)], 1)
    m = re.match(r"(\d{4})-(\d{1,2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = re.match(r"(\d{1,2})/(\d{4})", s)
    if m:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.match(r"(\d{4})$", s)
    if m:
        return date(int(m.group(1)), 1, 1)
    return None
