"""Command-line interface: `jobbot <command>`."""

from __future__ import annotations

import argparse
import csv
import logging
import shutil
import sys
from importlib import resources
from pathlib import Path

from .config import load_config
from .db import STATUSES, Tracker
from .profile import Profile, ProfileError


def _ctx(args, need_profile: bool = True):
    cfg = load_config(args.config)
    tracker = Tracker(cfg.path("storage", "db_path"))
    profile = None
    if need_profile:
        try:
            profile = Profile.load(cfg.path("storage", "profile"))
        except ProfileError as exc:
            sys.exit(f"error: {exc}")
    return cfg, tracker, profile


def _pipeline(cfg, tracker, profile, with_llm: bool = True):
    from .llm import make_llm
    from .notify import Notifier
    from .pipeline import Pipeline

    return Pipeline(cfg, profile, tracker, llm=make_llm(cfg) if with_llm else None,
                    notifier=Notifier(cfg, fallback_email=profile.personal.get("email")))


def _get_app(tracker: Tracker, app_id: int) -> dict:
    app = tracker.application(app_id)
    if app is None:
        sys.exit(f"error: no application #{app_id} (see `jobbot list`)")
    return app


# ---- commands -----------------------------------------------------------------

def cmd_init(args):
    target = Path(args.dir)
    target.mkdir(parents=True, exist_ok=True)
    templates = resources.files("jobbot") / "templates"
    for src, dst in (("config.example.yaml", "config.yaml"), ("profile.example.yaml", "profile.yaml"),
                     ("env.example", ".env")):
        out = target / dst
        if out.exists():
            print(f"exists, left unchanged: {out}")
            continue
        with resources.as_file(templates / src) as path:
            shutil.copy(path, out)
        print(f"created {out}")
    print("\nNext: fill in profile.yaml (or run `jobbot import-cv your_cv.pdf`), set search keywords in "
          "config.yaml, add secrets to .env, then `jobbot doctor` and `jobbot run --preview`.")


