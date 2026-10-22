"""Readers for each careers-page system. Every reader returns a list of job dicts."""
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36 FinderJobBot/1.0")


def http(url, data=None, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
    h.update(headers or {})
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def getjson(url, **kw):
    return json.loads(http(url, **kw))


TAG = re.compile(r"<[^>]+>")


def strip_html(s):
    if not s:
        return ""
    s = html.unescape(html.unescape(s))
    return re.sub(r"\s+", " ", TAG.sub(" ", s)).strip()


def iso(value):
    """Best-effort conversion of the many date formats to ISO-8601 (UTC)."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            v = float(value)
            if v > 1e12:
                v /= 1000.0
            return datetime.fromtimestamp(v, timezone.utc).isoformat()
        s = str(value).strip()
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d %B %Y"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.isoformat()
    except Exception:
        return None


def workday_posted(text):
    """Workday says 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'."""
    if not text:
        return None
    t = text.lower()
    now = time.time()
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    else:
        m = re.search(r"(\d+)\+?\s*day", t)
        if not m:
            return None
        days = int(m.group(1))
    return datetime.fromtimestamp(now - days * 86400, timezone.utc).isoformat()


def mk(company, jid, title, location, url, posted=None, desc="", dept="", comp="", level=""):
    return {
        "level": level or "",
        "id": "%s::%s" % (company, jid),
        "company": company,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "posted": posted,
        "desc": (desc or "")[:15000],
        "dept": dept or "",
        "comp": comp or "",
    }


# ---------------------------------------------------------------- readers

def greenhouse(c, s):
    host = "boards-api.eu.greenhouse.io" if s.get("eu") else "boards-api.greenhouse.io"
    try:
        d = getjson("https://%s/v1/boards/%s/jobs?content=true" % (host, s["token"]))
    except Exception:
        return greenhouse_board(c, s)
    out = []
    for j in d.get("jobs", []):
        out.append(mk(c, j["id"], j.get("title"), (j.get("location") or {}).get("name"),
                      j.get("absolute_url"), iso(j.get("first_published") or j.get("updated_at")),
                      strip_html(j.get("content")),
                      ", ".join(x.get("name", "") for x in j.get("departments") or [])))
    return out


def greenhouse_board(c, s):
    """Fallback: the public job-board site's own data route (works for EU boards too)."""
    host = "job-boards.eu.greenhouse.io" if s.get("eu") else "job-boards.greenhouse.io"
    out, page = [], 1
    while page <= 40:
        d = getjson("https://%s/%s?_data=routes%%2F%%24url_token&page=%d" % (host, s["token"], page))
        jp = d.get("jobPosts") or {}
        for j in jp.get("data", []):
            out.append(mk(c, j["id"], j.get("title"), j.get("location"), j.get("absolute_url"),
                          iso(j.get("published_at") or j.get("updated_at")), strip_html(j.get("content"))))
        if page >= (jp.get("total_pages") or 1):
            break
        page += 1
    return out


def lever(c, s):
    host = "api.eu.lever.co" if s.get("eu") else "api.lever.co"
    d = getjson("https://%s/v0/postings/%s?mode=json" % (host, s["slug"]))
    out = []
    for j in d:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location") or ""]
        loc = ", ".join(x for x in locs if x)
        if j.get("workplaceType") == "remote" and "remote" not in loc.lower():
            loc += " (Remote)"
        out.append(mk(c, j["id"], j.get("text"), loc, j.get("hostedUrl"), iso(j.get("createdAt")),
                      j.get("descriptionPlain") or strip_html(j.get("description")), cat.get("team")))
    return out


def ashby(c, s):
    d = getjson("https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % s["org"])
    out = []
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [x.get("location", "") for x in j.get("secondaryLocations") or []]
        loc = ", ".join(x for x in locs if x)
        if j.get("isRemote") and "remote" not in loc.lower():
            loc += " (Remote)"
        comp = ((j.get("compensation") or {}).get("compensationTierSummary")) or ""
        out.append(mk(c, j.get("id"), j.get("title"), loc, j.get("jobUrl"), iso(j.get("publishedAt")),
                      j.get("descriptionPlain") or "", j.get("department"), comp))
    return out


def workable(c, s):
    d = getjson("https://apply.workable.com/api/v1/widget/accounts/%s?details=true" % s["sub"])
    out = []
    for j in d.get("jobs", []):
        locs = j.get("locations") or [{"city": j.get("city"), "country": j.get("country")}]
        loc = "; ".join(", ".join(x for x in (l.get("city"), l.get("country")) if x) for l in locs)
        if j.get("telecommuting"):
            loc += " (Remote)"
        out.append(mk(c, j.get("shortcode"), j.get("title"), loc, j.get("url") or j.get("shortlink"),
                      iso(j.get("published_on") or j.get("created_at")), strip_html(j.get("description"))))
    return out


def smartrecruiters(c, s):
    out, offset = [], 0
    while offset < 2000:
        d = getjson("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100&offset=%d&country=in"
                    % (s["id"], offset))
        rows = d.get("content", [])
        for j in rows:
            l = j.get("location") or {}
            loc = ", ".join(x for x in (l.get("city"), l.get("region"), l.get("country")) if x)
            if l.get("remote"):
                loc += " (Remote)"
            out.append(mk(c, j["id"], j.get("name"), loc,
                          "https://jobs.smartrecruiters.com/%s/%s" % (s["id"], j["id"]),
                          iso(j.get("releasedDate")), "", (j.get("department") or {}).get("label"),
                          level=(j.get("experienceLevel") or {}).get("label")))
        offset += 100
        if len(rows) < 100 or offset >= d.get("totalFound", 0):
            break
    return out


INDIA_RX = re.compile(r"\bindia\b|\bind\b|bangalore|bengaluru|noida|gurgaon|gurugram|hyderabad|pune|mumbai|chennai|delhi", re.I)


def workday_india_facet(facets):
    """Find the India location filter in a Workday site's facets (names differ per company)."""
    found = {}

    def walk(fs, parent=None):
        for f in fs:
            for v in f.get("values") or []:
                if v.get("values"):
                    walk([v], f.get("facetParameter") or parent)
                elif INDIA_RX.search(v.get("descriptor") or ""):
                    found.setdefault(f.get("facetParameter") or parent, []).append(v["id"])
    walk(facets or [])
    for k in found:                       # a country filter is the cleanest
        if "country" in (k or "").lower():
            return {k: found[k]}
    for k in found:
        if k:
            return {k: found[k]}
    return None


def workday(c, s):
    base = "https://%s.%s.myworkdayjobs.com" % (s["tenant"], s["wd"])
    api = "%s/wday/cxs/%s/%s/jobs" % (base, s["tenant"], s["site"])
    hdr = {"Accept": "application/json"}
    first = getjson(api, data={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}, headers=hdr)
    facet = workday_india_facet(first.get("facets"))
    query = "" if facet else "India"
    out, offset, total = [], 0, None
    while offset < s.get("max", 1000):
        d = getjson(api, data={"appliedFacets": facet or {}, "limit": 20, "offset": offset, "searchText": query},
                    headers=hdr)
        if total is None:
            total = d.get("total", 0)
        rows = d.get("jobPostings", [])
        for j in rows:
            loc = j.get("locationsText") or ""
            if re.match(r"^\d+ Locations?$", loc):
                loc = "India (%s)" % loc
            path = j.get("externalPath", "")
            out.append(mk(c, path or j.get("title"), j.get("title"), loc,
                          "%s/en-US/%s%s" % (base, s["site"], path), workday_posted(j.get("postedOn"))))
        offset += 20
        if not rows or offset >= (total or 0):
            break
    return out


def eightfold(c, s):
    out, start = [], 0
    base = s["url"]
    sep = "&" if "?" in base else "?"
    while start < 500:
        d = getjson("%s%sstart=%d&num=50&sort_by=timestamp" % (base, sep, start))
        rows = d.get("positions", [])
        for j in rows:
            loc = "; ".join(j.get("locations") or [j.get("location") or ""])
            url = j.get("canonicalPositionUrl") or j.get("url") or base
            out.append(mk(c, j.get("id"), j.get("name"), loc, url, iso(j.get("t_create")),
                          strip_html(j.get("job_description")), j.get("department")))
        start += 50
        if len(rows) < 50:
            break
    return out


def pcsx(c, s):
    """Eightfold's newer careers API (Microsoft, Qualcomm, Micron ...): {"host":..., "domain":...}."""
    out, start = [], 0
    while start < s.get("max", 1000):
        d = getjson("https://%s/api/pcsx/search?domain=%s&query=&location=%s&start=%d&sort_by=timestamp"
                    % (s["host"], s["domain"], urllib.parse.quote(s.get("location", "India")), start))
        d = d.get("data", d)
        rows = d.get("positions", [])
        for j in rows:
            out.append(mk(c, j.get("id"), j.get("name"), "; ".join(j.get("locations") or []),
                          "https://%s%s" % (s["host"], j.get("positionUrl", "")), iso(j.get("postedTs")),
                          "", j.get("department")))
        start += len(rows)
        if not rows or start >= (d.get("count") or 0):
            break
    return out


def _path(obj, path):
    """Read 'a.b.0.c' from nested JSON; '' returns obj."""
    if not path:
        return obj
    for part in path.split("."):
        if obj is None:
            return None
        if isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            obj = obj.get(part)
    return obj


def _fill(x, n):
    if isinstance(x, str):
        return x.replace("{n}", str(n))
    if isinstance(x, dict):
        return {k: _fill(v, n) if not (isinstance(v, str) and v == "{n:int}") else n for k, v in x.items()}
    if isinstance(x, list):
        return [_fill(v, n) for v in x]
    return x


def jsonapi(c, s):
    """Generic JSON careers API. Spec keys:
       url (may contain {n}), method GET|POST, body (JSON, "{n:int}" or "{n}" placeholders), headers,
       items (dot path to the job list), fields {id,title,location,url,posted,desc} (dot paths),
       url_prefix (prepended to relative job urls), page {start, step, max} for {n}."""
    pg = s.get("page") or {"start": 0, "step": 0, "max": 1}
    out, n, seen_ids = [], pg.get("start", 0), set()
    f = s.get("fields", {})
    for _ in range(pg.get("max", 1)):
        url = _fill(s["url"], n)
        body = _fill(s.get("body"), n) if s.get("body") is not None else None
        if s.get("method", "GET").upper() == "POST":
            d = getjson(url, data=body if body is not None else {}, headers=s.get("headers"))
        else:
            d = getjson(url, headers=s.get("headers"))
        rows = _path(d, s.get("items", "")) or []
        fresh = 0
        for j in rows:
            loc = _path(j, f.get("location", "location"))
            if isinstance(loc, list):
                loc = "; ".join(x if isinstance(x, str) else str(_path(x, f.get("location_sub", "name")) or "")
                                for x in loc)
            elif isinstance(loc, dict):
                loc = ", ".join(str(v) for v in loc.values() if isinstance(v, str))
            jid = _path(j, f.get("id", "id")) or _path(j, f.get("url", "url"))
            if jid in seen_ids:
                continue
            seen_ids.add(jid)
            fresh += 1
            link = _path(j, f.get("url", "url")) or ""
            if s.get("url_template"):
                link = s["url_template"].replace("{id}", str(jid))
            elif link and not str(link).startswith("http"):
                link = s.get("url_prefix", "") + str(link)
            out.append(mk(c, jid, _path(j, f.get("title", "title")), loc, link,
                          iso(_path(j, f.get("posted", "posted"))), strip_html(str(_path(j, f.get("desc", "_")) or ""))))
        if not pg.get("step") or not fresh or len(rows) == 0:
            break
        n += pg["step"]
    return out


def amazon(c, s):
    out = []
    for q in ("software development engineer", "software engineer"):
        for offset in (0, 100):
            d = getjson("https://www.amazon.jobs/en/search.json?country=IND&base_query=%s&sort=recent"
                        "&result_limit=100&offset=%d" % (urllib.parse.quote(q), offset))
            for j in d.get("jobs", []):
                out.append(mk(c, j.get("id_icims") or j.get("id"), j.get("title"),
                              j.get("normalized_location") or j.get("location"),
                              "https://www.amazon.jobs" + j.get("job_path", ""), iso(j.get("posted_date")),
                              strip_html((j.get("basic_qualifications") or "") + " " +
                                         (j.get("description_short") or "")), j.get("job_category")))
    uniq = {}
    for j in out:
        uniq[j["id"]] = j
    return list(uniq.values())


def atlassian(c, s):
    d = getjson("https://www.atlassian.com/endpoint/careers/listings")
    rows = d if isinstance(d, list) else d.get("listings") or d.get("jobs") or []
    out = []
    for j in rows:
        locs = j.get("locations") or []
        loc = "; ".join(x if isinstance(x, str) else str(x.get("name", x)) for x in locs)
        url = ((j.get("portalJobPost") or {}).get("portalUrl") or j.get("applyUrl")
               or "https://www.atlassian.com/company/careers/details/%s" % j.get("id"))
        out.append(mk(c, j.get("id"), j.get("title"), loc, url, iso(j.get("updatedDate") or j.get("postedDate")),
                      strip_html(j.get("overview") or j.get("description") or ""), j.get("category")))
    return out


JOBLINK = re.compile(r"job|career|position|opening|requisition|vacanc|gh_jid|lever\.co|ashbyhq|apply|/j/|role",
                     re.I)


def page(c, s, browser=None):
    """Generic watcher for custom career sites: collect job-looking links on the page."""
    url = s["url"]
    links = []
    if browser is not None:
        pg = browser.new_page(user_agent=UA)
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                pg.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            if s.get("wait_for"):
                try:
                    pg.wait_for_selector(s["wait_for"], timeout=15000)
                except Exception:
                    pass
            pg.wait_for_timeout(2500)
            links = pg.eval_on_selector_all(
                "a[href]", "els => els.map(e => [e.href, (e.innerText || e.textContent || '').trim()])")
        finally:
            pg.close()
    else:
        raw = http(url)
        for href, text in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, re.I | re.S):
            links.append([urllib.parse.urljoin(url, html.unescape(href)), strip_html(text)])
    out, seen = [], set()
    rx = re.compile(s["link_regex"], re.I) if s.get("link_regex") else JOBLINK
    for href, text in links:
        text = re.sub(r"\s+", " ", text or "").strip()
        first = text.split(" • ")[0]
        if not (4 <= len(first) <= 140) or not href.startswith("http"):
            continue
        if not rx.search(href):
            continue
        key = href.split("#")[0]
        if key in seen or key.rstrip("/") == url.rstrip("/"):
            continue
        seen.add(key)
        # the rest of the link text often carries the location
        out.append(mk(c, key, first[:140], text[len(first):].strip(" •|-,")[:200], key))
    return out


READERS = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
    "smartrecruiters": smartrecruiters, "workday": workday, "eightfold": eightfold,
    "amazon": amazon, "atlassian": atlassian, "page": page, "pcsx": pcsx, "json": jsonapi,
}


