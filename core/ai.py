"""Second opinion from free AI models, with fallbacks across providers.

Order: Gemini -> Groq -> NVIDIA NIM -> OpenRouter. A provider is used only if its key is set as a GitHub secret.
When one hits its free limit (429), rejects the key, or fails 3 times in a row, the next one takes over for
the rest of the run. If none is available, the keyword-rule result is kept - the AI never blocks a run.
All four speak the OpenAI chat-completions format. (GitHub Models was retired by GitHub on 30 July 2026.)
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

# name, base URL, env var with the key, models to try in order, seconds between calls (free-tier pacing)
PROVIDERS = [
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
     ["gemini-flash-latest", "gemini-2.5-flash", "gemini-flash-lite-latest"], 7),
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
     ["openai/gpt-oss-20b", "openai/gpt-oss-120b"], 22),          # 8k tokens/minute -> ~3 calls/minute
    ("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
     ["openai/gpt-oss-20b", "nvidia/nemotron-3-super-120b-a12b"], 2),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", ["openrouter/free"], 4),
]
SPACING_SCALE = 1.0   # tests set this to 0
QUOTED = re.compile(r'["\u201c\u201d].{4,}["\u201c\u201d]')

RULES = """You screen job postings for one candidate. Decide if this job is realistic for them to apply to NOW.

Candidate:
{candidate}

