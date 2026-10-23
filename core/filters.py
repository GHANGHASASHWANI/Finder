"""Decides whether a job fits the profile, labels how fresher-friendly it is, and scores it.

Fit labels
  fresher     says new grad / entry level / 0-1 years
  open        no experience or seniority mentioned
  stretch     asks for up to 2 years (e.g. "2 years", "1-2 years", "0-2 years", "1-3 years")
  unverified  the job description could not be read
Rejected: anything asking for 2+ / minimum 2 / 3 or more years anywhere, or senior wording.
"""
import re

NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
N = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
YEARS_RX = re.compile(
    r"(?P<pre>(?:minimum|min\.?|at\s+least|over|more\s+than|in\s+excess\s+of)\s*(?:of\s*)?)?"
    r"\(?" + N + r"\)?\s*(?P<plus>\+|plus)?\s*(?:(?:-|–|—|to)\s*" + N.replace("(", "(?P<hi>", 1) +
    r"\s*\+?)?\s*(?:\(?\s*\+\s*\)?)?\s*(?:years?|yrs?)\b(?P<post>\s*(?:\+|or\s+more|or\s+above|plus|and\s+above))?",
    re.I)
# words near a "N years" that show it is about the candidate's experience (not "4-year degree", "founded 10 years ago")
EXP_CONTEXT = re.compile(r"experience|\bexp\b|hands[- ]on|relevant|proven|professional|industry|track record|"
                         r"seniority|expertise|proficien", re.I)
# weaker words count only right after "N years", e.g. "5-8 years development", "3 years of coding"
EXP_RIGHT_AFTER = re.compile(r"^\s*(of\s+)?(\w+\s+){0,2}?(develop|coding|programming|building|software|engineering|"
                             r"working|work\b|in (a |the )?(similar|software|engineering|backend|data|it\b))", re.I)
COMPANY_TALK = re.compile(r"\b(founded|established|in business|ago|history|since|legacy|we have been|we've been|"
                          r"for over|for more than|our (team|company|journey|founders|clients) (has|have))\b", re.I)
NOT_EXP = re.compile(r"degree|bachelor|course|program(?!m)|\bago\b|warranty|\bold\b|anniversary|of (history|legacy)", re.I)
FRESHER_RX = re.compile(r"new[- ]?grads?\b|\bfreshers?\b|entry[- ]level|campus (hire|hiring|recruit|drive|program)|"
                        r"early[- ]career|university grad|graduate (program|engineer|hire|trainee)|recent(ly)? graduat|"
                        r"\b0\s*(?:-|–|to)\s*1\s*(?:years?|yrs?)|no (prior )?experience (is )?required|"
                        r"20(25|26)\s*(batch|pass|grad)", re.I)
# senior wording that is unambiguous wherever it appears
SENIOR_DESC_RX = re.compile(
    r"\bsenior[- ]level\b|\bmid[- ]?(?:to[- ]senior|senior)[- ]level\b|\bmid[- ]level\b|seasoned (engineer|developer|professional)|"
    r"mentor(ing|s)? (junior|other) (engineers|developers|team members)|lead (a|the) team of|team lead(ership)? experience|"
    r"people management|manage (a|the) team of|experienced hire", re.I)
# wording that is senior only when it describes the candidate ("you have extensive experience"),
# not company boilerplate ("our extensive experience in fintech")
SENIOR_ASK_RX = re.compile(r"extensive (professional |industry |hands[- ]on )?experience|significant (professional |industry )?"
                           r"experience|substantial experience|deep expertise|proven track record of leading", re.I)
ASKS_CANDIDATE = re.compile(r"\b(you|your|candidate|applicant|should|must|require[sd]?|need|bring|ideal|looking for|"
                            r"have|possess)\b", re.I)

ROLE_TYPES = [
    ("AI/ML", r"\b(ai|ml|machine learning|genai|gen ai|llm|deep learning|nlp|computer vision|applied scien|mlops)\b"),
    ("Data", r"\bdata (engineer|platform|pipeline)|\betl\b|pyspark|\bspark\b|big data|analytics engineer"),
    ("DevOps/SRE/Cloud", r"devops|dev ops|\bsre\b|site reliability|platform engineer|cloud engineer|infrastructure|"
                         r"prodops|production engineer|\bops\b"),
    ("QA/Test", r"\bqa\b|quality|\btest|sdet|automation engineer"),
    ("Mobile", r"android|\bios\b|mobile|flutter|react native"),
    ("Frontend", r"front[- ]?end|\bui\b|web developer"),
    ("Full-stack", r"full[- ]?stack"),
    ("Backend", r"back[- ]?end|\bapi\b|server"),
    ("Quant", r"quant|trading|low[- ]latency"),
]


