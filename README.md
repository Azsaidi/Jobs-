# jobbot: UAE job search on autopilot

jobbot finds open roles in the UAE that fit your profile and tailors an ATS-optimised CV and cover letter for each one. It applies for you where that can be done reliably, notifies you by email, Telegram or phone push, and tracks every application.

```
 find ──► filter ──► score fit ──► tailor CV + cover letter ──► apply ──► notify you ──► track
 company boards   UAE only,      keywords, then      PDF + DOCX, ATS-checked,     email or    digest      SQLite +
 + aggregators    nationals-only  Claude reads       never invents facts          web form                dashboard
                  roles, age...   the whole ad
```

## What it does

| Step | How |
|---|---|
| **Find** | Public job-board APIs of the ATSs UAE employers use (Greenhouse, Lever, Ashby, Workable), plus optional aggregators (JSearch, SerpApi) that cover LinkedIn, Bayt, Indeed, GulfTalent and company sites through Google for Jobs. Also takes any RSS feed. |
| **Filter** | Keeps only roles in the seven emirates (remote optional). Skips roles reserved for UAE nationals (Emiratisation) unless you are Emirati, and flags ads that require Arabic you don't list. Also drops excluded titles and companies, stale postings, and duplicates of the same role found through several sources. |
| **Score** | A fast keyword and title score, then Claude reads each remaining ad against your master CV. It scores fit from 0 to 100, lists strengths, gaps and deal-breakers, and drops weak matches. |
| **Tailor** | Claude rewrites your CV for the job: it mirrors the ad's terminology where that's true, re-orders skills and bullets, and writes the summary and a cover letter. The output is checked against your master CV automatically (see [No invented facts](#no-invented-facts)). It renders a one-column, ATS-safe PDF and DOCX and reports ATS keyword coverage before and after. |
| **Apply** | **Email** when the ad says "send your CV to ...", which is common in the UAE: it sends from your own address with the CV and cover letter attached and the ad's reference code in the subject. **Web form** on Greenhouse, Lever, Ashby and Workable: it fills your details, uploads the CV, answers screening questions from your answer bank, and submits only when every required field is answered and there's no captcha. Anything else is left ready for you to finish. |
| **Notify** | One digest per run: applied, ready for your review, and needs you. Sent by email, Telegram and/or ntfy push. |
| **Track** | SQLite tracker with full history, CLI status updates (interview, offer, rejected...), CSV export and an HTML dashboard. |

### What it deliberately does not do

- **It doesn't log into LinkedIn, Bayt, Indeed or Naukrigulf, or submit there.** Automating those accounts breaks their terms and gets accounts banned. jobbot still *finds* their listings through the aggregators and prepares the tailored CV, and the digest gives you the link.
- **It doesn't solve captchas, or create accounts on Workday, Taleo, SuccessFactors or Oracle portals.** Those applications end as `needs_manual`, with your files ready. `jobbot open <id>` opens supported forms pre-filled so you only review and click Submit.
- **It doesn't invent experience.** See below.

## Quick start

```bash
git clone <this repo> && cd Jobs-
python -m venv .venv && source .venv/bin/activate
pip install -e ".[browser,import]"
playwright install chromium        # only needed for web-form applications

jobbot init                        # creates config.yaml, profile.yaml, .env (all git-ignored)
jobbot import-cv ~/my_cv.pdf --force   # optional: drafts profile.yaml from your CV (needs ANTHROPIC_API_KEY)
# edit profile.yaml, config.yaml (search.keywords) and .env
jobbot doctor                      # checks everything is wired up
jobbot run --preview               # shows what matches right now; records and sends nothing
jobbot run                         # the real thing (review mode by default)
```

### 1. `profile.yaml` is your master CV

This is the **only source of facts** for every tailored CV, so make it complete: every role, every real achievement (with numbers), every skill and tool, plus certifications and languages. Tailoring selects and re-words; it can't add anything that isn't here.

The UAE-specific fields (`nationality`, `visa_status`, `notice_period`, `driving_license`) appear as a details line on the CV, as UAE recruiters expect. Turn that line off with `tailoring.show_personal_details: false`.

`application_answers` is your answer bank for screening questions. Each entry is a regex matched against the question text, and the first match wins, so put specific rules before general ones (e.g. `sponsor` before `visa`). A question with no answer means jobbot won't submit that form and leaves it for you.

### 2. `config.yaml`

- `search.keywords`: the job titles you want. Use several variants.
- `sources`: which boards to read. For company boards, the value is the name in the company's job URL:
  - `job-boards.greenhouse.io/careem` → greenhouse board `careem`
  - `jobs.lever.co/<site>` → lever site
  - `jobs.ashbyhq.com/<org>` → ashby org
  - `apply.workable.com/<account>` → workable account

  The defaults were checked to have UAE openings, but they lean toward tech and fintech. For broad coverage in any field, enable `jsearch` or `serpapi` (both have free tiers).
- `apply.mode`:
  - `review` (default): everything is prepared and you get a digest. Approve with `jobbot apply <id>`.
  - `auto`: submits on its own when fit ≥ `min_score_to_auto_apply`, up to `daily_limit` per 24h.
  - `off`: only finds and tailors.

  Start with `review` for a week, read what it produces, then switch to `auto` if you trust it.

### 3. `.env` secrets

| Variable | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | AI fit scoring, tailoring, cover letters, `import-cv`. Without it, jobbot falls back to keyword matching and keyword-ordered CVs with a template cover letter. |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | Email applications (sent from **your** address) and email digests. For Gmail, use an App Password. |
| `RAPIDAPI_KEY` / `SERPAPI_KEY` | The aggregator sources. |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, or `notify.ntfy.topic` | Phone notifications. |

Test notifications with `jobbot test-notify`.

## Daily use

```bash
jobbot run                      # find + tailor (+ apply in auto mode) + notify
jobbot list                     # all applications;  --status prepared|applied|needs_manual|interview...
jobbot show 12                  # details, history and paths; output/applications/<job>/job.md has the full analysis
jobbot apply 12 15              # approve and submit (email or web form)
jobbot apply 12 --no-submit     # fill the form / draft the email without sending (screenshot saved)
jobbot open 12                  # open the form pre-filled in a visible browser; you click Submit
jobbot tailor 12                # regenerate the CV after editing profile.yaml
jobbot status 12 interview --note "Call with HM on Tuesday"
jobbot status 12 rejected
jobbot report                   # output/dashboard.html
jobbot export --out apps.csv
```

Each application gets a folder under `output/applications/` containing:

- the tailored CV as PDF and DOCX, named `Name_Company_Role_CV.pdf`
- the cover letter as PDF and TXT (the TXT is handy for pasting into forms)
- `job.md`: the ad, Claude's assessment, ATS coverage before and after, the missing keywords, and any warnings

### Run it automatically

**cron**, every day at 8:00 and 17:00 UAE time if your machine runs on UAE time:

```cron
0 8,17 * * *  cd /path/to/Jobs- && .venv/bin/jobbot run >> data/cron.log 2>&1
```

**Windows**: in Task Scheduler, create a task that runs `C:\path\to\Jobs-\.venv\Scripts\jobbot.exe run` with "Start in" set to the project folder.

## No invented facts

ATS optimisation must never mean lying on a CV, because that gets offers withdrawn. Claude's tailored CV is validated before anything is rendered:

- Company, title, location and dates always come from your profile, never from the model.
- Every bullet must cite the source bullet(s) it is based on, from the same role. A bullet without a valid source is dropped.
- Any number that doesn't appear in the cited source text (e.g. "500 users" when the source says 120) reverts the bullet to your original wording.
- Skills must exist in your profile, spelled the same way. Anything else is removed. Skills the model left out are kept under *Additional Skills*.
- The summary and cover letter may only use numbers from your profile, or your computed years of experience. Otherwise jobbot falls back to your own summary or the template letter.
- Keywords you genuinely lack are listed under "Missing" in `job.md` instead of being slipped into the CV.

Every correction is recorded in the application's notes and in `job.md`.

## Costs and privacy

- Claude usage is per job that passes the keyword filter: one fit assessment, then one tailoring call for the jobs you go ahead with. Your master CV is prompt-cached across a run. Limit spend with `matching.max_llm_assessments_per_run` and `tailoring.max_per_run`. To trade quality for cost, change `llm.model` (e.g. `claude-sonnet-5-5`) or `llm.effort`.
- Everything is stored locally: the profile, the SQLite tracker and the generated files. `config.yaml`, `profile.yaml`, `.env`, `data/` and `output/` are git-ignored; keep them out of commits.

## Development

```bash
pip install -e ".[browser,import,dev]"
pytest
```

The browser tests drive a local HTML form, so they need Chromium. They are skipped when Playwright or Chromium is missing. Set `JOBBOT_CHROMIUM` to use an existing Chromium binary.

Layout:

```
jobbot/
  sources/        ats_boards.py (Greenhouse/Lever/Ashby/Workable), aggregators.py (JSearch/SerpApi/RSS)
  matching.py     UAE filters, keyword + title score, Claude fit assessment
  ats.py          keyword extraction, coverage scoring, CV lint
  tailor.py       Claude tailoring + no-fabrication validator, keyword fallback
  render.py       ATS-safe PDF/DOCX CV and cover letter
  apply/          email_apply.py, browser_apply.py (Playwright form filler)
  notify.py       digest via email / Telegram / ntfy
  db.py           SQLite tracker;  report.py  HTML dashboard
  pipeline.py     orchestration;   cli.py     commands
```
