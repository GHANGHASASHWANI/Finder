#!/usr/bin/env python3
"""Telegram bot: send it a job link, get back a tailored PDF resume + referral messages.

  python bot.py --check   # exit 0 and print has_work=true/false (cheap; no browser) for GitHub Actions
  python bot.py           # answer every new message from your chat

Runs every ~10 minutes in .github/workflows/bot.yml. Only messages from TELEGRAM_CHAT_ID are answered.
Telegram keeps unanswered messages for 24 hours, so nothing is lost if a run is late.
"""
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from core import sources, referral  # noqa: E402
from core import resume as cv  # noqa: E402
from core.ai import AIReviewer  # noqa: E402
from core.notify import _tg  # noqa: E402

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
URL_RX = re.compile(r"https?://[^\s<>\"']+")
HELP = ("Send me a job link (one or more per message) and I'll reply with:\n"
        "1. a resume tailored to that job (PDF, every keyword I can cover)\n"
        "2. referral messages: LinkedIn note, DM, and lines for the referrer's form\n\n"
        "Replies take ~10-20 minutes (I check every 10 minutes).")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def updates():
    d = json.loads(urllib.request.urlopen(
        "https://api.telegram.org/bot%s/getUpdates?timeout=0&allowed_updates=%%5B%%22message%%22%%5D" % TOKEN,
        timeout=30).read())
    out = []
    for u in d.get("result", []):
        m = u.get("message") or {}
        if str((m.get("chat") or {}).get("id")) == CHAT:
            out.append((u["update_id"], m.get("text") or m.get("caption") or ""))
        else:
            out.append((u["update_id"], None))      # someone else: ignore, but mark as read
    return out


def confirm(last_id):
    urllib.request.urlopen("https://api.telegram.org/bot%s/getUpdates?timeout=0&offset=%d" % (TOKEN, last_id + 1),
                           timeout=30).read()


def say(text):
    _tg(TOKEN, "sendMessage", {"chat_id": CHAT, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})


# ------------------------------------------------------------------ read a job from its link

