"""Tailor the master CV to one job, for ATS ranking and for a human reader.

Ground rule: a tailored CV may re-order, select and re-word what is in your master CV,
but must never add facts. Claude's output is checked against the master CV and anything
that cannot be traced back to it (new numbers, unknown skills, bullets without a source)
is reverted to your original wording and reported as a warning.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from . import ats
from .matching import job_context, profile_context
from .models import Job, TailoredCV, TailoredExperience
from .profile import Profile

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


class _Bullet(BaseModel):
    source_ids: list[str] = Field(description="ids of the master-CV bullets this bullet is based on, e.g. ['e0b2']")
    text: str


class _Role(BaseModel):
    experience_id: str = Field(description="id of the master-CV role, e.g. 'e0'")
    bullets: list[_Bullet]


class _SkillGroup(BaseModel):
    category: str
    skills: list[str]


class _TailoredOutput(BaseModel):
    headline: str = Field(description="Professional headline aligned to the target job title, truthful to the CV")
    summary: str = Field(description="3-4 sentence professional summary")
    skill_groups: list[_SkillGroup]
    roles: list[_Role]
    keywords_targeted: list[str]
    missing_keywords: list[str] = Field(description="Important job keywords the candidate genuinely lacks")
    cover_letter: str = Field(description="Cover letter body, no address block, signed with the candidate's name")


TAILOR_SYSTEM = """You tailor a candidate's master CV to a specific job in the UAE so it ranks well in applicant
tracking systems (ATS) and reads well to a recruiter. The master CV follows.

Absolute rules - the CV will be checked automatically and violations are discarded:
- Never invent employers, titles, dates, responsibilities, tools, certifications or metrics. Every number you
  write must appear in the cited source bullets. Every bullet must cite the master-CV bullet ids it is based on.
