"""Submit an application through the first channel that can handle the job."""

from __future__ import annotations

from pathlib import Path

from ..models import ApplyResult, Job
from ..profile import Profile
from .browser_apply import BrowserApplier, application_url, is_supported
from .email_apply import apply_by_email, find_application_emails


def submit_application(job: Job, profile: Profile, files: dict[str, Path], cover_letter: str, apply_cfg: dict,
                       screenshot_dir: Path, submit: bool = True, channels: list[str] | None = None) -> ApplyResult:
    """Try each configured channel in order. A channel that doesn't apply to this job returns 'skipped'
    and the next one is tried; if none can submit, the result is 'needs_manual' with the reasons."""
    reasons = []
    for channel in channels or apply_cfg.get("channels") or ["email", "browser"]:
        if channel == "email":
            result = apply_by_email(job, profile, files, cover_letter,
                                    cc_self=(apply_cfg.get("email") or {}).get("cc_self", True), submit=submit)
        elif channel == "browser":
            result = BrowserApplier(apply_cfg.get("browser") or {}).apply(
                job, profile, files, cover_letter, submit=submit, screenshot_dir=screenshot_dir)
        else:
            result = ApplyResult("skipped", channel, f"unknown channel {channel}")
        if result.status != "skipped":
            return result
        reasons.append(f"{channel}: {result.detail}")
    return ApplyResult("needs_manual", "manual", "apply yourself at the link - " + "; ".join(reasons))


__all__ = ["submit_application", "application_url", "is_supported", "find_application_emails", "BrowserApplier"]
