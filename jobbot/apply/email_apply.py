"""Apply by email - common for UAE postings ("send your CV to careers@...")."""

from __future__ import annotations

import re
from pathlib import Path

from .. import mailer
from ..models import ApplyResult, Job
from ..profile import Profile

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IGNORE_LOCAL = re.compile(r"^(no-?reply|donotreply|privacy|dpo|gdpr|unsubscribe|abuse|webmaster|info@example)", re.I)
_IGNORE_DOMAIN = re.compile(r"(example\.(com|org)|sentry\.io|email\.com|domain\.com)$", re.I)
_CONTEXT = re.compile(r"\b(cv|c\.v|resume|résumé|apply|application|applications|send|email|e-mail|submit|interested)\b", re.I)


def find_application_emails(text: str) -> list[str]:
    """Addresses the ad asks candidates to send their CV to (nearest to 'send your CV' wording first)."""
    found: list[tuple[int, str]] = []
    for m in _EMAIL.finditer(text or ""):
        addr = m.group(0).rstrip(".")
        local, _, domain = addr.partition("@")
        if _IGNORE_LOCAL.match(local) or _IGNORE_DOMAIN.search(domain):
            continue
        window = text[max(0, m.start() - 160): m.end() + 60]
        if not _CONTEXT.search(window):
            continue
        rank = 0 if re.search(r"\b(cv|resume|apply|application)\b", window, re.I) else 1
        if addr.lower() not in {a.lower() for _, a in found}:
            found.append((rank, addr))
    return [a for _, a in sorted(found, key=lambda x: x[0])]


def subject_reference(text: str) -> str | None:
    """Some ads ask you to quote a reference in the subject line."""
    for m in re.finditer(r"\bref(?:erence)?\b(?:\s*(?:no|number|code|#))?\.?\s*[:#]?\s*([A-Z0-9][A-Z0-9/_-]{2,20})",
                         text or "", re.I):
        if any(c.isdigit() for c in m.group(1)):
            return m.group(1).rstrip("-/_")
    return None


def compose(job: Job, profile: Profile, cover_letter: str) -> tuple[str, str]:
    ref = subject_reference(job.description)
    subject = f"Application for {job.title} - {profile.name}" + (f" (Ref: {ref})" if ref else "")
    body = cover_letter.strip() or (
        f"Dear Hiring Team,\n\nPlease find attached my CV for the {job.title} position at {job.company}.\n\n"
        f"Kind regards,\n{profile.name}")
    p = profile.personal
    signature = "\n".join(str(x) for x in (p.get("phone"), p.get("email"), p.get("linkedin")) if x)
    if signature and signature not in body:
        body += f"\n{signature}"
    return subject, body


def apply_by_email(job: Job, profile: Profile, files: dict[str, Path], cover_letter: str,
                   cc_self: bool = True, submit: bool = True) -> ApplyResult:
    targets = find_application_emails(job.description)
    if not targets:
        return ApplyResult("skipped", "email", "no application email address in the ad")
    if not mailer.smtp_configured():
        return ApplyResult("skipped", "email", f"ad asks for email to {targets[0]} but SMTP is not configured")
    attachments = [files[k] for k in ("cv_pdf", "cv_docx") if k in files][:1]
    if "cover_letter_pdf" in files:
        attachments.append(files["cover_letter_pdf"])
    if not attachments:
        return ApplyResult("failed", "email", "no CV file to attach")
    subject, body = compose(job, profile, cover_letter)
    if not submit:
        return ApplyResult("needs_manual", "email", f"draft ready - email {targets[0]} with subject '{subject}'")
    try:
        mailer.send_email(targets[0], subject, body, attachments=attachments,
                          cc=[profile.personal["email"]] if cc_self else None,
                          reply_to=profile.personal["email"], from_name=profile.name)
    except mailer.MailError as exc:
        return ApplyResult("failed", "email", str(exc))
    return ApplyResult("applied", "email", f"emailed {targets[0]}")
