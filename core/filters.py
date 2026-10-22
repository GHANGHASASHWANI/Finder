"""Decides whether a job fits the profile, and scores it."""
import re


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
        self.skills = c(p["skills"])
        self.prio = [(name, re.compile(rx, re.I)) for name, rx in p["location_priority"]]
        self.intern_ok = c(p.get("intern_keywords_ok", []))
        self.level_ii_ok = set(p.get("level_ii_ok_companies", []))

    # ---- title
    def title_ok(self, title, company="", desc=""):
        t = title or ""
        if not any(r.search(t) for r in self.inc):
            return False, "title not a software role"
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
            # custom career pages rarely show the location in the link; trust the page's India filter
            return "Unknown (check page)" if source_type == "page" else None
        if any(r.search(l) for r in self.india):
            for name, rx in self.prio:
                if name != "Remote" and rx.search(l):
                    return name
            return "Other India"
        if any(r.search(l) for r in self.remote) and not any(r.search(l) for r in self.block):
            return "Remote"
        return None

    # ---- experience
    YEARS = re.compile(
        r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)(?:\s+of)?"
        r"(?:\s+\w+){0,6}?\s+(?:experience|exp\b|industry|professional|work)", re.I)

    def min_years(self, desc):
        if not desc:
            return None
        lows = [int(m.group(1)) for m in self.YEARS.finditer(desc) if int(m.group(1)) <= 20]
        return min(lows) if lows else None

    def experience_ok(self, desc):
        y = self.min_years(desc)
        if y is None:
            return True, None
        return y <= self.p.get("max_years_required", 1), y

    # ---- score
    def skill_hits(self, text):
        return sorted({r.pattern.replace("\\b", "").replace("\\", "") for r in self.skills if r.search(text or "")})

    def evaluate(self, job, company):
        """Returns (matched: bool, enriched_job_or_reason)."""
        ok, why = self.title_ok(job["title"], company["company"], job.get("desc"))
        if not ok:
            return False, why
        grp = self.location_group(job["location"], company["source"]["type"], company.get("india_locations"))
        if not grp:
            return False, "location not India/remote"
        ok, yrs = self.experience_ok(job.get("desc"))
        if not ok:
            return False, "needs %s+ years" % yrs
        hits = self.skill_hits(job["title"] + " " + job.get("desc", ""))
        tier = company.get("tier", 5)
        salary_pts = {1: 10, 2: 10, 3: 8, 4: 6, 5: 4}.get(tier, 4)
        fresher_pts = 10 if re.search(r"new grad|graduate|university|campus|early career|entry|fresher|engineer i\b|"
                                      r"sde[- ]?(i|1)\b|associate", job["title"], re.I) else 6
        if yrs == 0:
            fresher_pts = 10
        match_pts = min(10, 4 + len(hits))
        prio_bonus = {"North India": 10, "Central/West India": 9, "South India": 8, "Remote": 8}.get(grp, 6)
        score = round(0.35 * salary_pts + 0.30 * match_pts + 0.25 * fresher_pts + 0.10 * prio_bonus, 1)
        j = dict(job)
        j.update({
            "tier": tier, "segment": company.get("segment", ""), "location_group": grp,
            "min_years": yrs, "skills_matched": hits, "priority": score,
            "est_ctc_lpa": job.get("comp") or company.get("fresher_ctc_lpa", ""),
        })
        j.pop("desc", None)
        return True, j
