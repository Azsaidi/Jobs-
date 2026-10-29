"""The end-to-end run: find -> filter -> assess -> tailor -> apply -> notify -> track."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from . import ats
from .apply import submit_application
from .db import Tracker
from .llm import LLMError
from .matching import FitAssessment, MatchResult, assess_fit, keyword_match, score_job
from .models import ApplyResult, Job, TailoredCV, TailoredExperience
from .notify import Notifier, RunSummary
from .profile import Profile
from .render import cv_text, render_all
from .sources import fetch_all
from .tailor import keyword_tailor, llm_tailor
from .util import now_iso, slugify

log = logging.getLogger(__name__)


def base_cv(profile: Profile) -> TailoredCV:
    """The master CV as-is, for 'before tailoring' ATS scores."""
    return TailoredCV(
        headline=profile.headline, summary=profile.summary, skills=profile.skills,
        experience=[TailoredExperience(e.company, e.title, e.location, e.start, e.end, e.bullets)
                    for e in profile.experience])


class Pipeline:
    def __init__(self, cfg, profile: Profile, tracker: Tracker, llm=None, notifier: Notifier | None = None,
                 fetcher=fetch_all):
        self.cfg = cfg
        self.profile = profile
        self.tracker = tracker
        self.llm = llm
        self.notifier = notifier
        self.fetcher = fetcher
        self.output_dir = cfg.path("storage", "output_dir") if hasattr(cfg, "path") else Path(
            cfg["storage"]["output_dir"])

    # ---- full run --------------------------------------------------------
    def run(self, *, apply: bool = True, notify: bool = True, tailor: bool = True) -> RunSummary:
        cfg, summary = self.cfg, RunSummary()
        jobs, errors = self.fetcher(cfg)
        summary.fetched, summary.errors = len(jobs), list(errors)

        candidates: list[Job] = []
        for job in jobs:
            state = self.tracker.upsert_job(job)
            if state == "new":
                summary.new += 1
                candidates.append(job)
            elif state == "duplicate":
                summary.duplicates += 1
        seen = {j.id for j in candidates}
        candidates += [j for j in self.tracker.backlog() if j.id not in seen]

        matches = self._filter_and_score(candidates, summary)
        assessed = self._assess(matches, summary)
        if not tailor:
            summary.prepared = [self._preview(job, m, fit) for job, m, fit in assessed]
            return summary

        limit = int(cfg["tailoring"].get("max_per_run", 15))
        prepared_ids = []
        for job, m, fit in assessed[:limit]:
            try:
                prepared_ids.append(self.prepare(job, m, fit))
            except Exception as exc:  # noqa: BLE001 - one bad posting must not stop the run
                log.exception("preparing %s failed", job.title)
                summary.errors.append(f"tailoring '{job.title}' at {job.company} failed: {exc}")

        apply_cfg = cfg["apply"]
        mode = apply_cfg.get("mode", "review")
        for app_id in prepared_ids:
            app = self.tracker.application(app_id)
            score = app["llm_score"] if app["llm_score"] is not None else app["match_score"]
            can_auto = (apply and mode == "auto" and score is not None
                        and score >= float(apply_cfg.get("min_score_to_auto_apply", 80))
                        and self.tracker.applied_since(24) < int(apply_cfg.get("daily_limit", 10)))
            if can_auto:
                self.apply_application(app_id)
                app = self.tracker.application(app_id)
                {"applied": summary.applied, "needs_manual": summary.needs_manual,
                 "failed": summary.failed}.get(app["status"], summary.prepared).append(app)
            else:
                summary.prepared.append(app)

        summary.stats = self.tracker.stats()
        if notify and self.notifier:
            summary.errors += [f"notification {e}" for e in self.notifier.send_digest(summary)]
        return summary

    def _filter_and_score(self, jobs: list[Job], summary: RunSummary) -> list[tuple[Job, MatchResult]]:
        search, min_kw = self.cfg["search"], float(self.cfg["matching"].get("min_keyword_score", 35))
        out = []
        for job in jobs:
            m = score_job(job, self.profile, search)
            if not m.passed:
                self.tracker.set_job_skip(job.id, m.reason)
                summary.filtered += 1
            elif m.keyword_score < min_kw:
                self.tracker.set_job_skip(job.id, f"keyword score {m.keyword_score} < {min_kw}", m.keyword_score)
                summary.filtered += 1
            else:
                self.tracker.set_keyword_score(job.id, m.keyword_score)
                out.append((job, m))
        out.sort(key=lambda x: -x[1].keyword_score)
        return out

    def _assess(self, matches, summary: RunSummary) -> list[tuple[Job, MatchResult, FitAssessment | None]]:
        mcfg = self.cfg["matching"]
        use_llm = self.llm is not None and mcfg.get("use_llm", True)
        budget = int(mcfg.get("max_llm_assessments_per_run", 40))
        min_llm = float(mcfg.get("min_llm_score", 65))
        out = []
        for job, m in matches:
            fit = None
            if use_llm and budget > 0:
                budget -= 1
                try:
                    fit = assess_fit(self.llm, self.profile, job)
                except LLMError as exc:
                    summary.errors.append(f"fit assessment for '{job.title}': {exc}")
                if fit and (fit.score < min_llm or fit.dealbreakers):
                    why = "; ".join(fit.dealbreakers) or fit.summary
                    self.tracker.set_job_skip(job.id, f"fit {fit.score} - {why}"[:500])
                    summary.filtered += 1
                    continue
            elif use_llm:
                continue  # over today's assessment budget: stays in the backlog for the next run
            out.append((job, m, fit))
        out.sort(key=lambda x: -(x[2].score if x[2] else x[1].keyword_score))
        return out

    def _preview(self, job: Job, m: MatchResult, fit: FitAssessment | None) -> dict:
        return {"id": job.id, "title": job.title, "company": job.company, "url": job.url, "apply_url": job.apply_url,
                "match_score": m.keyword_score, "llm_score": fit.score if fit else None,
                "match_summary": fit.summary if fit else ", ".join(m.keywords[:8])}

    # ---- per job ---------------------------------------------------------
    def tailor(self, job: Job, keywords: list[str]) -> TailoredCV:
        tcfg = self.cfg["tailoring"]
        letter = tcfg.get("cover_letter", True)
        if self.llm is not None and tcfg.get("use_llm", True):
            try:
                return llm_tailor(self.llm, self.profile, job, keywords, cover_letter=letter)
            except LLMError as exc:
                cv = keyword_tailor(self.profile, job, keywords, cover_letter=letter)
                cv.warnings.append(f"Claude tailoring failed ({exc}) - used keyword tailoring")
                return cv
        return keyword_tailor(self.profile, job, keywords, cover_letter=letter)

    def prepare(self, job: Job, m: MatchResult | None = None, fit: FitAssessment | None = None) -> int:
        """Tailor the CV + cover letter for a job and record it as 'prepared'."""
        if m is None:
            m = keyword_match(job, self.profile, self.cfg["search"])
        keywords = m.keywords
        cv = self.tailor(job, keywords)
        tcfg = self.cfg["tailoring"]
        details = tcfg.get("show_personal_details", True)
        folder = self.output_dir / "applications" / f"{date.today():%Y-%m-%d}_{slugify(job.company, 25)}_{slugify(job.title, 40)}"
        files = render_all(cv, self.profile, job, folder, tcfg.get("formats", ["pdf", "docx"]), details)

        before, _, _ = ats.coverage(cv_text(base_cv(self.profile), self.profile, details), keywords)
        after, matched, missing = ats.coverage(cv_text(cv, self.profile, details), keywords)
        lint = ats.lint_cv_text(cv_text(cv, self.profile, details), self.profile.personal.get("email", ""),
                                self.profile.personal.get("phone"))
        notes = cv.warnings + m.flags + [f"ATS: {x}" for x in lint]
        (folder / "job.md").write_text(_job_report(job, m, fit, cv, before, after, matched, missing, notes),
                                       encoding="utf-8")
        return self.tracker.create_application(
            job.id, "prepared", match_score=m.keyword_score, llm_score=fit.score if fit else None,
            match_summary=fit.summary if fit else f"keyword match {m.keyword_score}",
            ats_before=before, ats_after=after, missing_keywords=sorted(set(missing) | set(cv.missing_keywords)),
            cv_pdf=str(files.get("cv_pdf") or ""), cv_docx=str(files.get("cv_docx") or ""),
            cover_letter=str(files.get("cover_letter_txt") or ""), notes="; ".join(notes)[:2000] or None,
        )

    def application_files(self, app: dict) -> tuple[dict[str, Path], str]:
        files: dict[str, Path] = {}
        for key in ("cv_pdf", "cv_docx"):
            if app.get(key) and Path(app[key]).exists():
                files[key] = Path(app[key])
        letter = ""
        if app.get("cover_letter") and Path(app["cover_letter"]).exists():
            letter = Path(app["cover_letter"]).read_text(encoding="utf-8")
            pdf = Path(app["cover_letter"]).with_suffix(".pdf")
            if pdf.exists():
                files["cover_letter_pdf"] = pdf
        return files, letter

    def apply_application(self, app_id: int, submit: bool = True, channels: list[str] | None = None) -> ApplyResult:
        app = self.tracker.application(app_id)
        if app is None:
            raise KeyError(f"no application #{app_id}")
        job = self.tracker.get_job(app["job_id"])
        files, letter = self.application_files(app)
        if not files:
            raise FileNotFoundError(f"application #{app_id} has no CV files - run `jobbot tailor {app_id}`")
        result = submit_application(job, self.profile, files, letter, self.cfg["apply"],
                                    self.output_dir / "screenshots", submit=submit, channels=channels)
        fields = {"status": result.status if result.status in ("applied", "needs_manual", "failed") else "needs_manual",
                  "channel": result.channel, "notes": result.detail}
        if result.status == "applied":
            fields["applied_at"] = now_iso()
        self.tracker.update_application(app_id, **fields)
        self.tracker.log(app_id, "apply", f"{result.channel}: {result.detail}"
                         + (f" (screenshot: {result.screenshot})" if result.screenshot else ""))
        return result


def _job_report(job, m, fit, cv, before, after, matched, missing, notes) -> str:
    lines = [f"# {job.title} - {job.company}", "", f"- Location: {job.location}", f"- Link: {job.url}",
             f"- Apply: {job.apply_url or job.url}", f"- Source: {job.source}", f"- Posted: {job.posted_at or 'unknown'}",
             f"- Keyword match: {m.keyword_score}"]
    if fit:
        lines += [f"- Claude fit score: {fit.score}", "", f"**Assessment:** {fit.summary}", "",
                  "**Strengths:**", *[f"- {s}" for s in fit.strengths], "", "**Gaps:**", *[f"- {g}" for g in fit.gaps]]
    lines += ["", f"## ATS keyword coverage: {before}% -> {after}% after tailoring ({cv.method})", "",
              f"Matched: {', '.join(matched) or '-'}", "", f"Missing (consider only if true for you): {', '.join(missing) or '-'}"]
    if notes:
        lines += ["", "## Notes", *[f"- {n}" for n in notes]]
    lines += ["", "## Job description", "", job.description]
    return "\n".join(lines) + "\n"