Rules:
- "reject" ONLY with explicit evidence in the posting text, and you MUST quote that exact phrase in "reason":
  * it asks for 2+ years, a minimum of 2 years, or 3 or more years of experience (even for one skill), or
  * it clearly describes a senior/lead role (e.g. "lead a team", "mentor engineers", "engineering leader",
    "senior", "staff", "principal", grade codes like AVP), or
  * it requires a degree the candidate does not have (Master's/PhD required, not "preferred"), or
  * it is not a software/tech role (hardware, sales, pre-sales, support, pure business), or it is an internship.
- NEVER reject because the candidate lacks a skill, language or framework - list it in "skills_to_learn" instead.
- NEVER guess. Do not reject because experience is "likely", "typical" or "implied". If nothing explicit
  is stated, the answer is "open".
- "fresher" if it explicitly targets new grads / freshers / entry level / 0-1 years / recent graduates.
- "stretch" if it asks for up to 2 years (e.g. "1-2 years", "0-2 years", "2 years", "1+ years").
- "open" if no experience or seniority is stated.

Reply with JSON only:
{{"fit": "fresher|open|stretch|reject", "years_required": number or null,
  "reason": "max 20 words; for reject, quote the exact phrase from the posting in double quotes",
  "role_type": "SWE|Backend|Frontend|Full-stack|Mobile|AI/ML|Data|DevOps/SRE/Cloud|QA/Test|Quant|Security|Other",
  "skills_to_learn": ["up to 5 technologies the job needs that the candidate lacks"]}}"""


class Provider:
    def __init__(self, name, base, key, models, spacing):
        self.name, self.base, self.key, self.models, self.spacing = name, base, key, list(models), spacing
        self.last = 0
        self.errors = 0


class AIReviewer:
    def __init__(self, profile, log=print):
        self.log = log
        self.budget = int(profile.get("ai_max_per_run", 30))
        self.candidate = profile.get("candidate_summary", "")
        self.used = 0
        order = profile.get("ai_providers") or [p[0] for p in PROVIDERS]
        table = {p[0]: p for p in PROVIDERS}
        self.providers = []
        for name in order:
            if name in table and os.environ.get(table[name][2], "").strip():
                n, base, env, models, spacing = table[name]
                self.providers.append(Provider(n, base, os.environ[env].strip(), models, spacing))
        self.disabled = not self.providers or not profile.get("ai_review", True)
        if not self.providers and profile.get("ai_review", True):
            log("ai review off: no AI key set (GEMINI_API_KEY / GROQ_API_KEY / NVIDIA_API_KEY / OPENROUTER_API_KEY)")
        elif self.providers:
            log("ai providers: %s" % " -> ".join(p.name for p in self.providers))

    @property
    def errors(self):
        return sum(p.errors for p in self.providers) + getattr(self, "_dropped_errors", 0)

    def available(self):
        return not self.disabled and bool(self.providers) and self.used < self.budget

    def _drop(self, why):
        p = self.providers.pop(0)
        self._dropped_errors = getattr(self, "_dropped_errors", 0) + p.errors
        if not self.providers:
            self.disabled = True
        nxt = self.providers[0].name if self.providers else "none left, keeping rule results"
        self.log("ai %s stopped for this run (%s) -> %s" % (p.name, why, nxt))

    def _call(self, p, body):
        req = urllib.request.Request(p.base + "/chat/completions", data=json.dumps(body).encode(), headers={
            "Authorization": "Bearer " + p.key, "Content-Type": "application/json",
            # Groq sits behind Cloudflare, which blocks Python's default user agent (error 1010)
            "User-Agent": "Mozilla/5.0 (compatible; FinderJobBot/1.0; +https://github.com/GHANGHASASHWANI/Finder)"})
        with urllib.request.urlopen(req, timeout=90) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise ValueError("non-JSON reply: %r" % raw[:150])

    def judge(self, title, company, location, desc):
        if not self.available():
            return None
        self.used += 1
        msgs = [
            {"role": "system", "content": RULES.format(candidate=self.candidate)},
            {"role": "user", "content": "Company: %s\nTitle: %s\nLocation: %s\n\nDescription:\n%s"
                                        % (company, title, location, (desc or "")[:6000])},
        ]
        while self.providers:
            p = self.providers[0]
            wait = p.spacing * SPACING_SCALE - (time.time() - p.last)
            if wait > 0:
                time.sleep(wait)
            p.last = time.time()
            try:
                body = {"model": p.models[0], "temperature": 0, "max_tokens": 2000, "messages": msgs}
                if "gpt-oss" in p.models[0]:
                    body["reasoning_effort"] = "low"     # reasoning models: keep thinking short so the answer fits
                out = self._call(p, body)
                text = out["choices"][0]["message"].get("content") or ""
                m = re.search(r"\{.*\}", text, re.S)
                if not m:
                    raise ValueError("no JSON in answer: %r" % text[:120])
                res = json.loads(m.group(0))
            except urllib.error.HTTPError as e:
                try:
                    detail = e.read().decode("utf-8", "replace")[:160]
                except Exception:
                    detail = ""
                if e.code == 404 and len(p.models) > 1:      # model retired -> next model, same provider
                    self.log("ai %s: model %s not available, trying %s" % (p.name, p.models[0], p.models[1]))
                    p.models.pop(0)
                    continue
                if e.code in (500, 502, 503, 504) and len(p.models) > 1 and not getattr(p, "_rotated", False):
                    # model overloaded ("high demand") -> rotate to the provider's next model once
                    self.log("ai %s: %s busy (HTTP %d), trying %s" % (p.name, p.models[0], e.code, p.models[1]))
                    p.models.append(p.models.pop(0))
                    p._rotated = True
                    continue
                if e.code in (400, 401, 402, 403, 404, 429):
                    self._drop("HTTP %d%s %s" % (e.code, " free limit reached" if e.code == 429 else "", detail))
                    continue
                if self._fail(p, "HTTP %d %s" % (e.code, detail)):
                    continue
                return None
            except Exception as e:
                if self._fail(p, str(e)[:160]):
                    continue
                return None
            p.errors = 0
            if res.get("fit") not in ("fresher", "open", "stretch", "reject"):
                return None
            if res["fit"] == "reject" and not QUOTED.search(str(res.get("reason") or "")):
                # a reject must quote the posting; an unsupported guess is treated as "open"
                res["fit"], res["reason"] = "open", "AI rejected without quoting the posting - kept"
            res["skills_to_learn"] = [str(x)[:30] for x in (res.get("skills_to_learn") or [])][:5]
            res["reason"] = str(res.get("reason") or "")[:160]
            res["by"] = p.name
            return res
        return None

    def _fail(self, p, msg):
        """Returns True if the provider was dropped (caller should retry with the next one)."""
        p.errors += 1
        self.log("ai %s error: %s" % (p.name, msg))
        if p.errors >= 3:
            self._drop("3 errors in a row")
            return True
        return False
