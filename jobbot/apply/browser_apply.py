"""Fill (and optionally submit) hosted application forms on Greenhouse, Lever, Ashby and Workable.

The filler only submits when every required field has an answer and no visible captcha
is present. Anything else - unknown questions, captchas, sites that need an account
(Workday, Taleo, SuccessFactors...) - ends as `needs_manual`, with a screenshot of the
pre-filled form, and you finish it yourself (`jobbot open <id>` opens it pre-filled).
"""

from __future__ import annotations

import glob
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..models import ApplyResult, Job
from ..profile import Profile

log = logging.getLogger(__name__)

FORM_SELECTOR = ("input[type=email], input[name*=email i], input[id*=email i], input[autocomplete=email], "
                 "input[type=file]")
SUPPORTED_HOSTS = re.compile(r"(greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com)$", re.I)

_COLLECT_JS = r"""
() => {
  const clean = t => (t || '').replace(/\s+/g, ' ').replace(/\*/g, '').trim();
  const visible = el => {
    const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelFor = el => {
    let t = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.innerText; }
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(/\s+/).map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) {
      const c = el.closest('fieldset, [class*="field"], [class*="question"], [class*="Field"], [class*="Question"]');
      if (c) { const lg = c.querySelector('legend, label, [class*="label"], [class*="Label"]'); if (lg) t = lg.innerText; }
    }
    if (!t) t = el.placeholder || el.name || '';
    return clean(t);
  };
  const groupLabel = el => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg) return clean(lg.innerText); }
    let c = el.parentElement;
    for (let i = 0; c && i < 5; i++, c = c.parentElement) {
      const lg = c.querySelector(':scope > label, :scope > legend, :scope > [class*="label"], :scope > [class*="question"], :scope > div > label');
      if (lg && !lg.contains(el)) return clean(lg.innerText);
    }
    return '';
  };
  const out = []; let i = 0;
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search'].includes(type)) continue;
    if (type !== 'file' && !visible(el)) continue;
    if (el.closest('[aria-hidden="true"]') && type !== 'file') continue;
    el.setAttribute('data-jobbot-idx', String(i));
    const d = { idx: i, tag: el.tagName.toLowerCase(), type, name: el.name || '', id: el.id || '',
      label: labelFor(el), required: !!(el.required || el.getAttribute('aria-required') === 'true'),
      value: type === 'file' ? (el.files && el.files.length ? 'file' : '') : (el.value || ''),
      role: el.getAttribute('role') || '', accept: el.accept || '' };
    if (type === 'radio' || type === 'checkbox') { d.group = groupLabel(el); d.checked = el.checked; d.value = el.checked ? 'on' : ''; }
    if (type === 'file' && (!d.label || /^(attach|upload|choose|browse|select)/i.test(d.label))) d.label = clean(groupLabel(el) + ' ' + d.label);
    if (d.role === 'combobox') {
      // react-select style widgets: typed text is only a search; a choice shows up as a separate value element
      const box = el.closest('[class*="control"], [class*="Control"]');
      if (box) d.value = box.querySelector('[class*="singleValue"], [class*="single-value"], [class*="multiValue"], [class*="multi-value"]') ? 'selected' : '';
    }
    if (el.tagName === 'SELECT') { d.options = Array.from(el.options).map(o => clean(o.text)); d.value = el.selectedIndex > 0 ? el.value : ''; }
    out.push(d); i++;
  }
  return out;
}
"""

_CAPTCHA_JS = r"""
() => Array.from(document.querySelectorAll('iframe')).some(f => {
  const s = f.src || ''; const r = f.getBoundingClientRect();
  const big = r.width > 60 && r.height > 60 && getComputedStyle(f).visibility !== 'hidden';
  return big && /recaptcha\/.*(anchor|bframe)|hcaptcha\.com|challenges\.cloudflare\.com/.test(s) && !/size=invisible/.test(s);
})
"""

