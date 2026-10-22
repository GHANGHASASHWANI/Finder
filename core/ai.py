"""Second opinion from an AI model (GitHub Models, free with the Actions GITHUB_TOKEN).

Only used for jobs the keyword rules could not settle. If the token is missing, the daily limit is hit,
or the model answers badly, the rule-based result is kept - the AI never blocks a run.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

ENDPOINT = "https://models.github.ai/inference/chat/completions"

RULES = """You screen job postings for one candidate. Decide if this job is realistic for them to apply to NOW.

Candidate:
{candidate}

Rules:
- "reject" if the posting requires 2+ years, a minimum of 2 years, or 3 or more years of experience anywhere
  (even for one skill), or is clearly mid-level/senior (leads a team, mentors juniors, "experienced hire",
  owns architecture, senior-level grade codes), or is not a software/tech role (hardware, sales, support,
  pure business analyst).
- "fresher" if it explicitly targets new grads / freshers / entry level / 0-1 years.
- "stretch" if it asks for up to 2 years (e.g. "1-2 years", "0-2 years", "2 years", "1+ years").
- "open" if no experience or seniority is stated and nothing suggests seniority.
- Internship roles: "reject".

Reply with JSON only:
{{"fit": "fresher|open|stretch|reject", "years_required": number or null,
  "reason": "max 15 words, quote the deciding phrase if any",
  "role_type": "SWE|Backend|Frontend|Full-stack|Mobile|AI/ML|Data|DevOps/SRE/Cloud|QA/Test|Quant|Security|Other",
  "skills_to_learn": ["up to 5 technologies the job needs that the candidate lacks"]}}"""


class AIReviewer:
    def __init__(self, profile, log=print):
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_MODELS_TOKEN")
        self.model = profile.get("ai_model", "openai/gpt-4o-mini")
        self.budget = int(profile.get("ai_max_per_run", 60))
        self.candidate = profile.get("candidate_summary", "")
        self.log = log
        self.used = 0
        self.disabled = not self.token or not profile.get("ai_review", True)

    def available(self):
        return not self.disabled and self.used < self.budget

    def judge(self, title, company, location, desc):
        if not self.available():
            return None
        # free tier allows 15 requests/minute: keep at most one call every 5 seconds
        wait = 5 - (time.time() - getattr(self, "_last", 0))
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        self.used += 1
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 250,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": RULES.format(candidate=self.candidate)},
                {"role": "user", "content": "Company: %s\nTitle: %s\nLocation: %s\n\nDescription:\n%s"
                                            % (company, title, location, (desc or "")[:9000])},
            ],
        }
        req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                out = json.loads(r.read().decode())
            text = out["choices"][0]["message"]["content"]
            res = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 429):
                self.disabled = True
                self.log("ai review stopped for this run (HTTP %d%s)" % (
                    e.code, ", daily free limit reached" if e.code == 429 else ""))
            return None
        except Exception as e:
            self.log("ai review error: %s" % str(e)[:120])
            return None
        if res.get("fit") not in ("fresher", "open", "stretch", "reject"):
            return None
        res["skills_to_learn"] = [str(s)[:30] for s in (res.get("skills_to_learn") or [])][:5]
        res["reason"] = str(res.get("reason") or "")[:160]
        return res
