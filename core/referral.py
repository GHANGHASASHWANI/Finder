"""Referral outreach for a job, written from the tailored resume (same facts, the job's language).

Three pieces, because LinkedIn and referral portals need different things:
  note    - connection-request note (LinkedIn allows ~200-300 characters)
  message - the DM to send once they accept (first person, short, skimmable)
  blurb   - 2-3 lines in third person the referrer can paste into the portal's "why refer?" box
"""
import json
import re

from core.resume import _numbers

WRITE = """Write referral outreach for a candidate applying to one job. Use ONLY facts from the resume given;
never invent numbers, employers, titles, awards or skills. Match the job's language and stack.

What to highlight:
- If the job's stack overlaps the candidate's work at Toddle (backend APIs, Node.js, GraphQL/REST, PostgreSQL,
  performance), lead with Toddle and add one project as support.
- If the job asks for a different stack (e.g. Java/Spring Boot, Python, Go, mobile, data), lead with the projects
  as listed in the resume for that stack, then one line of Toddle impact.
- If the job is AI/ML/LLM, lead with the RAG/LLM projects.
- Always one problem-solving line (DSA / competitions), short.

Return JSON:
{"note": "LinkedIn connection note, max 280 characters, warm, mentions the role and asks if they'd consider
          referring; no links",
 "message": "first-person DM, 110-170 words: greeting; one line who I am (current title at Toddle, 2026 CSE grad,
             CGPA); 3 short bullets starting with '- ' tailored to this job; one line why this role/team;
             a clear ask to refer; then the lines 'Role: ...', 'Job ID: ...', 'Link: ...' and
             'Resume: attached'; thank you; sign-off 'Ashwani'",
 "blurb": "THIRD person, 2-3 sentences, max 70 words, for the referrer's 'why should we hire them' box"}
Reply with JSON only."""


def job_code(url):
    m = re.search(r"(?:_|/)(JR-?\d{5,}|R-?\d{4,}|P-\d{4,}|\d{6,})(?:[-_/?]|$)", url or "")
    return m.group(1) if m else ""


def fallback(resume, job):
    """Plain template if the AI is unavailable - built only from resume facts."""
    t = resume["experience"][0]
    title = (t.get("roles") or [[t.get("role", "")]])[0][0]
    b1 = t["bullets"][0]
    proj = resume["projects"][0]
    msg = ("Hi,\nI hope you're doing well. I'm Ashwani, %s at Toddle and a 2026 CSE graduate from Chandigarh University. "
           "I came across the %s role at %s and it matches my experience closely.\n\n- %s\n- %s: %s\n- %s\n\n"
           "Would you be open to referring me?\n\nRole: %s\nJob ID: %s\nLink: %s\nResume: attached\n\n"
           "Thank you so much for considering it.\nBest regards,\nAshwani") % (
        title, job["title"], job["company"], b1, proj["name"].split(" - ")[0], proj["bullets"][0],
        (resume.get("achievements") or [""])[0], job["title"], job_code(job.get("url")) or "-", job.get("url", ""))
    note = ("Hi! I'm Ashwani, %s at Toddle (backend, 2M+ MAU). I'm applying for the %s role at %s and would be "
            "grateful if you'd consider referring me. Happy to share details!") % (title, job["title"], job["company"])
    return {"note": note[:280], "message": msg,
            "blurb": "Ashwani is %s %s at Toddle and a 2026 CSE graduate. %s" % (
                "an" if title[:1].lower() in "aeiou" else "a", title, b1)}


def write(reviewer, resume, job, desc):
    """Returns dict(note, message, blurb). Uses the AI if available, otherwise the plain template."""
    out = fallback(resume, job)
    if reviewer is None or not reviewer.available():
        return out
    reviewer.used += 1
    facts = {k: v for k, v in resume.items() if k not in ("contact",)}
    user = "RESUME (tailored for this job):\n%s\n\nJOB: %s at %s\nJob ID: %s\nLink: %s\n\nPOSTING:\n%s" % (
        json.dumps(facts, ensure_ascii=False), job["title"], job["company"], job_code(job.get("url")) or "-",
        job.get("url", ""), (desc or "")[:5000])
    res, _ = reviewer.ask_json(WRITE, user, max_tokens=1500, temperature=0.4)
    if not isinstance(res, dict):
        return out
    allowed = _numbers(json.dumps(resume)) | _numbers(job.get("url", "")) | _numbers(job_code(job.get("url")))
    allowed |= _numbers(job["title"])
    for k, cap in (("note", 300), ("message", 1600), ("blurb", 500)):
        v = res.get(k)
        if isinstance(v, str) and 20 < len(v) <= cap and _numbers(v) <= allowed:
            out[k] = v.strip()
    return out