_SUCCESS = re.compile(
    r"thank(s| you) for (applying|your application|your interest)|application (has been |was )?(received|submitted)"
    r"|successfully (submitted|applied)|we('ve| have) received your application", re.I)
_CONSENT = re.compile(r"privacy|consent|agree|terms|acknowledge|data (processing|protection)|gdpr", re.I)
_DECLINE = re.compile(r"decline|prefer not|don.?t wish|do not wish|not to (say|disclose|answer)", re.I)
_EEO = re.compile(r"gender|race|ethnic|veteran|disabilit|sexual orientation|pronoun|transgender", re.I)


def find_chromium(configured: str | None = None) -> str | None:
    """A Chromium binary for Playwright: config, $JOBBOT_CHROMIUM, or a preinstalled one."""
    for candidate in (configured, os.environ.get("JOBBOT_CHROMIUM")):
        if candidate and Path(candidate).exists():
            return candidate
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux*/chrome",):
        found = sorted(glob.glob(pattern))
        if found:
            return found[-1]
    return None  # let Playwright use its own downloaded browser


def application_url(job: Job) -> str:
    """The URL that shows the application form itself."""
    url = job.apply_url or job.url
    if job.source == "greenhouse" or "greenhouse.io" in url:
        board = job.extra.get("board")
        jid = parse_qs(urlparse(url).query).get("gh_jid", [None])[0] or url.rstrip("/").split("/")[-1].split("?")[0]
        if board and jid and jid.isdigit():
            host = "job-boards.eu.greenhouse.io" if ".eu.greenhouse.io" in url else "job-boards.greenhouse.io"
            return f"https://{host}/embed/job_app?for={board}&token={jid}"
    if "jobs.lever.co" in url and not url.rstrip("/").endswith("/apply"):
        return url.rstrip("/") + "/apply"
    if "jobs.ashbyhq.com" in url and not url.rstrip("/").endswith("/application"):
        return url.rstrip("/") + "/application"
    return url


def _country(location: str | None) -> str | None:
    loc = (location or "").lower()
    if any(x in loc for x in ("uae", "emirates", "dubai", "abu dhabi", "sharjah", "ajman", "ras al", "fujairah", "al ain")):
        return "United Arab Emirates"
    return location.split(",")[-1].strip() if location and "," in location else None


