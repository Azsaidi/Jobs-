"""Notifications to you: email digest, Telegram and/or ntfy push."""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass, field

import requests

from . import mailer
from .config import Config

log = logging.getLogger(__name__)


@dataclass
class RunSummary:
    fetched: int = 0
    new: int = 0
    duplicates: int = 0
    filtered: int = 0
    applied: list[dict] = field(default_factory=list)
    prepared: list[dict] = field(default_factory=list)
    needs_manual: list[dict] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def has_news(self) -> bool:
        return bool(self.applied or self.prepared or self.needs_manual or self.failed)

    def headline(self) -> str:
        parts = []
        if self.applied:
            parts.append(f"{len(self.applied)} applied")
        if self.prepared:
            parts.append(f"{len(self.prepared)} to review")
        if self.needs_manual:
            parts.append(f"{len(self.needs_manual)} need you")
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        return "jobbot: " + (", ".join(parts) if parts else f"no new matches ({self.new} new postings checked)")


def _line(a: dict) -> str:
    score = a.get("llm_score") or a.get("match_score")
    s = f"#{a['id']} {a['title']} - {a['company']}"
    if score is not None:
        s += f" (fit {round(score)})"
    return s


def digest_text(summary: RunSummary) -> str:
    out = [summary.headline(), ""]

    def block(title: str, apps: list[dict], extra) -> None:
        if not apps:
            return
        out.append(f"{title} ({len(apps)})")
        for a in apps:
            out.append(f"  - {_line(a)}")
            for x in extra(a):
                if x:
                    out.append(f"      {x}")
        out.append("")

    block("APPLIED", summary.applied, lambda a: [f"via {a.get('channel')}", a.get("url")])
    block("READY FOR YOUR REVIEW - approve with `jobbot apply <id>`", summary.prepared,
          lambda a: [a.get("match_summary"), a.get("url"), f"CV: {a.get('cv_pdf') or a.get('cv_docx')}"])
    block("NEEDS YOU TO FINISH (captcha / extra questions / no online form)", summary.needs_manual,
          lambda a: [a.get("notes"), a.get("apply_url") or a.get("url"), f"CV: {a.get('cv_pdf') or a.get('cv_docx')}"])
    block("FAILED", summary.failed, lambda a: [a.get("notes")])
    if summary.errors:
        out.append("SOURCE ERRORS")
        out += [f"  - {e}" for e in summary.errors[:10]]
        out.append("")
    out.append(f"Postings fetched: {summary.fetched}, new: {summary.new}, duplicates: {summary.duplicates}, "
               f"filtered out: {summary.filtered}")
    if summary.stats:
        out.append("Tracker: " + ", ".join(f"{k}={v}" for k, v in sorted(summary.stats.items())))
    return "\n".join(out).strip() + "\n"


def digest_html(summary: RunSummary) -> str:
    def rows(apps: list[dict], cols) -> str:
        return "".join("<tr>" + "".join(f"<td style='padding:4px 8px;border-bottom:1px solid #eee'>{c(a)}</td>"
                                        for c in cols) + "</tr>" for a in apps)

    def link(a):
        url = a.get("apply_url") or a.get("url") or ""
        return f"<a href='{html.escape(url)}'>{html.escape(a['title'])}</a>" if url else html.escape(a["title"])

    def score(a):
        s = a.get("llm_score") or a.get("match_score")
        return "" if s is None else str(round(s))

    common = [lambda a: f"#{a['id']}", link, lambda a: html.escape(a["company"]), score]
    parts = [f"<h2 style='font-family:sans-serif'>{html.escape(summary.headline())}</h2>"]
    for title, apps, extra in (
        ("Applied", summary.applied, lambda a: html.escape(a.get("channel") or "")),
        ("Ready for your review", summary.prepared, lambda a: html.escape(a.get("match_summary") or "")),
        ("Needs you to finish", summary.needs_manual, lambda a: html.escape(a.get("notes") or "")),
        ("Failed", summary.failed, lambda a: html.escape(a.get("notes") or "")),
    ):
        if apps:
            parts.append(f"<h3 style='font-family:sans-serif'>{title} ({len(apps)})</h3>"
                         f"<table style='font-family:sans-serif;font-size:14px;border-collapse:collapse'>"
                         f"{rows(apps, common + [extra])}</table>")
    if summary.prepared:
        parts.append("<p style='font-family:sans-serif'>Approve with <code>jobbot apply &lt;id&gt;</code>, "
                     "or skip with <code>jobbot status &lt;id&gt; archived</code>.</p>")
    if summary.errors:
        parts.append("<p style='font-family:sans-serif;color:#a33'>Source errors:<br>"
                     + "<br>".join(html.escape(e) for e in summary.errors[:10]) + "</p>")
    parts.append(f"<p style='font-family:sans-serif;color:#666'>Fetched {summary.fetched}, new {summary.new}, "
                 f"duplicates {summary.duplicates}, filtered {summary.filtered}.</p>")
    return "\n".join(parts)


class Notifier:
    def __init__(self, cfg: dict, fallback_email: str | None = None):
        self.cfg = cfg.get("notify") or {}
        self.fallback_email = fallback_email

    def channels(self) -> list[str]:
        return [name for name in ("email", "telegram", "ntfy") if (self.cfg.get(name) or {}).get("enabled")]

    def send(self, subject: str, text: str, html_body: str | None = None) -> list[str]:
        """Send to every enabled channel. Returns error messages (empty list = all good)."""
        errors = []
        for name in self.channels():
            try:
                getattr(self, f"_send_{name}")(subject, text, html_body)
            except Exception as exc:  # noqa: BLE001 - a failing channel must not hide the others
                log.warning("notification via %s failed: %s", name, exc)
                errors.append(f"{name}: {exc}")
        return errors

    def send_digest(self, summary: RunSummary) -> list[str]:
        if not summary.has_news and not self.cfg.get("notify_when_nothing_new"):
            return []
        return self.send(summary.headline(), digest_text(summary), digest_html(summary))

    def _send_email(self, subject, text, html_body):
        to = (self.cfg.get("email") or {}).get("to") or self.fallback_email
        if not to:
            raise RuntimeError("notify.email.to is not set")
        mailer.send_email(to, subject, text, html=html_body, from_name="jobbot")

    def _send_telegram(self, subject, text, html_body):
        token, chat = Config.env("TELEGRAM_BOT_TOKEN"), Config.env("TELEGRAM_CHAT_ID")
        if not token or not chat:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
        body = text if text.startswith(subject) else f"{subject}\n\n{text}"
        for i in range(0, len(body), 4000):  # Telegram message limit is 4096 chars
            r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat, "text": body[i:i + 4000], "disable_web_page_preview": True},
                              timeout=30)
            r.raise_for_status()

    def _send_ntfy(self, subject, text, html_body):
        ncfg = self.cfg.get("ntfy") or {}
        topic = ncfg.get("topic")
        if not topic:
            raise RuntimeError("notify.ntfy.topic is not set")
        headers = {"Title": subject.encode("ascii", "ignore").decode(), "Tags": "briefcase"}
        if Config.env("NTFY_TOKEN"):
            headers["Authorization"] = f"Bearer {Config.env('NTFY_TOKEN')}"
        r = requests.post(f"{ncfg.get('server', 'https://ntfy.sh').rstrip('/')}/{topic}",
                          data=text[:3900].encode("utf-8"), headers=headers, timeout=30)
        r.raise_for_status()
