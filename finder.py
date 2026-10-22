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
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from core import sources  # noqa: E402
from core.filters import Matcher  # noqa: E402
from core.notify import send_all  # noqa: E402
from core.ai import AIReviewer  # noqa: E402

STATE = os.path.join(ROOT, "state")
DOCS = os.path.join(ROOT, "docs")
NOW = datetime.now(timezone.utc)
FIRST_RUN_ALERT_DAYS = 3        # on a company's first check, alert only for jobs posted in the last N days
MAX_ALERTS_PER_COMPANY = 15     # guards against a page redesign flooding you
KEEP_DASHBOARD_DAYS = 10      # notifications/dashboard keep 10 days
BLOCKED_RX = re.compile(r"403|429|timed out|Timeout|Connection reset|RemoteDisconnected", re.I)
MAX_PAGE_DETAILS = 300          # custom career pages: open at most this many job pages per run in the browser


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


def read_descriptions(cands, browser, workers=16):
    """Fetch the full description for each (company, job) that lacks one. Custom pages use the browser."""
    need = [(c, j) for c, j in cands if len(j.get("desc") or "") < 300]
    plain = [(c, j) for c, j in need if c["source"]["type"] != "page"]
    pages = [(c, j) for c, j in need if c["source"]["type"] == "page"]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(sources.describe, c, j): j for c, j in plain}
        for f in as_completed(futs):
            try:
                if not f.result():
                    futs[f]["desc_missing"] = True
            except Exception:
                futs[f]["desc_missing"] = True
    for i, (c, j) in enumerate(pages):
        ok = False
        if i < MAX_PAGE_DETAILS:
            try:
                ok = sources.describe(c, j, browser)
            except Exception:
                ok = False
        if not ok:
            j["desc_missing"] = True
    missing = sum(1 for _, j in need if j.get("desc_missing"))
    log("descriptions: %d needed, %d unreadable" % (len(need), missing))


