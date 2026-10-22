#!/usr/bin/env python3
"""Finder — watches company career pages and alerts you about new matching jobs.

Usage:
  python3 finder.py               # normal run (checks companies that are due this hour)
  python3 finder.py --all         # check every company now
  python3 finder.py --validate    # check every company, write state/status.json, send no alerts
  python3 finder.py --only Google --only Razorpay --no-notify
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from core import sources  # noqa: E402
from core.filters import Matcher  # noqa: E402
from core.notify import send_all  # noqa: E402

STATE = os.path.join(ROOT, "state")
DOCS = os.path.join(ROOT, "docs")
NOW = datetime.now(timezone.utc)
FIRST_RUN_ALERT_DAYS = 3        # on a company's first check, alert only for jobs posted in the last N days
MAX_ALERTS_PER_COMPANY = 15     # guards against a page redesign flooding you
KEEP_DASHBOARD_DAYS = 10      # notifications/dashboard keep 10 days


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def due(company, args):
    if args.only:
        return company["company"].lower() in [o.lower() for o in args.only]
    if args.all or args.validate:
        return True
    every = int(company.get("check_every_hours", 3))
    return NOW.hour % every == 0


def fetch(company, browser=None):
    s = company["source"]
    reader = sources.READERS[s["type"]]
    t0 = time.time()
    if s["type"] == "page":
        jobs = reader(company["company"], s, browser=browser)
    else:
        jobs = reader(company["company"], s)
    return jobs, round(time.time() - t0, 1)


def recent(posted, days):
    if not posted:
        return False
    try:
        d = datetime.fromisoformat(str(posted).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return NOW - d <= timedelta(days=days)
    except (ValueError, TypeError):
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--no-browser", action="store_true", help="read custom pages without a headless browser")
    ap.add_argument("--only", action="append")
    ap.add_argument("--workers", type=int, default=16)
    args = ap.parse_args()

    companies = load(os.path.join(ROOT, "companies.json"), [])
    profile = load(os.path.join(ROOT, "profile.json"), {})
    matcher = Matcher(profile)
    seen = load(os.path.join(STATE, "seen.json"), {})
    known = load(os.path.join(STATE, "companies.json"), {})
    board = load(os.path.join(DOCS, "jobs.json"), [])
    status = {s["company"]: s for s in load(os.path.join(STATE, "status.json"), {}).get("companies", [])}

    todo = [c for c in companies if due(c, args) and c.get("enabled", True)]
    api = [c for c in todo if c["source"]["type"] != "page"]
    pages = [c for c in todo if c["source"]["type"] == "page"]
    log("checking %d companies (%d feeds, %d pages)" % (len(todo), len(api), len(pages)))

    results = {}
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch, c): c for c in api}
        for f in as_completed(futs):
            c = futs[f]
            try:
                results[c["company"]] = (c, f.result(), None)
            except Exception as e:
                results[c["company"]] = (c, None, "%s: %s" % (type(e).__name__, str(e)[:200]))

    if pages:
        browser = pw = None
        if not args.no_browser:
            try:
                from playwright.sync_api import sync_playwright
                pw = sync_playwright().start()
                browser = pw.chromium.launch(headless=True)
            except Exception as e:
                log("headless browser unavailable (%s); reading pages as plain HTML" % e)
        for c in pages:
            try:
                results[c["company"]] = (c, fetch(c, browser), None)
            except Exception as e:
                results[c["company"]] = (c, None, "%s: %s" % (type(e).__name__, str(e)[:200]))
        if browser:
            browser.close()
            pw.stop()

    alerts, new_matches = [], 0
    for name, (c, res, err) in results.items():
        st = {"company": name, "tier": c["tier"], "type": c["source"]["type"],
              "careers_url": c.get("careers_url"), "checked_at": NOW.isoformat()}
        if err:
            st.update(ok=False, error=err, jobs=0, matches=0,
                      last_ok=status.get(name, {}).get("last_ok"))
            status[name] = st
            continue
        jobs, secs = res
        first_time = name not in known
        matched, sent = 0, 0
        for j in jobs:
            if not j["title"] or j["id"] in seen:
                continue
            seen[j["id"]] = NOW.isoformat()
            ok, info = matcher.evaluate(j, c)
            if not ok:
                continue
            matched += 1
            info["found_at"] = NOW.isoformat()
            info["first_check"] = first_time
            board.append(info)
            new_matches += 1
            if (not first_time or recent(j.get("posted"), FIRST_RUN_ALERT_DAYS)) and sent < MAX_ALERTS_PER_COMPANY:
                alerts.append(info)
                sent += 1
        if jobs or not first_time:
            known.setdefault(name, NOW.isoformat())
        st.update(ok=True, error="", jobs=len(jobs), new_matches=matched, seconds=secs, last_ok=NOW.isoformat(),
                  empty=len(jobs) == 0)
        status[name] = st

    # keep files small
    cutoff = (NOW - timedelta(days=KEEP_DASHBOARD_DAYS)).isoformat()
    board = [b for b in board if b.get("found_at", "") >= cutoff]
    board.sort(key=lambda b: b.get("found_at", ""), reverse=True)
    board = board[:4000]
    old = (NOW - timedelta(days=180)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= old}

    ok_n = sum(1 for s in status.values() if s.get("ok"))
    save(os.path.join(STATE, "seen.json"), seen)
    save(os.path.join(STATE, "companies.json"), known)
    save(os.path.join(DOCS, "jobs.json"), board)
    summary = {"updated_at": NOW.isoformat(), "checked": len(results), "ok": ok_n}
    # Full per-company status is private (encrypted in the repo); the public dashboard gets only the summary.
    save(os.path.join(STATE, "status.json"), dict(summary, companies=sorted(
        status.values(), key=lambda s: (s.get("ok", False), s["tier"], s["company"]))))
    save(os.path.join(DOCS, "status.json"), summary)

    errs = [n for n, (_, _, e) in results.items() if e]
    log("done: %d ok, %d errors, %d new matches, %d alerts" % (len(results) - len(errs), len(errs),
                                                              new_matches, len(alerts)))
    if args.validate:
        print("\n%-32s %-15s %6s  %s" % ("COMPANY", "TYPE", "JOBS", "RESULT"))
        for s in sorted(status.values(), key=lambda s: (s.get("ok", False), s["company"])):
            if s["company"] in results:
                print("%-32s %-15s %6s  %s" % (s["company"][:32], s["type"], s.get("jobs", 0),
                                              "ok" if s.get("ok") else s.get("error", "")[:80]))
    elif not args.no_notify:
        send_all(alerts, log)


if __name__ == "__main__":
    main()
