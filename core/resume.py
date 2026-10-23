"""Tailored one-page PDF resume for a job: AI rewrites the master resume for the job description,
safety checks keep the facts fixed, and the headless browser renders an ATS-friendly PDF.

The master resume (resume.json) holds personal data, so it is kept encrypted in the public repo
(secure.py) and generated PDFs are written to a temp folder only - never committed.
"""
import copy
import html
import json
import os
import re
import tempfile

TAILOR = """You tailor a software engineer's resume to one job posting so it passes ATS keyword screening.

Return the SAME JSON structure as the master resume, with these rules:
- "headline": up to 10 words, using the job's main role and stack words.
- "summary": max 3 lines, rewritten with the job's key terms; keep the candidate's real facts (degree, CGPA,
  years, employer).
- experience[*].bullets: rephrase with the job's vocabulary. NEVER change the company, titles, dates or any
  number/metric. Keep 3-4 bullets.
- projects[*]: you may present each project in the tech stack the job asks for (the candidate has versions of
  both projects in several stacks): set "tech" accordingly and reword the bullets to match. Keep every number
  and metric exactly as in the master. Keep "name" and "dates"; you may change the subtitle after " - ".
- skills: 5-6 groups as [label, comma list]; put the job's required technologies first; include the job's
  technologies the candidate lists or the posting requires.
- achievements: max 2 items, same facts.
- Do not add certifications, companies, degrees, awards or numbers that are not in the master.
Also add "keywords": the 20 most important skills/terms from the job posting (short phrases, as written in it).
Reply with JSON only."""

NUM = re.compile(r"\d[\d,.]*\+?%?")