def cmd_import_cv(args):
    from .import_cv import import_cv
    from .llm import LLMError, make_llm

    cfg = load_config(args.config)
    llm = make_llm(cfg)
    if llm is None:
        sys.exit("error: import-cv needs ANTHROPIC_API_KEY (in .env). Or copy profile.yaml from the template "
                 "and fill it in by hand.")
    out = Path(args.out) if args.out else cfg.path("storage", "profile")
    if out.exists() and not args.force:
        sys.exit(f"error: {out} exists - pass --force to overwrite or --out to write elsewhere")
    try:
        import_cv(llm, Path(args.cv), out)
    except (LLMError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")
    print(f"wrote {out} - review it carefully; it is the only source of facts for your tailored CVs")


def cmd_run(args):
    cfg, tracker, profile = _ctx(args)
    if args.preview:
        # Preview against a throwaway database so nothing is recorded or sent.
        from .pipeline import Pipeline
        from .llm import make_llm

        tracker.close()
        pipeline = Pipeline(cfg, profile, Tracker(":memory:"), llm=make_llm(cfg) if args.llm else None)
        summary = pipeline.run(apply=False, notify=False, tailor=False)
        print(f"Fetched {summary.fetched} postings; {len(summary.prepared)} match your profile "
              f"({summary.filtered} filtered out).\n")
        for a in summary.prepared[: args.limit]:
            score = a["llm_score"] if a["llm_score"] is not None else a["match_score"]
            print(f"  [{round(score):>3}] {a['title']} - {a['company']}\n        {a['apply_url'] or a['url']}")
        for e in summary.errors:
            print(f"  ! {e}")
        return
    pipeline = _pipeline(cfg, tracker, profile)
    from .notify import digest_text

    summary = pipeline.run(apply=not args.no_apply, notify=not args.no_notify)
    print(digest_text(summary))


def cmd_list(args):
    _, tracker, _ = _ctx(args, need_profile=False)
    apps = tracker.applications(args.status)
    if not apps:
        print("no applications yet" + (f" with status {args.status}" if args.status else ""))
        return
    print(f"{'#':>4}  {'status':<12} {'fit':>3}  {'ats':>9}  {'role':<45} company")
    for a in apps:
        score = a["llm_score"] if a["llm_score"] is not None else a["match_score"]
        ats = f"{a['ats_before'] or 0:.0f}->{a['ats_after'] or 0:.0f}"
        print(f"{a['id']:>4}  {a['status']:<12} {round(score or 0):>3}  {ats:>9}  {a['title'][:45]:<45} {a['company']}")


def cmd_show(args):
    _, tracker, _ = _ctx(args, need_profile=False)
    a = _get_app(tracker, args.id)
    for key in ("title", "company", "location", "status", "llm_score", "match_score", "match_summary",
                "ats_before", "ats_after", "missing_keywords", "url", "apply_url", "cv_pdf", "cv_docx",
                "cover_letter", "channel", "applied_at", "notes"):
        if a.get(key) not in (None, ""):
            print(f"{key:>16}: {a[key]}")
    print("\nHistory:")
    for e in tracker.events(args.id):
        print(f"  {e['at']}  {e['kind']}: {e['detail']}")
    if a.get("cv_pdf"):
        print(f"\nFull analysis: {Path(a['cv_pdf']).parent / 'job.md'}")


def cmd_apply(args):
    cfg, tracker, profile = _ctx(args)
    pipeline = _pipeline(cfg, tracker, profile, with_llm=False)
    for app_id in args.ids:
        a = _get_app(tracker, app_id)
        if a["status"] == "applied" and not args.force:
            print(f"#{app_id} already applied ({a['channel']}) - pass --force to apply again")
            continue
        result = pipeline.apply_application(app_id, submit=not args.no_submit,
                                            channels=[args.channel] if args.channel else None)
        print(f"#{app_id} {a['title']} - {a['company']}: {result.status} via {result.channel} - {result.detail}")
        if result.screenshot:
            print(f"      screenshot: {result.screenshot}")


def cmd_open(args):
    cfg, tracker, profile = _ctx(args)
    from .apply import BrowserApplier

    pipeline = _pipeline(cfg, tracker, profile, with_llm=False)
    a = _get_app(tracker, args.id)
    files, letter = pipeline.application_files(a)
    BrowserApplier(cfg["apply"].get("browser") or {}).open_prefilled(tracker.get_job(a["job_id"]), profile,
                                                                     files, letter)
    if input("Did you submit the application? [y/N] ").strip().lower().startswith("y"):
        from .util import now_iso

        tracker.update_application(args.id, status="applied", channel="manual", applied_at=now_iso())
        print("marked as applied")


def cmd_tailor(args):
    cfg, tracker, profile = _ctx(args)
    pipeline = _pipeline(cfg, tracker, profile)
    a = _get_app(tracker, args.id)
    pipeline.prepare(tracker.get_job(a["job_id"]))
    if a["status"] not in ("prepared", "needs_manual", "failed"):
        tracker.update_application(args.id, status=a["status"])
    a = tracker.application(args.id)
    print(f"re-tailored #{args.id}: ATS {a['ats_before']} -> {a['ats_after']}\n  {a['cv_pdf'] or a['cv_docx']}")


def cmd_status(args):
    _, tracker, _ = _ctx(args, need_profile=False)
    _get_app(tracker, args.id)
    fields = {"status": args.status}
    if args.status == "applied":
        from .util import now_iso

        fields["applied_at"] = now_iso()
    tracker.update_application(args.id, **fields)
    if args.note:
        tracker.log(args.id, "note", args.note)
    print(f"#{args.id} -> {args.status}")


def cmd_export(args):
    _, tracker, _ = _ctx(args, need_profile=False)
    cols = ["id", "status", "title", "company", "location", "llm_score", "match_score", "ats_before", "ats_after",
            "channel", "applied_at", "url", "cv_pdf", "notes", "created_at", "updated_at"]
    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(tracker.applications())
    print(f"wrote {out}")


def cmd_report(args):
    cfg, tracker, _ = _ctx(args, need_profile=False)
    from .report import build_dashboard

    path = build_dashboard(tracker, Path(args.out) if args.out else cfg.path("storage", "output_dir") / "dashboard.html")
    print(f"dashboard: {path}")


def cmd_test_notify(args):
    cfg, _, profile = _ctx(args)
    from .notify import Notifier

    notifier = Notifier(cfg, fallback_email=profile.personal.get("email"))
    if not notifier.channels():
        sys.exit("no notification channel enabled in config.yaml (notify.email / telegram / ntfy)")
    errors = notifier.send("jobbot test notification", "If you can read this, jobbot can reach you.")
    print("\n".join(errors) if errors else f"sent via {', '.join(notifier.channels())}")


def cmd_doctor(args):
    from . import mailer
    from .apply.browser_apply import find_chromium
    from .llm import ClaudeLLM

    cfg = load_config(args.config)
    ok = True

    def check(cond: bool, good: str, bad: str, required: bool = False):
        nonlocal ok
        print(("  ok   " if cond else ("  FAIL " if required else "  --   ")) + (good if cond else bad))
        ok = ok and (cond or not required)

    print(f"config: {Path(args.config).resolve()}")
    try:
        profile = Profile.load(cfg.path("storage", "profile"))
        check(True, f"profile: {profile.name}, {len(profile.experience)} roles, {len(profile.all_skills())} skills", "")
        check(bool(profile.application_answers), "application answers present",
              "no application_answers in profile - forms with screening questions will need you")
    except ProfileError as exc:
        check(False, "", f"profile: {exc}", required=True)
    check(bool(cfg["search"].get("keywords")), f"search keywords: {', '.join(cfg['search']['keywords'])}",
          "search.keywords is empty - set the job titles you want", required=True)
    enabled = [n for n, s in cfg["sources"].items() if (s or {}).get("enabled")]
    check(bool(enabled), f"sources enabled: {', '.join(enabled)}", "no job sources enabled", required=True)
    if cfg["sources"]["jsearch"].get("enabled"):
        check(bool(cfg.env("RAPIDAPI_KEY")), "RAPIDAPI_KEY set", "jsearch enabled but RAPIDAPI_KEY missing", True)
    if cfg["sources"]["serpapi"].get("enabled"):
        check(bool(cfg.env("SERPAPI_KEY")), "SERPAPI_KEY set", "serpapi enabled but SERPAPI_KEY missing", True)
    check(ClaudeLLM.available(), "Claude API key set - AI fit scoring and tailoring on",
          "ANTHROPIC_API_KEY not set - keyword-only matching and tailoring")
    check(mailer.smtp_configured(), "SMTP configured - can email applications and notifications",
          "SMTP not configured - no email applications or email notifications")
    try:
        import playwright  # noqa: F401
        check(True, f"playwright installed (chromium: {find_chromium(cfg['apply']['browser'].get('executable_path')) or 'playwright default'})", "")
    except ImportError:
        check(False, "", "playwright not installed - online forms will be left for you")
    print(f"  apply mode: {cfg['apply']['mode']} (daily limit {cfg['apply']['daily_limit']})")
    sys.exit(0 if ok else 1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="jobbot", description="Find, tailor, apply and track UAE job applications.")
    parser.add_argument("-c", "--config", default="config.yaml", help="path to config.yaml (default: ./config.yaml)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create config.yaml, profile.yaml and .env from templates")
    p.add_argument("dir", nargs="?", default=".")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("import-cv", help="draft profile.yaml from your existing CV (PDF/DOCX) using Claude")
    p.add_argument("cv")
    p.add_argument("--out")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_import_cv)

    p = sub.add_parser("doctor", help="check configuration and credentials")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("run", help="find jobs, tailor CVs, apply (per apply.mode) and notify you")
    p.add_argument("--preview", action="store_true", help="only show matching jobs; record and send nothing")
    p.add_argument("--llm", action="store_true", help="with --preview: also run Claude's fit assessment")
    p.add_argument("--limit", type=int, default=50, help="with --preview: how many matches to print")
    p.add_argument("--no-apply", action="store_true", help="prepare CVs but don't submit anything")
    p.add_argument("--no-notify", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("list", help="list tracked applications")
    p.add_argument("--status", choices=STATUSES)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="details and history of one application")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("apply", help="approve and submit prepared application(s)")
    p.add_argument("ids", type=int, nargs="+")
    p.add_argument("--channel", choices=["email", "browser"])
    p.add_argument("--no-submit", action="store_true", help="fill forms / draft emails without sending")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("open", help="open the application form pre-filled in a visible browser")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("tailor", help="regenerate the tailored CV for an application")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_tailor)

    p = sub.add_parser("status", help="update an application's status (interview, offer, rejected...)")
    p.add_argument("id", type=int)
    p.add_argument("status", choices=STATUSES)
    p.add_argument("--note")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("export", help="export applications to CSV")
    p.add_argument("--out", default="applications.csv")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("report", help="write an HTML dashboard of your applications")
    p.add_argument("--out")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("test-notify", help="send a test notification")
    p.set_defaults(func=cmd_test_notify)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    args.func(args)


if __name__ == "__main__":
    main()