def ai_pass(items, reviewer, cache):
    """items: list of (info, desc). AI double-checks jobs the rules marked open/stretch. Returns kept infos."""
    order = sorted(items, key=lambda x: ({"open": 0, "stretch": 1}.get(x[0].get("fit"), 9), -x[0].get("priority", 0)))
    asked = dropped = 0
    for info, desc in order:
        if info.get("fit") not in ("open", "stretch") or len(desc or "") < 300:
            continue
        res = cache.get(info["id"])
        if res is None:
            if not reviewer.available():
                continue
            res = reviewer.judge(info["title"], info["company"], info.get("location", ""), desc)
            if res is None:
                continue
            asked += 1
            cache[info["id"]] = dict(res, at=NOW.isoformat())
        if res["fit"] == "reject":
            info["_drop"] = True
            dropped += 1
            continue
        if not (info["fit"] == "stretch" and res["fit"] in ("open", "fresher")):
            info["fit"] = res["fit"]
        info["ai_reason"] = res.get("reason", "")
        if res.get("role_type") and res["role_type"] != "Other":
            info["role_type"] = res["role_type"]
        learn = list(dict.fromkeys((res.get("skills_to_learn") or []) + (info.get("skills_to_learn") or [])))
        info["skills_to_learn"] = learn[:6]
    if asked or dropped:
        log("ai review: asked %d, removed %d (budget used %d/%d)" % (asked, dropped, reviewer.used, reviewer.budget))
    return [info for info, _ in items if not info.pop("_drop", False)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--no-notify", action="store_true")
    ap.add_argument("--no-browser", action="store_true", help="read custom pages without a headless browser")
    ap.add_argument("--recheck", action="store_true", help="re-judge every job already on the dashboard")
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
    ai_cache = load(os.path.join(STATE, "ai.json"), {})
    reviewer = AIReviewer(profile, log)

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

    browser = pw = None
    if (pages or args.recheck or any(e and BLOCKED_RX.search(e) for _, _, e in results.values())) \
            and not args.no_browser:
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            # --disable-http2: some sites (Policybazaar, Tata Digital) reset HTTP/2 connections from headless Chrome
            browser = pw.chromium.launch(headless=True, args=["--disable-http2",
                                                              "--disable-blink-features=AutomationControlled"])
        except Exception as e:
            log("headless browser unavailable (%s); reading pages as plain HTML" % e)
    for c in pages:
        try:
            results[c["company"]] = (c, fetch(c, browser), None)
        except Exception as e:
            results[c["company"]] = (c, None, "%s: %s" % (type(e).__name__, str(e)[:200]))

    # retry feeds that blocked the plain HTTP client, this time from inside a real browser tab on the careers site
    if browser:
        blocked = [c for name, (c, res, err) in results.items()
                   if err and BLOCKED_RX.search(err) and c["source"]["type"] != "page" and c.get("careers_url")]
        for c in blocked:
            pg = browser.new_page(user_agent=sources.UA.replace(" FinderJobBot/1.0", ""))
            try:
                pg.goto(c["careers_url"], wait_until="domcontentloaded", timeout=45000)
                pg.wait_for_timeout(3000)
                sources.VIA["page"] = pg
                results[c["company"]] = (c, fetch(c), None)
                log("browser retry worked: %s" % c["company"])
            except Exception as e:
                err = results[c["company"]][2]
                results[c["company"]] = (c, None, "%s (browser retry: %s)" % (err, str(e).splitlines()[0][:80]))
            finally:
                sources.VIA["page"] = None
                pg.close()

    by_name = {c["company"]: c for c in companies}
    if args.recheck:
        # re-judge every job already on the dashboard with the current rules (re-reads each description)
        old = board
        board = []
        cands = [(by_name[b["company"]], dict(b, desc="")) for b in old if b.get("company") in by_name]
        log("rechecking %d dashboard jobs" % len(cands))
        read_descriptions(cands, browser, args.workers)
        items = []
        for c, j in cands:
            desc = j.get("desc") or ""
            ok, info = matcher.evaluate(j, c)
            if ok:
                info["found_at"] = j.get("found_at")
                items.append((info, desc))
        board = ai_pass(items, reviewer, ai_cache)
        log("recheck kept %d of %d" % (len(board), len(old)))

    alerts, new_matches = [], 0
    cands, first = [], {}
    for name, (c, res, err) in results.items():
        st = {"company": name, "tier": c["tier"], "type": c["source"]["type"],
              "careers_url": c.get("careers_url"), "checked_at": NOW.isoformat()}
        if err:
            st.update(ok=False, error=err, jobs=0, matches=0,
                      last_ok=status.get(name, {}).get("last_ok"))
            status[name] = st
            continue
        jobs, secs = res
        first[name] = name not in known
        for j in jobs:
            if not j["title"] or j["id"] in seen:
                continue
            seen[j["id"]] = NOW.isoformat()
            if matcher.prefilter(j, c)[0]:
                cands.append((c, j))
        if jobs or not first[name]:
            known.setdefault(name, NOW.isoformat())
        st.update(ok=True, error="", jobs=len(jobs), seconds=secs, last_ok=NOW.isoformat(), empty=len(jobs) == 0)
        status[name] = st

    if cands:
        log("reading %d job descriptions" % len(cands))
        read_descriptions(cands, browser, args.workers)
    if browser:
        browser.close()
        pw.stop()

    sent, matched, rejected = {}, {}, {}
    items = []
    for c, j in cands:
        desc = j.get("desc") or ""
        ok, info = matcher.evaluate(j, c)
        if not ok:
            rejected[c["company"]] = rejected.get(c["company"], 0) + 1
            continue
        info["found_at"] = NOW.isoformat()
        info["first_check"] = first.get(c["company"], False)
        items.append((info, desc))
    kept = ai_pass(items, reviewer, ai_cache)
    for info in kept:
        name = info["company"]
        matched[name] = matched.get(name, 0) + 1
        board.append(info)
        new_matches += 1
        if (not first.get(name) or recent(info.get("posted"), FIRST_RUN_ALERT_DAYS)) and \
                sent.get(name, 0) < MAX_ALERTS_PER_COMPANY:
            alerts.append(info)
            sent[name] = sent.get(name, 0) + 1
    # spare AI budget: double-check older dashboard jobs that were never reviewed (best first)
    if reviewer.available():
        todo_ai = sorted([b for b in board if b.get("fit") in ("open", "stretch") and not b.get("ai_reason")
                          and not b.get("ai_skip") and b.get("company") in by_name],
                         key=lambda b: -b.get("priority", 0))[:reviewer.budget - reviewer.used]
        if todo_ai:
            pairs = [(by_name[b["company"]], dict(b, desc="")) for b in todo_ai]
            read_descriptions(pairs, None, args.workers)
            for b, (_, j) in zip(todo_ai, pairs):
                if len(j.get("desc") or "") < 300:
                    b["ai_skip"] = True
            ai_pass([(b, j.get("desc", "")) for b, (_, j) in zip(todo_ai, pairs)], reviewer, ai_cache)
            board = [b for b in board if not (b["id"] in ai_cache and ai_cache[b["id"]]["fit"] == "reject")]

    for name in status:
        if name in results and status[name].get("ok"):
            status[name]["new_matches"] = matched.get(name, 0)
            status[name]["rejected_by_details"] = rejected.get(name, 0)

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
    ai_old = (NOW - timedelta(days=30)).isoformat()
    save(os.path.join(STATE, "ai.json"), {k: v for k, v in ai_cache.items() if v.get("at", "") >= ai_old})
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