def load_master(root):
    try:
        with open(os.path.join(root, "resume.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _numbers(text):
    return set(n.rstrip(".,") for n in NUM.findall(text or ""))


def _safe(master, ai):
    """Merge the AI version onto the master; anything that changes facts falls back to the master."""
    out = copy.deepcopy(master)
    master_nums = _numbers(json.dumps(master))
    ok_text = lambda t: isinstance(t, str) and 5 < len(t) < 400 and _numbers(t) <= master_nums
    if ok_text(ai.get("headline")) and len(ai["headline"]) < 110:
        out["headline"] = ai["headline"]
    if ok_text(ai.get("summary")):
        out["summary"] = ai["summary"]
    def merge(orig, new, cap):
        """Use each AI bullet if it passes the checks, otherwise keep the master bullet in that slot."""
        if not isinstance(new, list) or not new:
            return orig
        res = [x if ok_text(x) else (orig[i] if i < len(orig) else None) for i, x in enumerate(new[:cap])]
        res = [x for x in res if x]
        return res if len(res) >= 2 else orig

    for i, e in enumerate(out.get("experience", [])):
        try:
            e["bullets"] = merge(e["bullets"], ai["experience"][i]["bullets"], 4)
        except (KeyError, IndexError, TypeError):
            pass
    for i, p in enumerate(out.get("projects", [])):
        try:
            a = ai["projects"][i]
        except (KeyError, IndexError, TypeError):
            continue
        name = str(a.get("name") or "")
        if name.split(" - ")[0].strip() == p["name"].split(" - ")[0].strip() and len(name) < 90:
            p["name"] = name
        if isinstance(a.get("tech"), str) and len(a["tech"]) < 200:
            p["tech"] = a["tech"]
        p["bullets"] = merge(p["bullets"], a.get("bullets"), 3)
    sk = [s for s in (ai.get("skills") or []) if isinstance(s, list) and len(s) == 2
          and all(isinstance(x, str) for x in s) and len(s[1]) < 260]
    if 3 <= len(sk) <= 7:
        out["skills"] = sk
    ach = [x for x in (ai.get("achievements") or []) if ok_text(x)]
    if ach:
        out["achievements"] = ach[:2]
    return out


def tailor(reviewer, master, job, desc):
    """Returns (resume_dict, keywords) or (None, None)."""
    if not reviewer.available():
        return None, None
    reviewer.used += 1
    m = {k: v for k, v in master.items() if k not in ("name", "contact", "education")}
    user = "MASTER RESUME:\n%s\n\nJOB: %s at %s (%s)\n\nPOSTING:\n%s" % (
        json.dumps(m, ensure_ascii=False), job["title"], job["company"], job.get("location", ""), (desc or "")[:7000])
    res, by = reviewer.ask_json(TAILOR, user, max_tokens=4000, temperature=0.2)
    if not isinstance(res, dict):
        return None, None
    kws = [str(k)[:40] for k in (res.get("keywords") or []) if isinstance(k, str)][:25]
    return _safe(master, res), kws


def coverage(data, keywords):
    text = json.dumps(data, ensure_ascii=False).lower()
    hit = [k for k in keywords if k.lower() in text or all(w in text for w in re.findall(r"[a-z0-9+#.]+", k.lower()))]
    return hit, [k for k in keywords if k not in hit]


# ------------------------------------------------------------------ PDF

CSS = """
@page { size: A4; margin: 8.5mm 12mm; }
body { font-family: Carlito, Calibri, 'Liberation Sans', Arial, sans-serif; font-size: %(fs)spt; color: #111;
       line-height: 1.22; margin: 0; }
h1 { text-align: center; font-size: 16pt; margin: 0 0 1pt; }
.head { text-align: center; margin: 0 0 1pt; font-size: 10.5pt; }
.contact { text-align: center; margin-bottom: 3pt; }
.contact a { color: #1F4E9A; text-decoration: none; }
h2 { font-size: 11pt; text-transform: uppercase; border-bottom: 0.8pt solid #444; margin: 6pt 0 2pt; padding-bottom: 1pt; }
.row { display: flex; justify-content: space-between; margin-top: 3pt; }
.row.tight { margin-top: 0; }
.b { font-weight: bold; } .i { font-style: italic; }
ul { margin: 1pt 0 0; padding-left: 14pt; list-style: none; } li { margin: 0 0 1pt; text-indent: -9pt; }
li::before { content: '\\2022  '; }
p { margin: 0 0 1pt; }
"""


def to_html(d, fs=10):
    e = html.escape
    o = ["<html><head><meta charset='utf-8'><style>%s</style></head><body>" % (CSS % {"fs": fs})]
    o.append("<h1>%s</h1><div class='head'>%s</div>" % (e(d["name"]), e(d.get("headline", ""))))
    o.append("<div class='contact'>%s</div>" % "  |  ".join(
        "<a href='%s'>%s</a>" % (e(u), e(t)) if u else e(t) for t, u in d["contact"]))
    if d.get("summary"):
        o.append("<h2>Summary</h2><p>%s</p>" % e(d["summary"]))
    o.append("<h2>Experience</h2>")
    for x in d["experience"]:
        if x.get("roles"):
            o.append("<div class='row'><span class='b'>%s</span><span>%s</span></div>" % (e(x["org"]), e(x["place"])))
            for r, dt in x["roles"]:
                o.append("<div class='row tight i'><span>%s</span><span>%s</span></div>" % (e(r), e(dt)))
        else:
            o.append("<div class='row'><span class='b'>%s</span><span>%s</span></div>" % (e(x["org"]), e(x["dates"])))
            o.append("<div class='row tight i'><span>%s</span><span>%s</span></div>" % (e(x["role"]), e(x["place"])))
        o.append("<ul>%s</ul>" % "".join("<li>%s</li>" % e(b) for b in x["bullets"]))
    o.append("<h2>Projects</h2>")
    for p in d["projects"]:
        o.append("<div class='row'><span class='b'>%s</span><span>%s</span></div>" % (e(p["name"]), e(p["dates"])))
        o.append("<p class='i'>Tech: %s</p>" % e(p["tech"]))
        o.append("<ul>%s</ul>" % "".join("<li>%s</li>" % e(b) for b in p["bullets"]))
    o.append("<h2>Technical Skills</h2>")
    o.append("".join("<p><span class='b'>%s:</span> %s</p>" % (e(k), e(v)) for k, v in d["skills"]))
    if d.get("achievements"):
        o.append("<h2>Achievements</h2><ul>%s</ul>" % "".join("<li>%s</li>" % e(a) for a in d["achievements"]))
    o.append("<h2>Education</h2>")
    for x in d["education"]:
        o.append("<div class='row'><span class='b'>%s</span><span>%s</span></div>" % (e(x["org"]), e(x["place"])))
        o.append("<div class='row tight'><span>%s</span><span>%s</span></div>" % (e(x["degree"]), e(x["dates"])))
    o.append("</body></html>")
    return "".join(o)


def render_pdf(browser, data, path):
    """Renders a one-page A4 PDF (shrinks the font slightly, then trims, if it overflows)."""
    d = copy.deepcopy(data)
    page = browser.new_page()
    try:
        for attempt in range(8):
            fs = max(9.0, 10 - 0.25 * attempt)
            page.set_content(to_html(d, fs))
            # A4 = 297mm, minus 2 x 8.5mm margins = 280mm of content height at 96 dpi
            if page.evaluate("document.body.scrollHeight") <= 280 / 25.4 * 96 or attempt == 7:
                break
            if fs == 9.0:   # still too long: drop the least important lines
                if len(d.get("achievements") or []) > 1:
                    d["achievements"] = d["achievements"][:1]
                elif d.get("summary") and len(d["summary"]) > 200:
                    d["summary"] = d["summary"][:d["summary"].rfind(";", 0, 200)] + "." if ";" in d["summary"][:200] else d["summary"]
                else:
                    longest = max(d["projects"], key=lambda p: len(p["bullets"]))
                    if len(longest["bullets"]) > 2:
                        longest["bullets"] = longest["bullets"][:-1]
        page.pdf(path=path, format="A4", print_background=False,
                 margin={"top": "8.5mm", "bottom": "8.5mm", "left": "12mm", "right": "12mm"})
    finally:
        page.close()
    return path


def make(reviewer, browser, master, job, desc, outdir=None):
    """Full pipeline for one job. Returns dict(path, covered, total, missing) or None."""
    data, kws = tailor(reviewer, master, job, desc)
    if not data:
        return None
    outdir = outdir or tempfile.mkdtemp(prefix="resumes-")
    safe = re.sub(r"[^A-Za-z0-9]+", "_", job["company"]).strip("_")[:40] or "Company"
    path = os.path.join(outdir, "%s_%s.pdf" % (re.sub(r"\s+", "_", master["name"]), safe))
    n = 2
    while os.path.exists(path):
        path = path.replace(".pdf", "_%d.pdf" % n)
        n += 1
    render_pdf(browser, data, path)
    hit, miss = coverage(data, kws)
    return {"path": path, "covered": len(hit), "total": len(kws), "missing": miss[:6], "data": data}