REMOTE_WORDS_RX = re.compile(r"remote|anywhere|worldwide|world wide|global(ly)?|apac|asia(-pacific)?|work from home|"
                             r"\bwfh\b|home[- ]based|distributed|fully|first|\bor\b|\band\b|\bteam\b|\bany\b|"
                             r"\blocation(s)?\b|international|[^A-Za-z]+", re.I)


GENERIC_INCLUDES = {"new grad", "graduate", "university", "campus", "early career", "fresher", "entry[- ]level",
                    "junior", "\\bjr\\.?\\b"}
TECH_WORD = re.compile(r"software|engineer|develop|technolog|\btech\b|programm|\bit\b|data|\bai\b|\bml\b|cloud|"
                       r"cyber|security|digital|computer|sde|swe|quant|analytics|platform", re.I)


def _n(x):
    return NUM.get(x.lower(), None) if not x.isdigit() else int(x)


class Matcher:
    def __init__(self, profile):
        p = profile
        self.p = p
        c = lambda xs: [re.compile(x, re.I) for x in xs]
        self.inc = c(p["include_title"])
        self.exc = c(p["exclude_title"])
        self.india = c(p["india_locations"])
        self.remote = c(p["remote_words"])
        self.block = c(p["remote_blocklist"])
        self.prio = [(name, re.compile(rx, re.I)) for name, rx in p["location_priority"]]
        self.level_ii_ok = set(p.get("level_ii_ok_companies", []))
        self.resume = {k: re.compile(v, re.I) for k, v in p.get("resume_skills", {}).items()}
        self.vocab = {k: re.compile(v, re.I) for k, v in p.get("tech_vocab", {}).items()}
        self.vocab.update(self.resume)
        self.max_years = p.get("max_years_required", 2)

    # ---- title
    def title_ok(self, title, company=""):
        t = title or ""
        hits = [r for r in self.inc if r.search(t)]
        if not hits:
            return False, "title not a software role"
        if all(r.pattern in GENERIC_INCLUDES for r in hits) and not TECH_WORD.search(t):
            return False, "fresher program title without a tech word"
        for r in self.exc:
            if r.search(t):
                if company in self.level_ii_ok and re.search(r"\bii\b|\b2\b", t, re.I) and \
                        not re.search(r"\biii\b|senior|staff|principal|lead|manager", t, re.I):
                    continue
                return False, "excluded: %s" % r.pattern
        return True, ""

    # ---- location
    def location_group(self, loc, source_type="", company_locs=""):
        l = (loc or "").strip()
        if not l:
            return "Unknown (check page)" if source_type == "page" else None
        if any(r.search(l) for r in self.india):
            for name, rx in self.prio:
                if name != "Remote" and rx.search(l):
                    return name
            return "Other India"
        if any(r.search(l) for r in self.remote) and not any(r.search(l) for r in self.block):
            # only truly location-free remote jobs: "Remote", "Remote - Worldwide", "Anywhere (APAC)".
            # "San Francisco (Remote)" or "Paris (Remote)" means remote within that country -> not for India.
            rest = REMOTE_WORDS_RX.sub(" ", l)
            if not re.search(r"[A-Za-z]{2,}", rest):
                return "Remote"
        return None

    def prefilter(self, job, company):
        """Cheap checks on title + location only (before the job page is downloaded)."""
        ok, why = self.title_ok(job["title"], company["company"])
        if not ok:
            return False, why
        if not self.location_group(job["location"], company["source"]["type"]):
            return False, "location not India/remote"
        return True, ""

    # ---- experience
    def experience(self, text):
        """Returns (verdict, min_years, snippet). verdict: 'reject' | 'stretch' | 'fresher' | 'open'."""
        if not text:
            return "open", None, ""
        worst, lows, snippet = None, [], ""
        for m in YEARS_RX.finditer(text):
            lo = _n(m.group(2))
            hi = _n(m.group("hi")) if m.group("hi") else None
            if lo is None or lo > 25 or (hi is not None and (hi > 30 or hi < lo)):
                continue
            ctx_after = text[m.end():m.end() + 70]
            ctx_before = text[max(0, m.start() - 40):m.start()]
            if NOT_EXP.search(ctx_after[:18]):
                continue
            if lo > 15 or COMPANY_TALK.search(ctx_before[-25:] + " " + ctx_after[:35]):
                continue   # "for over 25 years we have...", "10 years in business"
            if not (EXP_CONTEXT.search(ctx_after) or EXP_CONTEXT.search(ctx_before) or EXP_RIGHT_AFTER.search(ctx_after)):
                continue
            plus = bool(m.group("plus") or m.group("pre") or m.group("post") or "+" in m.group(0))
            if lo >= 3 or (lo == 2 and (plus or (hi or 2) > 2)):
                v = "reject"
            elif lo == 2 or (hi or 0) >= 2 or (lo == 1 and plus):
                v = "stretch"   # "2 years", "1-2 years", "0-2 years", "1+ years"
            else:
                v = "fresher"   # "0-1 years", "1 year"
            lows.append(lo)
            rank = {"reject": 3, "stretch": 2, "fresher": 1}[v]
            if worst is None or rank > worst[0]:
                worst = (rank, v)
                snippet = re.sub(r"\s+", " ", text[max(0, m.start() - 20):m.end() + 40]).strip()
        if worst and worst[1] == "reject":
            return "reject", min(lows), snippet
        s = SENIOR_DESC_RX.search(text)
        if not s:
            for m in SENIOR_ASK_RX.finditer(text):
                before = text[max(0, m.start() - 60):m.start()]
                if ASKS_CANDIDATE.search(before) and not re.search(r"\b(our|we)\b", before[-25:], re.I):
                    s = m
                    break
        if s:
            return "reject", None, re.sub(r"\s+", " ", text[max(0, s.start() - 20):s.end() + 30]).strip()
        if worst:
            return worst[1], min(lows), snippet
        return "open", None, ""

    # ---- resume match
    def skills(self, text):
        t = text or ""
        job = {k for k, r in self.vocab.items() if r.search(t)}
        have = sorted(k for k in job if k in self.resume)
        learn = sorted(k for k in job if k not in self.resume)
        pct = round(100 * len(have) / len(job)) if job else None
        return have, learn, pct

    def role_type(self, title, desc=""):
        for name, rx in ROLE_TYPES:
            if re.search(rx, title or "", re.I):
                return name
        return "SWE"

    def evaluate(self, job, company):
        """Returns (matched: bool, enriched_job_or_reason)."""
        ok, why = self.prefilter(job, company)
        if not ok:
            return False, why
        grp = self.location_group(job["location"], company["source"]["type"])
        title, desc = job["title"], job.get("desc") or ""
        verdict, yrs, snippet = self.experience(title + ". " + desc)
        level = (job.get("level") or "").lower()
        if verdict == "reject":
            return False, "needs experience: %s" % snippet
        if re.search(r"mid|senior|director|executive|lead", level) and verdict == "open":
            return False, "listed as %s" % level
        if FRESHER_RX.search(title) or FRESHER_RX.search(desc) or re.search(r"entry|intern", level):
            fit = "fresher" if verdict != "stretch" else "stretch"
        elif job.get("desc_missing"):
            fit = "unverified"
        else:
            fit = verdict
        have, learn, pct = self.skills(title + " " + desc)
        tier = company.get("tier", 5)
        salary_pts = {1: 10, 2: 10, 3: 8, 4: 6, 5: 4}.get(tier, 4)
        fit_pts = {"fresher": 10, "open": 8, "stretch": 6, "unverified": 5}[fit]
        match_pts = 5 if pct is None else round(pct / 10)
        prio_bonus = {"North India": 10, "Central/West India": 9, "South India": 8, "Remote": 8}.get(grp, 6)
        score = round(0.30 * salary_pts + 0.30 * fit_pts + 0.30 * match_pts + 0.10 * prio_bonus, 1)
        j = dict(job)
        j.update({
            "tier": tier, "segment": company.get("segment", ""), "location_group": grp,
            "min_years": yrs, "exp_note": snippet[:140], "fit": fit, "role_type": self.role_type(title, desc),
            "skills_matched": have, "skills_to_learn": learn[:8], "match_pct": pct, "priority": score,
            "est_ctc_lpa": job.get("comp") or company.get("fresher_ctc_lpa", ""),
        })
        for k in ("desc", "desc_missing", "level"):
            j.pop(k, None)
        return True, j