def _companies():
    try:
        with open(os.path.join(ROOT, "companies.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com", "workable.com")


def company_for(url):
    """Company name from our list: ATS links match on the board id, other links on the careers-site host."""
    u = urllib.parse.urlparse(url)
    host = u.netloc.lower()
    parts = [p.lower() for p in u.path.split("/") if p]
    board = host.split(".")[0] if "myworkdayjobs.com" in host else (parts[0] if parts else "")
    if "greenhouse.io" in host and parts and parts[0] == "embed":
        board = urllib.parse.parse_qs(u.query).get("for", [""])[0].lower()
    on_ats = any(h in host for h in ATS_HOSTS)
    for c in _companies():
        s = c.get("source", {})
        if on_ats:
            ids = [str(s.get(k, "")).lower() for k in ("tenant", "token", "slug", "org", "id")]
            if board and board in ids:
                return c["company"]
        else:
            hosts = {urllib.parse.urlparse(c.get(k) or s.get("url") or "").netloc.lower()
                     for k in ("careers_url", "feed_url")} | {str(s.get("host", "")).lower()}
            if host in hosts or host.replace("www.", "") in {h.replace("www.", "") for h in hosts}:
                return c["company"]
    name = board if on_ats else host.replace("www.", "").replace("careers.", "").replace("jobs.", "").split(".")[0]
    return name.replace("-", " ").title()


def read_job(url, browser=None):
    """Returns dict(title, company, location, url, desc). Uses each ATS's API, then the page itself."""
    u = urllib.parse.urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    job = {"url": url, "company": company_for(url), "title": "", "location": "", "desc": ""}
    gj = lambda x, **k: sources.getjson(x, **k)
    try:
        if "myworkdayjobs.com" in host and "job" in parts:
            tenant = host.split(".")[0]
            i = parts.index("job")
            site = parts[i - 1]
            d = gj("https://%s/wday/cxs/%s/%s/%s" % (host, tenant, site, "/".join(parts[i:])),
                   headers={"Accept": "application/json"})["jobPostingInfo"]
            job.update(title=d.get("title", ""), location=d.get("location", ""), desc=sources.strip_html(d.get("jobDescription")))
        elif "greenhouse.io" in host:
            jid = urllib.parse.parse_qs(u.query).get("gh_jid", [None])[0] or parts[-1]
            token = parts[0] if parts and parts[0] != "embed" else urllib.parse.parse_qs(u.query).get("for", [""])[0]
            d = gj("https://boards-api.greenhouse.io/v1/boards/%s/jobs/%s" % (token, jid))
            offices = "; ".join(o.get("name", "") for o in d.get("offices") or [])
            job.update(title=d.get("title", ""), location=offices or (d.get("location") or {}).get("name", ""),
                       desc=sources.strip_html(d.get("content")))
        elif "lever.co" in host and len(parts) >= 2:
            d = gj("https://api.lever.co/v0/postings/%s/%s" % (parts[0], parts[1]))
            job.update(title=d.get("text", ""), location=(d.get("categories") or {}).get("location", ""),
                       desc=" ".join([d.get("descriptionPlain") or ""] + ["%s: %s" % (x.get("text"), sources.strip_html(x.get("content")))
                                                                         for x in d.get("lists") or []] + [d.get("additionalPlain") or ""]))
        elif "ashbyhq.com" in host and len(parts) >= 2:
            d = gj("https://api.ashbyhq.com/posting-api/job-board/%s" % parts[0])
            j = next((x for x in d.get("jobs", []) if x.get("id") == parts[1]), None)
            if j:
                job.update(title=j.get("title", ""), location=j.get("location", ""), desc=j.get("descriptionPlain") or "")
        elif "smartrecruiters.com" in host and len(parts) >= 2:
            d = gj("https://api.smartrecruiters.com/v1/companies/%s/postings/%s" % (parts[0], parts[1].split("-")[0]))
            sec = (d.get("jobAd") or {}).get("sections") or {}
            job.update(title=d.get("name", ""), location=(d.get("location") or {}).get("city", ""),
                       desc=" ".join(sources.strip_html((sec.get(k) or {}).get("text")) for k in sec))
    except Exception as e:
        log("api read failed for %s: %s" % (url, str(e)[:120]))
    if len(job["desc"]) < 300 and browser is not None:      # any other site: read the page in the browser
        pg = browser.new_page(user_agent=sources.UA.replace(" FinderJobBot/1.0", ""))
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            pg.wait_for_timeout(2000)
            job["desc"] = re.sub(r"\s+", " ", pg.inner_text("body"))[:15000]
            if not job["title"]:
                h = pg.query_selector("h1")
                job["title"] = (h.inner_text().strip() if h else pg.title()).split("|")[0].strip()[:120]
        except Exception as e:
            log("browser read failed for %s: %s" % (url, str(e)[:120]))
        finally:
            pg.close()
    job["title"] = job["title"] or "Software Engineer"
    return job


# ------------------------------------------------------------------ main

def handle(url, reviewer, browser, master, outdir):
    job = read_job(url, browser)
    if len(job["desc"]) < 300:
        say("Couldn't read the job description at %s\nThe page may need a login or block bots. Paste the job "
            "description text here instead (start the message with the link)." % html.escape(url))
        return
    log("tailoring for %s - %s" % (job["company"], job["title"]))
    r = cv.make(reviewer, browser, master, job, job["desc"], outdir=outdir, mode="max")
    if not r:
        say("The AI couldn't tailor a resume right now (free limit or outage). Send the link again in a while.")
        return
    e = html.escape
    say("<b>%s</b> · %s\n<a href=\"%s\">%s</a>" % (e(job["company"]), e(job["location"] or "-"), e(url), e(job["title"])))
    cap = "Resume for %s - %s\nATS keywords covered: %d/%d" % (job["company"], job["title"], r["covered"], r["total"])
    if r["missing"]:
        cap += "\nNot covered: " + ", ".join(r["missing"])
    with open(r["path"], "rb") as f:
        _tg(TOKEN, "sendDocument", {"chat_id": CHAT, "caption": cap[:1000]},
            {"document": (os.path.basename(r["path"]), f.read())})
    ref = referral.write(reviewer, r["data"], job, job["desc"])
    say("<b>Referral for %s</b>\n\n1) Connection note (%d chars):\n<pre>%s</pre>\n2) Message after they accept:\n"
        "<pre>%s</pre>\n3) For the referrer's form (3rd person):\n<pre>%s</pre>"
        % (e(job["company"]), len(ref["note"]), e(ref["note"]), e(ref["message"]), e(ref["blurb"])))


def main():
    if not (TOKEN and CHAT):
        sys.exit("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set")
    pushed = os.environ.get("BOT_TEXT", "")
    if pushed:
        # instant mode: the Cloudflare relay received the message and passed it in (no polling needed)
        return answer([(None, pushed)], [], acknowledged=True)
    ups = updates()
    work = [(i, t) for i, t in ups if t]
    if "--check" in sys.argv:
        print("has_work=%s" % ("true" if work else "false"))
        if not work and ups:
            confirm(ups[-1][0])          # only messages from others: clear them
        return
    if not ups:
        return
    answer(work, ups)


def answer(work, ups, acknowledged=False):
    profile = json.load(open(os.path.join(ROOT, "profile.json")))
    profile["ai_max_per_run"] = 2 * max(1, sum(len(URL_RX.findall(t)) for _, t in work)) + 2
    reviewer = AIReviewer(profile, log)
    master = cv.load_master(ROOT)
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True, args=["--disable-http2", "--disable-blink-features=AutomationControlled"])
    outdir = tempfile.mkdtemp(prefix="bot-")
    try:
        for uid, text in work:
            urls = list(dict.fromkeys(URL_RX.findall(text)))
            if not urls:
                say(HELP)
            elif not master:
                say("resume.json is missing on the server (is FINDER_KEY set and resume.json.enc pushed?)")
            else:
                if not acknowledged:
                    say("Got it - working on %d job%s..." % (len(urls), "s" if len(urls) > 1 else ""))
                for url in urls[:5]:
                    try:
                        handle(url.rstrip(").,"), reviewer, browser, master, outdir)
                    except Exception as e:
                        log("failed %s: %s" % (url, e))
                        say("Something went wrong with %s: %s" % (html.escape(url), html.escape(str(e)[:150])))
            if uid is not None:
                confirm(uid)             # polling mode: mark this message as answered
    finally:
        browser.close()
        pw.stop()
        if ups:
            confirm(ups[-1][0])


if __name__ == "__main__":
    main()
