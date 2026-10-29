"""AI review: uses a fake GitHub Models response; checks reject/keep, caching and graceful failure."""
import io
import json
import os
import sys
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import finder  # noqa: E402
from core import ai  # noqa: E402


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def reply(obj):
    return Resp(json.dumps({"choices": [{"message": {"content": json.dumps(obj)}}]}).encode())


class T(unittest.TestCase):
    def setUp(self):
        os.environ["GEMINI_API_KEY"] = "x"
        for k in ("GROQ_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY"):
            os.environ.pop(k, None)
        ai.SPACING_SCALE = 0
        self.real = ai.urllib.request.urlopen
        self.calls = []

    def tearDown(self):
        ai.urllib.request.urlopen = self.real

    def items(self):
        d = "x" * 400
        return [({"id": "a", "title": "Data Engineer", "company": "B", "fit": "open", "priority": 7,
                  "skills_to_learn": ["Airflow"]}, d),
                ({"id": "b", "title": "Software Engineer", "company": "C", "fit": "open", "priority": 6}, d),
                ({"id": "c", "title": "SDE 1", "company": "D", "fit": "fresher", "priority": 9}, d)]

    def test_reject_and_keep(self):
        answers = {"Data Engineer": {"fit": "reject", "reason": "\"5+ years of experience\" on AWS"},
                   "Software Engineer": {"fit": "fresher", "reason": "new grads", "role_type": "Backend",
                                         "skills_to_learn": ["Go"]},
                   "SDE 1": {"fit": "fresher", "reason": "new grads"}}

        def fake(req, timeout=0):
            body = json.loads(req.data)
            self.calls.append(body)
            title = [l for l in body["messages"][1]["content"].splitlines() if l.startswith("Title:")][0][7:]
            return reply(answers[title])
        ai.urllib.request.urlopen = fake
        cache = {}
        r = ai.AIReviewer({"ai_max_per_run": 10}, log=lambda *a: None)
        kept = finder.ai_pass(self.items(), r, cache)
        self.assertEqual([k["id"] for k in kept], ["b", "c"])
        self.assertEqual(kept[0]["fit"], "fresher")
        self.assertEqual(kept[0]["role_type"], "Backend")
        self.assertEqual(len(self.calls), 3)          # fresher jobs are double-checked too
        # cached: no new calls the second time, same result
        kept = finder.ai_pass(self.items(), r, cache)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual([k["id"] for k in kept], ["b", "c"])

    def test_rate_limit_keeps_rule_result(self):
        def fake(req, timeout=0):
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        ai.urllib.request.urlopen = fake
        r = ai.AIReviewer({}, log=lambda *a: None)
        kept = finder.ai_pass(self.items(), r, {})
        self.assertEqual(len(kept), 3)
        self.assertTrue(r.disabled)

    def test_bad_replies_keep_rule_result(self):
        ai.urllib.request.urlopen = lambda req, timeout=0: Resp(b"")
        r = ai.AIReviewer({"ai_max_per_run": 10}, log=lambda *a: None)
        kept = finder.ai_pass(self.items(), r, {})
        self.assertEqual(len(kept), 3)
        self.assertEqual(r.errors, 3)   # 3 bad replies in a row -> AI stops, rule results kept
        self.assertTrue(r.disabled)

    def test_falls_back_to_next_provider(self):
        os.environ["GROQ_API_KEY"] = "y"

        def fake(req, timeout=0):
            if "googleapis" in req.full_url:
                raise urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
            return reply({"fit": "open", "reason": "nothing stated"})
        ai.urllib.request.urlopen = fake
        r = ai.AIReviewer({}, log=lambda *a: None)
        res = r.judge("SE", "X", "Pune", "x" * 400)
        self.assertEqual(res["by"], "groq")
        self.assertEqual([p.name for p in r.providers], ["groq"])

    def test_busy_model_rotates(self):
        seen = []

        def fake(req, timeout=0):
            m = json.loads(req.data)["model"]
            seen.append(m)
            self.assertIn("FinderJobBot", req.get_header("User-agent"))
            if m == "gemini-flash-latest":
                raise urllib.error.HTTPError("u", 503, "busy", {}, io.BytesIO(b'{"error":"high demand"}'))
            return reply({"fit": "stretch", "reason": "1-2 years"})
        ai.urllib.request.urlopen = fake
        r = ai.AIReviewer({}, log=lambda *a: None)
        self.assertEqual(r.judge("SE", "X", "Pune", "x" * 400)["fit"], "stretch")
        self.assertEqual(seen, ["gemini-flash-latest", "gemini-flash-lite-latest"])

    def test_unquoted_reject_is_kept_as_doubtful(self):
        ai.urllib.request.urlopen = lambda req, timeout=0: reply({"fit": "reject", "reason": "Likely needs 2+ years"})
        r = ai.AIReviewer({}, log=lambda *a: None)
        self.assertEqual(r.judge("SE", "X", "Pune", "x" * 400)["fit"], "doubtful")

    def test_no_key_means_no_calls(self):
        os.environ.pop("GEMINI_API_KEY")
        r = ai.AIReviewer({}, log=lambda *a: None)
        self.assertFalse(r.available())


if __name__ == "__main__":
    unittest.main()
