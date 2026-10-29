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
     ["gemini-flash-latest", "gemini-flash-lite-latest"], 7),     # gemini-2.5-flash: 404 since Oct 2026
    ("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
     ["openai/gpt-oss-20b", "openai/gpt-oss-120b"], 22),          # 8k tokens/minute -> ~3 calls/minute
    ("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
     ["openai/gpt-oss-20b", "nvidia/nemotron-3-super-120b-a12b"], 2),
    ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", ["openrouter/free"], 4),
]
SPACING_SCALE = 1.0   # tests set this to 0
QUOTED = re.compile(r'["\u201c\u2018].{4,}?["\u201d\u2019]|"[^"]{4,}"|(?<![A-Za-z])\'[^\']{4,}\'(?![A-Za-z])')

RULES = """You screen job postings for one candidate. Decide if this job is realistic for them to apply to NOW.

Candidate:
{candidate}

Rules:
- "reject" ONLY with explicit evidence in the posting text, and you MUST quote that exact phrase in "reason":
  * it asks for 2+ years, a minimum of 2 years, or 3 or more years of experience (even for one skill), or
  * it clearly describes a senior/lead role (e.g. "lead a team", "mentor engineers", "engineering leader",
    "senior", "staff", "principal", grade codes like AVP), or
  * it requires a degree the candidate does not have (Master's/PhD required, not "preferred"), or
  * it is not a software/tech role (hardware, sales, pre-sales, support, pure business), or it is an internship, or
  * it says a specialised DOMAIN background is "essential", "must" or "required" that the candidate clearly lacks and
    cannot pick up in a few weeks - e.g. optical networking (DWDM, ROADM), RF/wireless PHY, VLSI/ASIC, automotive
    ADAS/AUTOSAR, SAP/Salesforce functional consulting, mainframe/COBOL, telecom protocols (L2/L3, MPLS).
    Ordinary tools, languages and frameworks (Go, Kubernetes, Spark, Selenium...) are NOT domain expertise.
- NEVER reject because the candidate lacks a skill, language or framework - list it in "skills_to_learn" instead.
- NEVER guess. Do not reject because experience is "likely", "typical" or "implied". If nothing explicit
  is stated, the answer is "open".
- "fresher" if it explicitly targets new grads / freshers / entry level / 0-1 years / recent graduates.
- "stretch" if it asks for up to 2 years (e.g. "1-2 years", "0-2 years", "2 years", "1+ years").
- "open" if no experience or seniority is stated.

Also judge "level": who the role is really pitched at, from its responsibilities, even when no years are stated:
- "entry": juniors are welcome - learning, working under guidance, small/medium features, new-grad language.
- "mid": expects someone who already works independently - owns services/systems end to end, leads designs,
  drives roadmaps or other teams, deep expertise in an area, interviews/mentors others, on-call ownership.
- "senior": leads people, strategy, architecture across teams, executive stakeholders.
Quote the phrase that decided the level in "level_quote".

Reply with JSON only:
{{"fit": "fresher|open|stretch|reject", "years_required": number or null,
  "level": "entry|mid|senior", "level_quote": "exact phrase from the posting",
  "based_in": "country where the role is based (e.g. India, Canada) or 'remote-global' or 'unclear'",
  "degree_required": "none|bachelor|master|phd  (only if REQUIRED, not preferred)",
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
        self.timeout = int(profile.get("ai_timeout", 45))
        self.wait_on_limit = int(profile.get("ai_wait_on_limit", 0))   # bot: wait out a per-minute limit
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
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            raise ValueError("non-JSON reply: %r" % raw[:150])

    def ask_json(self, system, user, max_tokens=2000, temperature=0):
        """One chat call with provider/model fallback. Returns (dict, provider_name) or (None, None)."""
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        while self.providers:
            p = self.providers[0]
            wait = p.spacing * SPACING_SCALE - (time.time() - p.last)
            if wait > 0:
                time.sleep(wait)
            p.last = time.time()
            try:
                # Groq's free tier counts max_tokens against an 8k tokens/minute budget: ask for less there
                mt = min(max_tokens, 2500) if p.name == "groq" else max_tokens
                body = {"model": p.models[0], "temperature": temperature, "max_tokens": mt, "messages": msgs}
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
                if e.code == 429 and self.wait_on_limit and getattr(p, "_waited", 0) < 2:
                    # per-minute free limit (e.g. two bot runs at once): wait it out instead of giving up
                    p._waited = getattr(p, "_waited", 0) + 1
                    self.log("ai %s: rate limited, waiting %ds" % (p.name, self.wait_on_limit))
                    time.sleep(self.wait_on_limit)
                    continue
                if e.code == 413 or (e.code == 400 and "too large" in detail.lower()):
                    self._drop("request too large for this provider's free tier")
                    continue
                if e.code in (400, 401, 402, 403, 404, 429):
                    self._drop("HTTP %d%s %s" % (e.code, " free limit reached" if e.code == 429 else "", detail))
                    continue
                if self._fail(p, "HTTP %d %s" % (e.code, detail)):
                    continue
                return None, None
            except Exception as e:
                if "timed out" in str(e) and len(p.models) > 1 and not getattr(p, "_rotated", False):
                    self.log("ai %s: %s timed out, trying %s" % (p.name, p.models[0], p.models[1]))
                    p.models.append(p.models.pop(0))
                    p._rotated = True
                    continue
                if self._fail(p, str(e)[:160]):
                    continue
                return None, None
            p.errors = 0
            return res, p.name
        return None, None

    def judge(self, title, company, location, desc):
        if not self.available():
            return None
        self.used += 1
        res, by = self.ask_json(RULES.format(candidate=self.candidate),
                                "Company: %s\nTitle: %s\nLocation: %s\n\nDescription:\n%s"
                                % (company, title, location, (desc or "")[:6000]))
        if res is None or res.get("fit") not in ("fresher", "open", "stretch", "reject"):
            return None
        if res["fit"] == "reject" and not QUOTED.search(str(res.get("reason") or "")):
            # a reject must quote the posting; an unsupported guess is treated as "open"
            res["fit"], res["reason"] = "doubtful", "AI thinks it is not a fit but quoted nothing: " + str(res.get("reason") or "")[:100]
        res["skills_to_learn"] = [str(x)[:30] for x in (res.get("skills_to_learn") or [])][:5]
        res["reason"] = str(res.get("reason") or "")[:160]
        res["level"] = res.get("level") if res.get("level") in ("entry", "mid", "senior") else ""
        based = str(res.get("based_in") or "").strip().lower()
        if based and based not in ("india", "unclear", "remote-global", "remote", "global") and "india" not in based:
            res["fit"], res["reason"] = "reject", "based in %s" % res["based_in"]
        if str(res.get("degree_required") or "").lower() in ("master", "phd"):
            res["fit"], res["reason"] = "reject", "requires a %s degree" % res["degree_required"]
        res["level_quote"] = str(res.get("level_quote") or "")[:160]
        res["by"] = by
        return res

    def _fail(self, p, msg):
        """Returns True if the provider was dropped (caller should retry with the next one)."""
        p.errors += 1
        self.log("ai %s error: %s" % (p.name, msg))
        if p.errors >= 3:
            self._drop("3 errors in a row")
            return True
        return False