# ---------------------------------------------------------------- job details (full description)

def _wd_detail(c, s, job):
    path = job["id"].split("::", 1)[1]
    base = "https://%s.%s.myworkdayjobs.com" % (s["tenant"], s["wd"])
    d = getjson("%s/wday/cxs/%s/%s%s" % (base, s["tenant"], s["site"], path), headers={"Accept": "application/json"})
    return strip_html((d.get("jobPostingInfo") or {}).get("jobDescription"))


def _sr_detail(c, s, job):
    jid = job["id"].split("::", 1)[1]
    d = getjson("https://api.smartrecruiters.com/v1/companies/%s/postings/%s" % (s["id"], jid))
    sec = (d.get("jobAd") or {}).get("sections") or {}
    return " ".join(strip_html((sec.get(k) or {}).get("text")) for k in ("jobDescription", "qualifications",
                                                                           "additionalInformation"))


def _pcsx_detail(c, s, job):
    jid = job["id"].split("::", 1)[1]
    d = getjson("https://%s/api/pcsx/position_details?position_id=%s&domain=%s&hl=en" % (s["host"], jid, s["domain"]))
    d = d.get("data", d)
    return strip_html(d.get("jobDescription") or d.get("job_description") or d.get("description"))


def _visible_text(raw):
    raw = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", raw)
    return strip_html(raw)


DETAIL = {"workday": _wd_detail, "smartrecruiters": _sr_detail, "pcsx": _pcsx_detail}


def describe(company, job, browser=None):
    """Fill in job['desc'] from the job page when the feed did not include it. Returns True if text was found."""
    if len(job.get("desc") or "") >= 300:
        return True
    s = company["source"]
    text = ""
    try:
        if s["type"] in DETAIL:
            text = DETAIL[s["type"]](company["company"], s, job)
    except Exception:
        text = ""
    if len(text) < 300 and job.get("url", "").startswith("http"):
        try:
            text = _visible_text(http(job["url"], headers={"Accept": "text/html"}, timeout=25))
        except Exception:
            pass
    if len(text) < 300 and browser is not None and job.get("url", "").startswith("http"):
        pg = browser.new_page(user_agent=UA)
        try:
            pg.goto(job["url"], wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_timeout(2500)
            text = re.sub(r"\s+", " ", pg.inner_text("body"))
        except Exception:
            pass
        finally:
            pg.close()
    if len(text) >= 300:
        job["desc"] = text[:15000]
        return True
    return False