def is_supported(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return bool(SUPPORTED_HOSTS.search(host))


class FormFiller:
    """Maps form fields to answers from your profile. Pure logic, testable without a browser."""

    def __init__(self, profile: Profile, files: dict[str, Path], cover_letter: str, accept_consent: bool = True):
        self.profile = profile
        self.files = files
        self.cover_letter = cover_letter
        self.accept_consent = accept_consent
        p = profile.personal
        parts = profile.name.split()
        recent = profile.experience[0]
        self.basic = [
            (r"first\s*name|given\s*name|forename", p.get("first_name") or parts[0]),
            (r"last\s*name|surname|family\s*name", p.get("last_name") or (parts[-1] if len(parts) > 1 else "")),
            (r"preferred\s*name", p.get("preferred_name") or parts[0]),
            (r"(current|most recent|present)\s*(company|employer)|^company$|^employer$", recent.company),
            (r"(current|most recent|present)\s*(job\s*)?(title|position|role|designation)", recent.title),
            (r"^(full\s*|legal\s*|your\s*)?name$", profile.name),
            (r"e-?mail", p.get("email")),
            (r"phone|mobile|telephone|contact\s*number|whatsapp", p.get("phone")),
            (r"linkedin", p.get("linkedin")),
            (r"github", p.get("github")),
            (r"website|portfolio|personal\s*(site|url)|blog", p.get("website") or p.get("linkedin")),
            (r"(current|your)\s+location|^location|^city|where are you (based|located)|city of residence", p.get("location")),
            (r"nationality|citizenship", p.get("nationality")),
            (r"^country( of residence)?$|^(current\s*)?country", p.get("country") or _country(p.get("location"))),
        ]

    def text_value(self, label: str) -> str | None:
        lab = label.lower().strip()
        if re.search(r"cover\s*letter|additional information|message to (the )?hiring|why (do you want|are you interested)", lab):
            answer = self.profile.answer_for(label)
            return answer or self.cover_letter or None
        answer = self.profile.answer_for(label)
        if answer is not None:
            return answer
        for pattern, value in self.basic:
            if value and re.search(pattern, lab):
                return str(value)
        return None

    def file_value(self, field: dict) -> Path | None:
        hint = f"{field.get('label', '')} {field.get('name', '')} {field.get('id', '')}".lower()
        if "cover" in hint:
            return self.files.get("cover_letter_pdf")
        return self.files.get("cv_pdf") or self.files.get("cv_docx")

    def choose_option(self, question: str, options: list[str]) -> str | None:
        """Pick a select/radio option: canned answer first, 'decline' for EEO questions."""
        answer = self.profile.answer_for(question) or self.text_value(question)
        if answer and (choice := match_option(answer, options)):
            return choice
        if _EEO.search(question):
            return next((o for o in options if o and _DECLINE.search(o)), None)
        return None


def match_option(answer: str, options: list[str]) -> str | None:
    """Best option for an answer: exact, then starts-with, then contains (ignoring '+971'-style suffixes)."""
    def norm(t: str) -> str:
        t = (t or "").replace("\u2019", "'").replace("\u2018", "'")
        return re.sub(r"\s*\+\d+$", "", t).strip().lower()

    candidates = [o for o in options if o and not re.match(r"^(select|choose|please select|--)", o.strip(), re.I)]
    a = norm(answer)
    if not a:
        return None
    for test in (lambda o: norm(o) == a, lambda o: norm(o).startswith(a), lambda o: a in norm(o),
                 lambda o: len(norm(o)) > 2 and norm(o) in a):
        for o in candidates:
            if test(o):
                return o
    return None


class BrowserApplier:
    def __init__(self, cfg: dict):
        self.headless = cfg.get("headless", True)
        self.executable = find_chromium(cfg.get("executable_path"))
        self.timeout_ms = int(cfg.get("timeout_seconds", 45)) * 1000
        self.accept_consent = cfg.get("accept_privacy_consent", True)

    def _launch(self, p, headless: bool):
        kwargs = {"headless": headless}
        if self.executable:
            kwargs["executable_path"] = self.executable
        return p.chromium.launch(**kwargs)

    def apply(self, job: Job, profile: Profile, files: dict[str, Path], cover_letter: str,
              submit: bool, screenshot_dir: Path) -> ApplyResult:
        url = application_url(job)
        if not is_supported(url):
            return ApplyResult("skipped", "browser", f"no automatic form support for {urlparse(url).hostname}")
        try:
            from playwright.sync_api import Error as PWError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ApplyResult("skipped", "browser", "playwright not installed (pip install playwright)")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        shot = screenshot_dir / f"{job.id}_form.png"
        try:
            with sync_playwright() as p:
                browser = self._launch(p, self.headless)
                page = browser.new_page()
                page.set_default_timeout(self.timeout_ms)
                result = self.fill_and_submit(page, url, profile, files, cover_letter, submit, shot)
                browser.close()
                return result
        except PWError as exc:
            return ApplyResult("failed", "browser", f"browser error: {str(exc).splitlines()[0]}",
                               str(shot) if shot.exists() else None)

    @staticmethod
    def _settle(page) -> None:
        """Wait until the page's scripts have loaded; filling earlier gets wiped when the app hydrates."""
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:  # noqa: BLE001 - some pages keep polling; carry on
            pass
        page.wait_for_timeout(1000)

    def fill_and_submit(self, page, url, profile, files, cover_letter, submit, shot: Path) -> ApplyResult:
        page.goto(url, wait_until="load")
        try:
            page.wait_for_selector(FORM_SELECTOR, state="attached", timeout=self.timeout_ms)
        except Exception:  # noqa: BLE001
            # Workable shows the job first with an "Apply" button
            btn = page.locator("button:has-text('Apply'), a:has-text('Apply for this job')").first
            if btn.count():
                btn.click()
                page.wait_for_selector(FORM_SELECTOR, state="attached", timeout=self.timeout_ms)
            else:
                page.screenshot(path=str(shot), full_page=True)
                return ApplyResult("needs_manual", "browser", "could not find the application form", str(shot))

        self._settle(page)
        filler = FormFiller(profile, files, cover_letter, self.accept_consent)
        filled = self.fill_fields(page, filler)
        time.sleep(1)
        filled += self.fill_fields(page, filler)  # second pass: fields revealed or reset by the first
        unanswered = self.unanswered_required(page)
        page.screenshot(path=str(shot), full_page=True)
        if unanswered:
            return ApplyResult("needs_manual", "browser",
                               f"filled {filled} fields; unanswered required: {'; '.join(unanswered[:6])}", str(shot))
        if page.evaluate(_CAPTCHA_JS):
            return ApplyResult("needs_manual", "browser", "form filled but a captcha must be solved by you", str(shot))
        if not submit:
            return ApplyResult("needs_manual", "browser", f"form pre-filled ({filled} fields), not submitted", str(shot))

        button = page.locator("button[type=submit], input[type=submit], button:has-text('Submit application'), "
                              "button:has-text('Submit Application'), button:has-text('Submit')").first
        if not button.count():
            return ApplyResult("needs_manual", "browser", "no submit button found", str(shot))
        button.click()
        deadline = time.time() + self.timeout_ms / 1000
        while time.time() < deadline:
            time.sleep(1.5)
            body = page.inner_text("body")[:20000]
            if _SUCCESS.search(body) or re.search(r"/(thanks|thank-you|confirmation|success)", page.url):
                done = shot.with_name(shot.stem.replace("_form", "") + "_submitted.png")
                page.screenshot(path=str(done), full_page=True)
                return ApplyResult("applied", "browser", "submitted - confirmation page seen", str(done))
            if page.evaluate(_CAPTCHA_JS):
                page.screenshot(path=str(shot), full_page=True)
                return ApplyResult("needs_manual", "browser", "captcha challenge appeared on submit", str(shot))
        page.screenshot(path=str(shot), full_page=True)
        errors = page.locator("[class*=error]:visible, [role=alert]:visible").all_inner_texts()[:3]
        detail = "submitted but no confirmation seen" + (f" - page shows: {' | '.join(errors)}" if errors else "")
        return ApplyResult("needs_manual", "browser", detail, str(shot))

    def fill_fields(self, page, filler: FormFiller) -> int:
        fields = page.evaluate(_COLLECT_JS)
        filled = 0
        handled_groups: set[str] = set()
        for f in fields:
            loc = page.locator(f'[data-jobbot-idx="{f["idx"]}"]')
            try:
                if f["type"] == "file":
                    if f["value"]:
                        continue
                    path = filler.file_value(f)
                    hint = f"{f['label']} {f['name']} {f['id']}".lower()
                    if path and Path(path).exists() and (f["required"] or re.search(r"resume|\bcv\b|cover", hint)):
                        loc.set_input_files(str(path))
                        filled += 1
                elif f["type"] == "radio":
                    key = f.get("name") or f.get("group")
                    if key in handled_groups:
                        continue
                    group = [g for g in fields if g["type"] == "radio" and (g.get("name") or g.get("group")) == key]
                    if any(g["checked"] for g in group):
                        handled_groups.add(key)
                        continue
                    choice = filler.choose_option(f.get("group") or f["label"], [g["label"] for g in group])
                    if choice:
                        target = next(g for g in group if g["label"] == choice)
                        page.locator(f'[data-jobbot-idx="{target["idx"]}"]').check(force=True)
                        filled += 1
                    handled_groups.add(key)
                elif f["type"] == "checkbox":
                    if f["checked"]:
                        continue
                    text = f"{f.get('group', '')} {f['label']}"
                    if f["required"] and filler.accept_consent and _CONSENT.search(text):
                        loc.check(force=True)
                        filled += 1
                    elif (ans := filler.profile.answer_for(text)) and ans.strip().lower() in ("yes", "true", "y"):
                        loc.check(force=True)
                        filled += 1
                elif f["tag"] == "select":
                    if f["value"]:
                        continue
                    choice = filler.choose_option(f["label"], f.get("options") or [])
                    if choice:
                        loc.select_option(label=choice)
                        filled += 1
                else:
                    if f["value"]:
                        continue
                    value = filler.text_value(f["label"]) or filler.text_value(f["name"].replace("_", " "))
                    if value is None:
                        continue
                    if f.get("role") == "combobox":
                        filled += self.pick_combobox(page, loc, value)
                    else:
                        loc.fill(value)
                        filled += 1
            except Exception as exc:  # noqa: BLE001 - one odd field must not abort the whole form
                log.debug("could not fill %s: %s", f.get("label"), exc)
        return filled

    @staticmethod
    def pick_combobox(page, loc, answer: str) -> bool:
        """Type into a searchable dropdown and click the matching visible option."""
        options = page.locator('[role="option"]:visible')
        # Search with the full answer, then with its first word (option wording often differs slightly).
        for query in dict.fromkeys([answer[:40], re.split(r"[\s'\u2019]", answer)[0]]):
            loc.click()
            loc.fill("")
            loc.press_sequentially(query, delay=25)
            page.wait_for_timeout(800)
            texts = options.all_inner_texts()
            choice = match_option(answer, texts)
            if choice is not None:
                options.nth(texts.index(choice)).click()
                page.wait_for_timeout(200)
                return True
        loc.fill("")
        page.keyboard.press("Escape")
        return False

    def unanswered_required(self, page) -> list[str]:
        fields = page.evaluate(_COLLECT_JS)
        missing, seen = [], set()
        for f in fields:
            hint = f"{f['label']} {f['name']} {f['id']}".lower()
            if f["type"] == "file" and not f["value"] and re.search(r"resume|\bcv\b", hint) and "cover" not in hint:
                missing.append(f["label"] or "Resume/CV")  # never submit without the CV, marked required or not
                continue
            if not f["required"]:
                continue
            if f["type"] == "radio":
                key = f.get("name") or f.get("group")
                if key in seen:
                    continue
                seen.add(key)
                group = [g for g in fields if g["type"] == "radio" and (g.get("name") or g.get("group")) == key]
                if not any(g["checked"] for g in group):
                    missing.append(f.get("group") or f["label"])
            elif not f["value"]:
                missing.append(f["label"] or f["name"])
        return missing

    def open_prefilled(self, job: Job, profile: Profile, files: dict[str, Path], cover_letter: str) -> None:
        """Open a visible browser with the form pre-filled; you review and press Submit yourself."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = self._launch(p, headless=False)
            page = browser.new_page()
            page.set_default_timeout(self.timeout_ms)
            page.goto(application_url(job), wait_until="load")
            try:
                page.wait_for_selector(FORM_SELECTOR, state="attached", timeout=self.timeout_ms)
                self._settle(page)
                filler = FormFiller(profile, files, cover_letter, self.accept_consent)
                self.fill_fields(page, filler)
                self.fill_fields(page, filler)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not pre-fill the form: %s", exc)
            print("Review the form in the browser window and submit it yourself. Close the window when done.")
            page.wait_for_event("close", timeout=0)
            browser.close()