- Skills must be copied exactly as written in the master CV skills list; order the most relevant first.
- You may rephrase bullets to use the job ad's exact terminology when it truthfully describes the same work
  (e.g. "built dashboards" -> "built Power BI dashboards" ONLY if Power BI is in that role's source text).
- Include every role from the master CV (use its id), most relevant bullets first; keep 2-6 bullets per role,
  fewer for older roles. Start bullets with strong action verbs; one line each where possible.
- List genuinely missing must-have keywords in missing_keywords instead of inserting them.
- Use plain text only: no markdown, emojis, tables or special symbols.

Cover letter: 180-250 words, professional and specific to this company and role, written for a UAE employer,
based only on facts in the master CV. No placeholders like [Company]. No address block or date. Sign off with
the candidate's name."""


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def _allowed_numbers(profile: Profile) -> set[str]:
    years = profile.years_of_experience()
    return _numbers(profile.all_text()) | {str(years), str(max(years - 1, 0))}


def keyword_tailor(profile: Profile, job: Job, keywords: list[str], cover_letter: bool = True) -> TailoredCV:
    """Deterministic tailoring (no LLM): put the most relevant skills and bullets first."""

    def relevance(text: str) -> int:
        return sum(ats.count_term(k, text) for k in keywords)

    skills = {}
    for cat, items in profile.skills.items():
        skills[cat] = sorted(items, key=lambda s: -relevance(s))
    roles = []
    for i, exp in enumerate(profile.experience):
        bullets = sorted(exp.bullets, key=lambda b: -relevance(b))
        roles.append(TailoredExperience(exp.company, exp.title, exp.location, exp.start, exp.end,
                                        bullets[: max(6 if i < 2 else 4, 2)]))
    _, _, missing = ats.coverage(profile.all_text(), keywords)
    return TailoredCV(
        headline=profile.headline, summary=profile.summary, skills=skills, experience=roles,
        keywords_targeted=[k for k in keywords if k not in missing], missing_keywords=missing,
        cover_letter=template_cover_letter(profile, job, keywords) if cover_letter else "", method="keyword",
    )


def template_cover_letter(profile: Profile, job: Job, keywords: list[str]) -> str:
    recent = profile.experience[0]
    own = {s.lower() for s in profile.all_skills()}
    matched = [k for k in keywords if k.lower() in own][:4]
    skills_line = f"My background in {', '.join(matched[:-1])} and {matched[-1]}" if len(matched) > 1 else ""
    years = profile.years_of_experience()
    exp_line = f"With {years}+ years of experience, most recently" if years else "Most recently,"
    best = recent.bullets[0].rstrip(".") if recent.bullets else ""
    if best[1:2].islower():
        best = best[0].lower() + best[1:]
    return (
        f"Dear Hiring Team,\n\n"
        f"I am writing to apply for the {job.title} position at {job.company}. {exp_line} as {recent.title} at "
        f"{recent.company}, I have developed the experience this role calls for.\n\n"
        + (f"In that role, I {best}. " if best else "")
        + (f"{skills_line} aligns closely with the requirements you have outlined.\n\n" if skills_line else "\n\n")
        + f"I would welcome the opportunity to discuss how I can contribute to {job.company}. "
        f"Thank you for your time and consideration.\n\nKind regards,\n{profile.name}"
    )


def llm_tailor(llm, profile: Profile, job: Job, keywords: list[str], cover_letter: bool = True) -> TailoredCV:
    prompt = (
        job_context(job)
        + f"\n\nATS keywords detected in this ad (most important first): {', '.join(keywords) or 'none'}\n\n"
        + "Tailor the master CV for this job."
        + ("" if cover_letter else " Return an empty cover_letter.")
    )
    out: _TailoredOutput = llm.parse(system=TAILOR_SYSTEM, cached_context=profile_context(profile),
                                     prompt=prompt, schema=_TailoredOutput, max_tokens=16000)
    cv = validate_tailored(profile, out)
    if not cover_letter:
        cv.cover_letter = ""
    elif not cv.cover_letter:
        cv.cover_letter = template_cover_letter(profile, job, keywords)
    return cv


def validate_tailored(profile: Profile, out: _TailoredOutput) -> TailoredCV:
    """Enforce the no-fabrication rules; revert anything that can't be traced to the master CV."""
    warnings: list[str] = []
    bullet_map = profile.bullet_ids()
    allowed_numbers = _allowed_numbers(profile)

    # Skills: only ones present in the master CV, spelled as there.
    canonical = {s.lower(): s for s in profile.all_skills()}
    skills: dict[str, list[str]] = {}
    used: set[str] = set()
    for group in out.skill_groups:
        kept = []
        for s in group.skills:
            key = s.strip().lower()
            if key in canonical and key not in used:
                kept.append(canonical[key])
                used.add(key)
            elif key not in canonical:
                warnings.append(f"dropped skill not in master CV: {s}")
        if kept:
            skills[group.category.strip() or "Skills"] = kept
    leftovers = [s for s in profile.all_skills() if s.lower() not in used]
    if leftovers:
        skills.setdefault("Additional Skills", []).extend(leftovers)

    # Roles: every role kept, facts (company/title/dates) always from the master CV.
    by_id = {r.experience_id.strip(): r for r in out.roles}
    experience = []
    for i, exp in enumerate(profile.experience):
        role = by_id.get(f"e{i}")
        bullets: list[str] = []
        for b in role.bullets if role else []:
            text = b.text.strip().lstrip("-•* ").strip()
            sources = [sid for sid in b.source_ids if sid in bullet_map and bullet_map[sid][0] == i]
            if not sources or not text:
                warnings.append(f"{exp.company}: dropped bullet without a valid source: {text[:80]}")
                continue
            source_text = " ".join(bullet_map[sid][1] for sid in sources)
            invented = _numbers(text) - _numbers(source_text)
            if invented:
                warnings.append(f"{exp.company}: reverted bullet with numbers not in source ({', '.join(sorted(invented))})")
                text = bullet_map[sources[0]][1]
            if text not in bullets:
                bullets.append(text)
        if not bullets:
            bullets = list(exp.bullets[:4])
            if role is None:
                warnings.append(f"{exp.company}: role missing from tailored output - used original bullets")
        experience.append(TailoredExperience(exp.company, exp.title, exp.location, exp.start, exp.end, bullets))

    summary = out.summary.strip()
    if not summary or _numbers(summary) - allowed_numbers:
        if summary:
            warnings.append("summary contained numbers not in the master CV - kept original summary")
        summary = profile.summary
    headline = out.headline.strip()
    if not headline or _numbers(headline) - allowed_numbers or len(headline) > 90:
        headline = profile.headline
    cover = out.cover_letter.strip()
    if cover and _numbers(cover) - allowed_numbers:
        warnings.append("cover letter contained numbers not in the master CV - replaced with template")
        cover = ""

    return TailoredCV(
        headline=headline, summary=summary, skills=skills, experience=experience,
        keywords_targeted=list(out.keywords_targeted), missing_keywords=list(out.missing_keywords),
        cover_letter=cover, warnings=warnings, method="llm",
    )
